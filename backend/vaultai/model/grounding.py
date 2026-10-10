"""The gate between untrusted model output and anything VaultAI keeps.

Output must parse into a closed schema (extra keys such as "status" or
"approve" are rejected outright). Every kept value must cite a line the model
was shown and quote text that is really on that line, and must read as its
declared type. Model values never replace a more confident rules value, and
their confidence is capped. There is no code path from model output to a
status change, an approval, or a file operation.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from vaultai.analysis import ObligationRec
from vaultai.domain import DocType, Evidence, FieldOrigin, IssueCode, ObligationKind, ValueType
from vaultai.extraction import Found, evidence
from vaultai.extraction.normalize import find_dates, find_money, money_str
from vaultai.model.base import AnalyzeTask, ClassifyTask, ExtractTask
from vaultai.parsing.layout import Doc
from vaultai.validation import Issue

MODEL_CONFIDENCE_CAP = 0.85
DEFAULT_MODEL_CONFIDENCE = 0.7


class _Out(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ClassifyOut(_Out):
    doc_type: str | None = Field(default=None, max_length=40)
    confidence: float = Field(default=0.0, ge=0, le=1)


class FieldOut(_Out):
    key: str = Field(max_length=64)
    value: str | None = Field(default=None, max_length=500)
    line_id: str | None = Field(default=None, max_length=24)
    quote: str | None = Field(default=None, max_length=500)
    confidence: float | None = Field(default=None, ge=0, le=1)


class ExtractOut(_Out):
    fields: list[FieldOut] = Field(default_factory=list, max_length=64)


class ClaimOut(_Out):
    text: str = Field(min_length=1, max_length=400)
    line_ids: list[Annotated[str, Field(max_length=24)]] = Field(default_factory=list, max_length=8)


class ObligationOut(_Out):
    kind: ObligationKind
    party: str | None = Field(default=None, max_length=120)
    description: str = Field(min_length=1, max_length=400)
    due_date: str | None = Field(default=None, max_length=10)
    line_id: str = Field(max_length=24)
    quote: str = Field(max_length=500)


class AnalyzeOut(_Out):
    claims: list[ClaimOut] = Field(default_factory=list, max_length=20)
    obligations: list[ObligationOut] = Field(default_factory=list, max_length=20)


class TranscribeOut(_Out):
    lines: list[Annotated[str, Field(max_length=500)]] = Field(default_factory=list, max_length=300)


REJECTED = "The local model's answer did not match the required format, so none of it was used."


def _parse(model: type[_Out], raw: object) -> tuple[_Out | None, list[Issue]]:
    try:
        return model.model_validate(raw), []
    except ValidationError:
        return None, [Issue(IssueCode.MODEL_OUTPUT_REJECTED, REJECTED)]


def _norm(text: str) -> str:
    return " ".join(text.casefold().split())


def _quote_on_line(doc: Doc, allowed: set[str], line_id: str | None, quote: str | None) -> bool:
    if not line_id or not quote or line_id not in allowed or line_id not in doc.by_id:
        return False
    return _norm(quote) in _norm(doc.by_id[line_id].text)


def _ungrounded(label: str, key: str | None = None) -> Issue:
    return Issue(IssueCode.UNGROUNDED_VALUE,
                 f"The model proposed a {label} that is not supported by the document text; it was discarded.", key)


# ---------------------------------------------------------------- classify


def ground_classify(raw: object, task: ClassifyTask) -> tuple[DocType | None, float, list[Issue]]:
    out, issues = _parse(ClassifyOut, raw)
    if out is None or out.doc_type is None:
        return None, 0.0, issues
    try:
        doc_type = DocType(out.doc_type.strip().lower())
    except ValueError:
        return None, 0.0, [Issue(IssueCode.MODEL_OUTPUT_REJECTED, REJECTED)]
    if doc_type not in task.labels:
        return None, 0.0, [Issue(IssueCode.MODEL_OUTPUT_REJECTED, REJECTED)]
    return doc_type, min(out.confidence, MODEL_CONFIDENCE_CAP), []


# ---------------------------------------------------------------- extract


def _typed(value_type: ValueType, value: str, quote: str, options: tuple[str, ...], date_order: str
           ) -> tuple[str, str, str | None] | None:
    """(canonical value, printed text, currency) when `value` reads as its type and agrees with the quote."""
    if value_type is ValueType.MONEY:
        stated = find_money(value)
        if not stated:
            return None
        printed = next((m for m in find_money(quote) if m.amount == stated[0].amount), None)
        return (money_str(printed.amount), printed.raw, printed.currency or stated[0].currency) if printed else None
    if value_type is ValueType.DATE:
        try:
            wanted = date.fromisoformat(value.strip())
        except ValueError:
            hits = find_dates(value, date_order)
            if not hits or hits[0].value is None:
                return None
            wanted = hits[0].value
        printed = next((h for h in find_dates(quote, date_order) if h.value == wanted), None)
        return (wanted.isoformat(), printed.raw, None) if printed else None
    if value_type is ValueType.BOOL:
        v = value.strip().lower()
        return (v, quote, None) if v in {"true", "false"} else None
    if value_type is ValueType.ENUM:
        match = next((o for o in options if o.lower() == value.strip().lower()), None)
        return (match, quote, None) if match else None
    if _norm(value) and _norm(value) in _norm(quote):
        return (" ".join(value.split()), " ".join(value.split()), None)
    return None


def ground_extract(raw: object, task: ExtractTask, doc: Doc, existing: Mapping[str, Found], date_order: str,
                   model_name: str) -> tuple[list[Found], list[Issue]]:
    out, issues = _parse(ExtractOut, raw)
    if out is None:
        return [], issues
    specs = {f.key: f for f in task.fields}
    allowed = {ln.id for ln in task.lines}
    kept: list[Found] = []
    for f in out.fields:
        if f.value is None:
            continue
        spec = specs.get(f.key)
        if spec is None or not _quote_on_line(doc, allowed, f.line_id, f.quote):
            issues.append(_ungrounded("value", f.key if spec else None))
            continue
        typed = _typed(spec.value_type, f.value, f.quote or "", spec.options, date_order)
        if typed is None:
            issues.append(_ungrounded(spec.label.lower(), spec.key))
            continue
        confidence = min(f.confidence if f.confidence is not None else DEFAULT_MODEL_CONFIDENCE, MODEL_CONFIDENCE_CAP)
        current = existing.get(spec.key)
        if current is not None and current.value is not None and current.confidence >= confidence:
            continue
        value, printed, currency = typed
        line = doc.by_id[f.line_id or ""]
        kept.append(Found(spec.key, value, printed, confidence, evidence(line), currency, FieldOrigin.MODEL, model_name))
    return kept, issues


# ---------------------------------------------------------------- analyze


@dataclass(frozen=True)
class Claim:
    text: str
    evidence: tuple[Evidence, ...]


CITATION = re.compile(r"\[(p\d+\.l\d+)\]")


def fact_line_ids(task: AnalyzeTask) -> set[str]:
    return {m for key in task.known_facts for m in CITATION.findall(key)}


def ground_analyze(raw: object, task: AnalyzeTask, doc: Doc, date_order: str
                   ) -> tuple[list[Claim], list[ObligationRec], list[Issue]]:
    out, issues = _parse(AnalyzeOut, raw)
    if out is None:
        return [], [], issues
    allowed = {ln.id for ln in task.lines} | fact_line_ids(task)
    claims = []
    for c in out.claims:
        cited = [evidence(doc.by_id[i]) for i in dict.fromkeys(c.line_ids) if i in allowed and i in doc.by_id]
        if cited:
            claims.append(Claim(" ".join(c.text.split()), tuple(cited)))
    obligations = []
    for o in out.obligations:
        if not _quote_on_line(doc, allowed, o.line_id, o.quote):
            issues.append(_ungrounded("obligation"))
            continue
        due = None
        if o.due_date:
            typed = _typed(ValueType.DATE, o.due_date, o.quote, (), date_order)
            if typed is None:
                issues.append(_ungrounded("deadline"))
                continue
            due = date.fromisoformat(typed[0])
        obligations.append(ObligationRec(o.kind, o.party, " ".join(o.description.split()), due,
                                         evidence(doc.by_id[o.line_id]), FieldOrigin.MODEL))
    return claims, obligations, issues


def ground_transcribe(raw: object) -> tuple[list[str], list[Issue]]:
    out, issues = _parse(TranscribeOut, raw)
    if out is None:
        return [], issues
    return [" ".join(t.split()) for t in out.lines if t.strip()], []
