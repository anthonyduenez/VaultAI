"""The gate between untrusted model output and anything VaultAI keeps.

Output must parse into a closed schema (vaultai.model.output; extra keys such as
"status" or "approve" are rejected outright). Every kept value must cite a line
the model was shown, quote that line exactly (Unicode-normalized, whitespace
collapsed, case kept), and read as its declared type inside the quote: amounts
and dates are re-parsed from the printed text, and text values are stored as
printed, never as the model spelled them. Summary claims must cite a line,
state only amounts and dates printed in what they cite, and give no advice;
other claims are dropped (they change no record, so they raise no issue).
Values are then scored by
vaultai.model.confidence rather than trusted at the model's word, and never
replace a more confident rules value. There is no code path from model output
to a status change, an approval, or a file operation.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date

from vaultai.analysis import ObligationRec
from vaultai.domain import DocType, Evidence, FieldOrigin, IssueCode, ValueType
from vaultai.extraction import Found, evidence
from vaultai.extraction.normalize import CURRENCY_CODES, find_dates, find_money, money_str
from vaultai.model import confidence, output
from vaultai.model.diagnostics import FailureReason, GroundingReason, failure_reason
from vaultai.model.base import AnalyzeTask, ClassifyTask, ExtractTask, TranscribeTask
from vaultai.parsing.layout import Doc
from vaultai.validation import Issue

MODEL_CONFIDENCE_CAP = confidence.CAP
EARLIEST_YEAR, LATEST_YEAR = 1900, 2100

REJECTED = "The local model's answer did not match the required format, so none of it was used."
_LINE_ID = re.compile(r"^\s*\[?\s*(p\d{1,4}\.l\d{1,5})(?![\w.])")
_ADVICE = re.compile(
    r"\b(unenforceable|invalid|illegal|unlawful|you should|we recommend|i recommend|recommend(?:ed|s)? that|"
    r"legal advice|consult (?:a|an|your) (?:lawyer|attorney|counsel))\b", re.I)
_CONTENT_WORD = re.compile(r"[a-z0-9][a-z0-9'-]{3,}")


def rejected(reason: FailureReason | str) -> Issue:
    return Issue(IssueCode.MODEL_OUTPUT_REJECTED, REJECTED, reason=str(reason))


def canon(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).split())


def line_id(raw: str | None) -> str | None:
    """The bare citation handle, accepting the bracketed or annotated forms shown in the prompt ("[p1.l4 ocr 60%]")."""
    m = _LINE_ID.match(raw or "")
    return m.group(1) if m else None


def quote_on_line(doc: Doc, allowed: set[str], cited: str | None, quote: str | None) -> bool:
    if not cited or not quote or not canon(quote) or cited not in allowed or cited not in doc.by_id:
        return False
    return canon(quote) in canon(doc.by_id[cited].text)


def printed(value: str, quote: str) -> str | None:
    """`value` as it is printed inside `quote`, matched ignoring case and spacing, or None."""
    words = canon(value).split()
    if not words:
        return None
    m = re.search(r"\s+".join(map(re.escape, words)), canon(quote), re.I)
    return m.group() if m else None


def _ungrounded(reason: GroundingReason, label: str, key: str | None = None,
                why: str = "is not supported by the document text") -> Issue:
    return Issue(IssueCode.UNGROUNDED_VALUE, f"The model proposed a {label} that {why}; it was discarded.", key,
                 reason=reason.value)


def _cited(doc: Doc, allowed: set[str], cited: str | None) -> bool:
    return bool(cited) and cited in allowed and cited in doc.by_id


def ground_classify(raw: object, task: ClassifyTask) -> tuple[DocType | None, float, list[Issue]]:
    out = output.parse(task, raw)
    if out is None:
        return None, 0.0, [rejected(failure_reason(raw))]
    if out.doc_type is None:
        return None, 0.0, []
    try:
        doc_type = DocType(out.doc_type.strip().lower())
    except ValueError:
        return None, 0.0, [rejected(FailureReason.SCHEMA_INVALID)]
    if doc_type not in task.labels:
        return None, 0.0, [rejected(FailureReason.SCHEMA_INVALID)]
    return doc_type, min(out.confidence, MODEL_CONFIDENCE_CAP), []


def _date(text: str, date_order: str) -> date | None:
    try:
        wanted = date.fromisoformat(text.strip())
    except ValueError:
        hits = find_dates(text, date_order)
        if len(hits) != 1 or hits[0].value is None:
            return None
        wanted = hits[0].value
    return wanted if EARLIEST_YEAR <= wanted.year <= LATEST_YEAR else None


def typed(value_type: ValueType, value: str, quote: str, options: tuple[str, ...], date_order: str
          ) -> tuple[str, str, str | None] | None:
    """(stored value, printed text, currency) when `value` is a valid `value_type` printed in `quote`, else None."""
    if value_type is ValueType.MONEY:
        stated = find_money(value)
        if len(stated) != 1:
            return None
        hit = next((m for m in find_money(quote) if m.amount == stated[0].amount), None)
        if hit is None or (hit.currency and stated[0].currency and hit.currency != stated[0].currency):
            return None
        currency = hit.currency or stated[0].currency
        if currency is not None and currency not in CURRENCY_CODES:
            return None
        return money_str(hit.amount), hit.raw, currency
    if value_type is ValueType.DATE:
        wanted = _date(value, date_order)
        hit = next((h for h in find_dates(quote, date_order) if wanted and h.value == wanted), None)
        return (wanted.isoformat(), hit.raw, None) if hit else None
    if value_type is ValueType.BOOL:
        v = value.strip().lower()
        return (v, quote, None) if v in {"true", "false"} else None
    if value_type is ValueType.ENUM:
        match = next((o for o in options if o.lower() == value.strip().lower()), None)
        return (match, quote, None) if match else None
    text = printed(value, quote)
    if text is None:
        return None
    if value_type is ValueType.IDENTIFIER:
        text = text.strip(" .,;:")
    return (text, text, None) if re.search(r"[A-Za-z0-9]", text) else None


def ground_extract(raw: object, task: ExtractTask, doc: Doc, existing: Mapping[str, Found], date_order: str,
                   model_name: str) -> tuple[list[Found], list[Issue]]:
    out = output.parse(task, raw)
    if out is None:
        return [], [rejected(failure_reason(raw))]
    specs = {f.key: f for f in task.fields}
    allowed = {ln.id for ln in task.lines}
    issues: list[Issue] = []
    candidates: dict[str, list[tuple[output.FieldOut, str, tuple[str, str, str | None]]]] = {}
    for f in out.fields:
        if f.value is None:
            continue
        spec = specs.get(f.key)
        cited = line_id(f.line_id)
        if spec is None:
            issues.append(_ungrounded(GroundingReason.UNKNOWN_FIELD, "value"))
            continue
        if not _cited(doc, allowed, cited):
            issues.append(_ungrounded(GroundingReason.INVALID_CITATION, "value", spec.key))
            continue
        if not quote_on_line(doc, allowed, cited, f.quote):
            issues.append(_ungrounded(GroundingReason.UNSUPPORTED_BY_DOCUMENT, "value", spec.key))
            continue
        value = typed(spec.value_type, f.value, f.quote or "", spec.options, date_order)
        if value is None:
            issues.append(_ungrounded(GroundingReason.UNSUPPORTED_BY_DOCUMENT, spec.label.lower(), spec.key))
            continue
        candidates.setdefault(spec.key, []).append((f, cited, value))
    kept: list[Found] = []
    for key, found in candidates.items():
        spec = specs[key]
        if len({v[0] for _, _, v in found}) > 1:
            issues.append(_ungrounded(GroundingReason.CONFLICTING_EVIDENCE, spec.label.lower(), key,
                                      "had more than one conflicting answer"))
            continue
        f, cited, (value, raw_text, currency) = found[0]
        line = doc.by_id[cited]
        score, _ = confidence.calibrate(spec, value, f.quote or "", line, doc, existing.get(key), f.confidence)
        if score < confidence.REJECT_BELOW:
            issues.append(_ungrounded(GroundingReason.WEAK_EVIDENCE, spec.label.lower(), key, "rests on weak evidence"))
            continue
        current = existing.get(key)
        if current is not None and current.value is not None and current.confidence >= score:
            continue
        kept.append(Found(key, value, raw_text, score, evidence(line), currency, FieldOrigin.MODEL, model_name))
    return kept, issues


@dataclass(frozen=True)
class Claim:
    text: str
    evidence: tuple[Evidence, ...]


CITATION = re.compile(r"\[(p\d+\.l\d+)\]")


def fact_line_ids(task: AnalyzeTask) -> set[str]:
    return {m for key in task.known_facts for m in CITATION.findall(key)}


def _supported(claim: str, sources: str, date_order: str) -> bool:
    """Every amount and date the claim states is also printed in its sources."""
    amounts = {m.amount for m in find_money(sources)}
    days = {h.value for h in find_dates(sources, date_order) if h.value}
    return (all(m.amount in amounts for m in find_money(claim, require_marker=True))
            and all(h.value in days for h in find_dates(claim, date_order) if h.value))


def _faithful(description: str, source: str) -> bool:
    words = set(_CONTENT_WORD.findall(description.casefold()))
    return bool(words) and len(words & set(_CONTENT_WORD.findall(source.casefold()))) >= 0.6 * len(words)


def ground_analyze(raw: object, task: AnalyzeTask, doc: Doc, date_order: str
                   ) -> tuple[list[Claim], list[ObligationRec], list[Issue]]:
    out = output.parse(task, raw)
    if out is None:
        return [], [], [rejected(failure_reason(raw))]
    allowed = {ln.id for ln in task.lines} | fact_line_ids(task)
    facts = " ".join(task.known_facts.values())
    known_text = canon(" ".join(ln.text for ln in task.lines) + " " + facts).casefold()
    issues: list[Issue] = []
    claims = []
    for c in out.claims:
        cited = [doc.by_id[i] for i in dict.fromkeys(filter(None, map(line_id, c.line_ids))) if i in allowed and i in doc.by_id]
        if not cited:
            continue
        sources = " ".join(ln.text for ln in cited) + " " + facts
        if _ADVICE.search(c.text) or not _supported(c.text, sources, date_order):
            continue
        claims.append(Claim(" ".join(c.text.split()), tuple(evidence(ln) for ln in cited)))
    obligations = []
    for o in out.obligations:
        cited = line_id(o.line_id)
        if not _cited(doc, allowed, cited):
            issues.append(_ungrounded(GroundingReason.INVALID_CITATION, "obligation"))
            continue
        if not quote_on_line(doc, allowed, cited, o.quote):
            issues.append(_ungrounded(GroundingReason.OBLIGATION_NOT_GROUNDED, "obligation"))
            continue
        line = doc.by_id[cited]
        due = None
        if o.due_date:
            value = typed(ValueType.DATE, o.due_date, o.quote, (), date_order)
            if value is None:
                issues.append(_ungrounded(GroundingReason.DEADLINE_NOT_GROUNDED, "deadline"))
                continue
            due = date.fromisoformat(value[0])
        description = " ".join(o.description.split())
        if not _faithful(description, line.text):
            description = canon(o.quote)
        party = " ".join(o.party.split()) if o.party else None
        if party and canon(party).casefold() not in known_text:
            party = None
        obligations.append(ObligationRec(o.kind, party, description, due, evidence(line), FieldOrigin.MODEL))
    return claims, obligations, issues


def ground_transcribe(raw: object) -> tuple[list[str], list[Issue]]:
    out = output.parse(TranscribeTask(b""), raw)
    if out is None:
        return [], [rejected(failure_reason(raw))]
    return [" ".join(t.split()) for t in out.lines if t.strip()], []
