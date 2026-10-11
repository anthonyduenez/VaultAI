"""The document row's summary columns, computed from its human-effective fields, obligations, clauses, and issues."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

from vaultai.domain import DOC_TYPE_LABELS, ClauseRisk, DocType, IssueCode, RiskLevel, Severity
from vaultai.schemas import SCHEMAS
from vaultai.validation import INJECTION_CODES
from vaultai.validation.rules import FieldView


@dataclass(frozen=True)
class Projection:
    title: str | None
    counterparty: str | None
    doc_date: str | None
    total_amount: str | None
    currency: str | None
    primary_id: str | None
    next_deadline: str | None
    confidence: float | None
    risk_level: RiskLevel


def _value(fields: Mapping[str, FieldView], key: str | None) -> str | None:
    f = fields.get(key or "")
    return f.value if f else None


def risk_level(clause_risks: Iterable[ClauseRisk], issues: Iterable[tuple[IssueCode, Severity]]) -> RiskLevel:
    risks, issues = set(clause_risks), list(issues)
    if ClauseRisk.PROFESSIONAL_REVIEW in risks or any(code in INJECTION_CODES for code, _ in issues):
        return RiskLevel.HIGH
    if ClauseRisk.ATTENTION in risks or any(sev is Severity.ERROR for _, sev in issues):
        return RiskLevel.MEDIUM
    if any(sev is Severity.WARNING for _, sev in issues):
        return RiskLevel.LOW
    return RiskLevel.NONE


def project(doc_type: DocType, fields: Mapping[str, FieldView], open_deadlines: Iterable[date],
            clause_risks: Iterable[ClauseRisk], issues: Iterable[tuple[IssueCode, Severity]]) -> Projection:
    schema = SCHEMAS[doc_type]
    primary_id = _value(fields, schema.primary_id)
    counterparty = _value(fields, schema.counterparty)
    if schema.counterparty == "parties" and counterparty:
        counterparty = counterparty.split(";")[0].strip()
    label = DOC_TYPE_LABELS[doc_type]
    title = _value(fields, schema.title) or None
    if title is None and primary_id:
        title = f"{label} {primary_id}"
    elif title is None and counterparty:
        title = f"{label} from {counterparty}"

    total = _value(fields, schema.total)
    try:
        total = str(Decimal(total)) if total is not None else None
    except InvalidOperation:
        total = None
    currency = _value(fields, "currency") or (fields[schema.total].currency if schema.total in fields else None)

    required = [f for f in fields.values() if f.required]
    confidence = round(sum(f.confidence if f.value is not None else 0.0 for f in required) / len(required), 4) if required else None
    deadlines = sorted(open_deadlines)
    return Projection(
        title=title,
        counterparty=counterparty,
        doc_date=_value(fields, schema.doc_date),
        total_amount=total,
        currency=currency if total is not None else None,
        primary_id=primary_id,
        next_deadline=deadlines[0].isoformat() if deadlines else None,
        confidence=confidence,
        risk_level=risk_level(clause_risks, issues),
    )
