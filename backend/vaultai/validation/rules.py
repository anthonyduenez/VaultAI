"""Deterministic validation: a registry of pure rules over a typed snapshot of one document.

The same snapshot is built after processing and after every human edit, so a
corrected value clears (or raises) its issues the same way both times.
"""

from __future__ import annotations

import re
import statistics
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

from vaultai.analysis import ClauseRec
from vaultai.analysis.contracts import clause_findings
from vaultai.domain import LEGAL_DOC_TYPES, DocType, Evidence, FieldOrigin, IssueCode, LineFlag, ValueType
from vaultai.extraction import Item, evidence
from vaultai.extraction.normalize import aba_routing_valid, same_identifier, written_amount
from vaultai.parsing.layout import Doc
from vaultai.schemas import KV_PREFIX, SCHEMAS
from vaultai.validation import Issue
from vaultai.validation.injection import UNSAFE_VALUE
from vaultai.validation.injection import match as injection_match

LOW_OCR = 0.6
TOLERANCE = Decimal("0.01")
UNUSUAL_MULTIPLE = 3
D = DocType


@dataclass(frozen=True)
class FieldView:
    key: str
    label: str
    value_type: ValueType
    required: bool
    value: str | None
    raw_text: str | None
    currency: str | None
    confidence: float
    origin: FieldOrigin
    evidence: Evidence | None


@dataclass(frozen=True)
class Peer:
    doc_id: str
    doc_type: DocType | None
    counterparty: str | None
    primary_id: str | None
    doc_date: str | None
    total: Decimal | None


@dataclass(frozen=True)
class Snapshot:
    doc_type: DocType
    fields: Mapping[str, FieldView]
    items: tuple[Item, ...]
    doc: Doc
    ocr_confidence: Mapping[int, float | None]
    clauses: tuple[ClauseRec, ...]
    peers: tuple[Peer, ...]
    history: tuple[Decimal, ...]
    review_threshold: float
    unusual_amount: Decimal

    def value(self, key: str) -> str | None:
        f = self.fields.get(key)
        return f.value if f else None

    def money(self, key: str) -> Decimal | None:
        v = self.value(key)
        if v is None or self.fields[key].value_type is not ValueType.MONEY:
            return None
        try:
            return Decimal(v)
        except InvalidOperation:
            return None

    def day(self, key: str) -> date | None:
        v = self.value(key)
        try:
            return date.fromisoformat(v) if v else None
        except ValueError:
            return None

    def ev(self, key: str) -> Evidence | None:
        f = self.fields.get(key)
        return f.evidence if f else None


Rule = Callable[[Snapshot], Iterable[Issue]]


def _fmt(v: Decimal | None) -> str:
    return f"{v:,.2f}" if v is not None else "?"


def unreadable(s: Snapshot) -> Iterable[Issue]:
    if not s.doc.visible and not any(ln.flag for ln in s.doc.lines):
        yield Issue(IssueCode.UNREADABLE, "No readable text was found on any page.")


def low_ocr(s: Snapshot) -> Iterable[Issue]:
    for page_no, conf in sorted(s.ocr_confidence.items()):
        if conf is not None and conf < LOW_OCR:
            yield Issue(IssueCode.LOW_OCR_CONFIDENCE, f"Page {page_no} was read with low OCR confidence ({conf:.0%}).",
                        evidence=Evidence(page=page_no))


def missing_required(s: Snapshot) -> Iterable[Issue]:
    for f in s.fields.values():
        if f.required and f.value is None and f.raw_text is None:
            yield Issue(IssueCode.MISSING_REQUIRED, f"{f.label} was not found.", f.key)


def low_confidence(s: Snapshot) -> Iterable[Issue]:
    for f in s.fields.values():
        if f.required and f.value is not None and f.origin is not FieldOrigin.HUMAN and f.confidence < s.review_threshold:
            yield Issue(IssueCode.LOW_EXTRACTION_CONFIDENCE,
                        f"{f.label} was read with low confidence ({f.confidence:.0%}).", f.key, f.evidence)


def invalid_dates(s: Snapshot) -> Iterable[Issue]:
    for f in s.fields.values():
        if f.value_type is ValueType.DATE and f.raw_text and s.day(f.key) is None:
            yield Issue(IssueCode.INVALID_DATE, f"{f.label} \"{f.raw_text}\" is not a real calendar date.", f.key, f.evidence)


DATE_PAIRS: dict[DocType, tuple[str, str]] = {
    D.INVOICE: ("invoice_date", "due_date"),
    D.CONTRACT: ("effective_date", "expiration_date"),
    D.STATEMENT_OF_WORK: ("effective_date", "end_date"),
    D.PURCHASE_ORDER: ("order_date", "delivery_date"),
    D.PROPOSAL: ("date", "valid_until"),
    D.POLICY: ("effective_date", "review_date"),
}


def date_order(s: Snapshot) -> Iterable[Issue]:
    first, second = DATE_PAIRS[s.doc_type]
    a, b = s.day(first), s.day(second)
    if a and b and b < a:
        yield Issue(IssueCode.DATE_ORDER,
                    f"{s.fields[second].label} ({b.isoformat()}) is before {s.fields[first].label} ({a.isoformat()}).",
                    second, s.ev(second))


def line_item_sum(s: Snapshot) -> Iterable[Issue]:
    amounts = [i.amount for i in s.items if i.amount is not None]
    if s.money("subtotal") is not None:
        target_key = "subtotal"
    elif not s.money("tax") and not s.money("discount"):
        target_key = "total"
    else:
        return
    target = s.money(target_key)
    if not amounts or target is None:
        return
    total = sum(amounts, Decimal(0))
    if abs(total - target) > TOLERANCE:
        yield Issue(IssueCode.AMOUNT_MISMATCH,
                    f"Line items add up to {_fmt(total)} but the printed {target_key} is {_fmt(target)}.",
                    target_key, s.ev(target_key))


def totals(s: Snapshot) -> Iterable[Issue]:
    subtotal, total, tax = s.money("subtotal"), s.money("total"), s.money("tax")
    if subtotal is None or total is None or (tax is None and total != subtotal):
        return
    discount, tax = s.money("discount") or Decimal(0), tax or Decimal(0)
    expected = subtotal - abs(discount) + tax
    if abs(expected - total) > TOLERANCE:
        parts = f"subtotal {_fmt(subtotal)}" + (f" - discount {_fmt(abs(discount))}" if discount else "") + f" + tax {_fmt(tax)}"
        yield Issue(IssueCode.AMOUNT_MISMATCH,
                    f"Total {_fmt(total)} does not equal {parts} = {_fmt(expected)}.", "total", s.ev("total"))


def tax_rate(s: Snapshot) -> Iterable[Issue]:
    tax, subtotal, ev = s.money("tax"), s.money("subtotal"), s.ev("tax")
    if tax is None or subtotal is None or ev is None or not ev.text:
        return
    m = re.search(r"(\d+(?:\.\d+)?)\s*%", ev.text)
    if not m:
        return
    rate = Decimal(m.group(1))
    computed = (subtotal * rate / 100).quantize(TOLERANCE)
    if abs(computed - tax) > Decimal("0.02"):
        yield Issue(IssueCode.TAX_INCONSISTENT,
                    f"Tax {_fmt(tax)} is not {rate}% of {_fmt(subtotal)} ({_fmt(computed)}).", "tax", ev)


def currencies(s: Snapshot) -> Iterable[Issue]:
    declared = s.value("currency")
    seen = {f.currency for f in s.fields.values() if f.value_type is ValueType.MONEY and f.currency and f.value}
    if declared and declared not in seen and seen:
        seen.add(declared)
    if len(seen) > 1:
        yield Issue(IssueCode.CURRENCY_MISMATCH, f"Amounts use more than one currency ({', '.join(sorted(seen))}).",
                    "currency", s.ev("currency"))


def written_vs_numeric(s: Snapshot) -> Iterable[Issue]:
    words, amount = s.value("amount_written"), s.money("amount")
    if not words or amount is None:
        return
    spelled = written_amount(words)
    if spelled is not None and spelled != amount:
        yield Issue(IssueCode.WRITTEN_AMOUNT_MISMATCH,
                    f"Written amount reads {_fmt(spelled)} but the numeric amount is {_fmt(amount)}.",
                    "amount_written", s.ev("amount_written"))


def unusual_amount(s: Snapshot) -> Iterable[Issue]:
    key = SCHEMAS[s.doc_type].total
    amount = s.money(key) if key else None
    if amount is None:
        return
    if s.doc_type in (D.CHECK, D.RECEIPT) and amount > s.unusual_amount:
        yield Issue(IssueCode.UNUSUAL_AMOUNT, f"Amount {_fmt(amount)} is above the review limit of {_fmt(s.unusual_amount)}.",
                    key, s.ev(key))
        return
    if len(s.history) >= 2:
        median = Decimal(statistics.median(s.history))
        if median > 0 and amount > UNUSUAL_MULTIPLE * median:
            yield Issue(IssueCode.UNUSUAL_AMOUNT,
                        f"Amount {_fmt(amount)} is more than {UNUSUAL_MULTIPLE}x the usual {_fmt(median)} for this counterparty.",
                        key, s.ev(key))


def micr_matches(s: Snapshot) -> Iterable[Issue]:
    printed, micr = s.value("check_number"), s.value(KV_PREFIX + "micr_check_number")
    if printed and micr and not same_identifier(printed, micr):
        yield Issue(IssueCode.CONFLICTING_VALUES,
                    f"Check number {printed} does not match the MICR line ({micr}).", "check_number", s.ev("check_number"))


def routing_numbers(s: Snapshot) -> Iterable[Issue]:
    for f in s.fields.values():
        if "routing" in f.key and f.value and not aba_routing_valid(re.sub(r"\D", "", f.value)):
            yield Issue(IssueCode.INVALID_IDENTIFIER, f"Routing number {f.value} fails the ABA checksum.", f.key, f.evidence)


def _norm(v: str | None) -> str:
    return re.sub(r"\W+", "", v or "").lower().lstrip("0")


def _peer_key(doc_type: DocType, counterparty: str | None, primary_id: str | None, doc_date: str | None,
              total: Decimal | None) -> tuple | None:
    if doc_type is D.RECEIPT:
        return (_norm(counterparty), doc_date, total) if counterparty and doc_date and total is not None else None
    if doc_type is D.CHECK:
        return (_norm(primary_id),) if primary_id else None
    return (_norm(counterparty), _norm(primary_id)) if primary_id else None


def duplicates(s: Snapshot) -> Iterable[Issue]:
    schema = SCHEMAS[s.doc_type]
    key = _peer_key(s.doc_type, s.value(schema.counterparty or ""), s.value(schema.primary_id or ""),
                    s.value(schema.doc_date or ""), s.money(schema.total) if schema.total else None)
    if key is None:
        return
    for p in s.peers:
        if p.doc_type is s.doc_type and _peer_key(p.doc_type, p.counterparty, p.primary_id, p.doc_date, p.total) == key:
            anchor = schema.primary_id if schema.primary_id and s.value(schema.primary_id) else schema.total
            yield Issue(IssueCode.DUPLICATE_DOCUMENT,
                        f"Looks like a duplicate of another {s.doc_type.value.replace('_', ' ')} already in VaultAI.",
                        anchor, s.ev(anchor or ""))
            return


def signatures(s: Snapshot) -> Iterable[Issue]:
    status, present = s.value("signature_status"), s.value("signature_present")
    if status in {"unsigned", "partially_signed"}:
        who = "No party has signed" if status == "unsigned" else "Not every party has signed"
        yield Issue(IssueCode.MISSING_SIGNATURE, f"{who}.", "signature_status", s.ev("signature_status"))
    elif present == "false":
        yield Issue(IssueCode.MISSING_SIGNATURE, "The signature line is blank.", "signature_present", s.ev("signature_present"))


def legal_disclaimer(s: Snapshot) -> Iterable[Issue]:
    yield Issue(IssueCode.LEGAL_REVIEW_REQUIRED,
                "Clause flags are issue spotting for a person to review, not legal advice.")


def _phrase_evidence(s: Snapshot, phrase: str, fallback: Evidence | None) -> Evidence | None:
    for probe in (phrase.lower(), " ".join(phrase.lower().split()[:3])):
        line = next((ln for ln in s.doc.visible if probe in ln.text.lower()), None)
        if line:
            return evidence(line)
    return fallback


def clause_issues(s: Snapshot) -> Iterable[Issue]:
    for c in s.clauses:
        for f in clause_findings(c.text):
            message = f"{c.heading}: {f.reason}" + (f' ("{f.phrase}").' if f.code is IssueCode.AMBIGUOUS_CLAUSE else ".")
            yield Issue(f.code, message, evidence=_phrase_evidence(s, f.phrase, c.evidence))


def injection(s: Snapshot) -> Iterable[Issue]:
    for ln in s.doc.lines:
        if ln.flag == LineFlag.HIDDEN.value:
            yield Issue(IssueCode.HIDDEN_TEXT, "Text on this page is invisible to a reader (hidden, tiny, or off-page).",
                        evidence=evidence(ln))
        if injection_match(ln.text):
            yield Issue(IssueCode.PROMPT_INJECTION,
                        "Text tries to instruct an AI system; it was quarantined and never sent to a model.",
                        evidence=evidence(ln))


def unsafe_model_values(s: Snapshot) -> Iterable[Issue]:
    for f in s.fields.values():
        if f.origin is FieldOrigin.MODEL and f.value and UNSAFE_VALUE.search(f.value):
            yield Issue(IssueCode.PROMPT_INJECTION,
                        f"{f.label} from the model contains a link, path, or command; it is stored as plain text only.",
                        f.key, f.evidence)


ALL = None
ITEMIZED = frozenset(t for t, schema in SCHEMAS.items() if schema.has_line_items)
RULES: tuple[tuple[frozenset[DocType] | None, Rule], ...] = (
    (ALL, unreadable),
    (ALL, low_ocr),
    (ALL, missing_required),
    (ALL, low_confidence),
    (ALL, invalid_dates),
    (frozenset(DATE_PAIRS), date_order),
    (ITEMIZED, line_item_sum),
    (ITEMIZED, totals),
    (ITEMIZED, tax_rate),
    (ALL, currencies),
    (frozenset({D.CHECK}), written_vs_numeric),
    (frozenset({D.CHECK}), micr_matches),
    (ALL, routing_numbers),
    (ALL, unusual_amount),
    (frozenset({D.CHECK, D.INVOICE, D.RECEIPT, D.PURCHASE_ORDER, D.PROPOSAL}), duplicates),
    (frozenset({D.CHECK, D.ADMIN_FORM, *LEGAL_DOC_TYPES}), signatures),
    (LEGAL_DOC_TYPES, legal_disclaimer),
    (LEGAL_DOC_TYPES, clause_issues),
    (ALL, injection),
    (ALL, unsafe_model_values),
)


def validate(s: Snapshot) -> list[Issue]:
    return [issue for applies, rule in RULES if applies is None or s.doc_type in applies for issue in rule(s)]
