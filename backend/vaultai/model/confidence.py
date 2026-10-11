"""How much VaultAI trusts a grounded model value. The model's own confidence is not the answer.

calibrate() scores one value that already passed grounding (exact quote on a line the
model was shown, value of the right type inside that quote):

    start at 0.80
    +0.05 when one of the field's printed labels is on the evidence line or the line above
    -0.05 when the field has known labels and none is near
    -0.05 when the quote is long enough to be unspecific
    x the line's OCR confidence when the page was OCR'd
    +0.05 when the rules read the same value, -0.25 when they read a different one
    -0.10 when the model itself reports less than 0.5
    capped at 0.85; below 0.40 the value is discarded as weak evidence

cross_check() runs after model values are merged with the rules and lowers a model
value to 0.50 when it fails arithmetic, date order, identifier checks, or the written
amount on a check. Below the review threshold, the field raises
low_extraction_confidence and the document goes to needs_review. Model-assisted
documents can never be auto-approved, whatever these numbers are.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import replace
from decimal import Decimal, InvalidOperation

from vaultai.domain import DocType, FieldOrigin, ValueType
from vaultai.extraction import Found
from vaultai.extraction.normalize import aba_routing_valid, written_amount
from vaultai.parsing.layout import Doc, Line, page_of
from vaultai.schemas import SCHEMAS, FieldSpec
from vaultai.validation.rules import DATE_PAIRS

CAP = 0.85
BASE = 0.80
REJECT_BELOW = 0.40
DOWNGRADED = 0.50
LONG_QUOTE = 160
TOLERANCE = Decimal("0.01")

def _labelled(spec: FieldSpec, doc: Doc, line: Line) -> bool:
    words = [w.casefold() for w in (*spec.aliases, spec.label) if w]
    rows = [ln for ln in doc.page(page_of(line.id)).lines if ln.flag is None]
    idx = next((i for i, ln in enumerate(rows) if ln.id == line.id), 0)
    near = " ".join(ln.text for ln in rows[max(0, idx - 1): idx + 1]).casefold()
    return any(re.search(rf"(?<!\w){re.escape(w)}(?!\w)", near) for w in words)


def same_value(value_type: ValueType, a: str | None, b: str | None) -> bool:
    if a is None or b is None:
        return False
    if value_type is ValueType.MONEY:
        try:
            return Decimal(a) == Decimal(b)
        except InvalidOperation:
            return False
    return " ".join(a.split()).casefold() == " ".join(b.split()).casefold()


def calibrate(spec: FieldSpec, value: str, quote: str, line: Line, doc: Doc, rules: Found | None,
              reported: float | None) -> tuple[float, list[str]]:
    score, reasons = BASE, []
    if _labelled(spec, doc, line):
        score += 0.05
    elif spec.aliases:
        score -= 0.05
        reasons.append("no known label near the value")
    if len(quote) > LONG_QUOTE:
        score -= 0.05
        reasons.append("long quote")
    if doc.is_ocr(line) and line.confidence is not None:
        score *= line.confidence
        reasons.append(f"OCR confidence {line.confidence:.0%}")
    if rules is not None and rules.value is not None:
        if same_value(spec.value_type, rules.value, value):
            score += 0.05
        else:
            score -= 0.25
            reasons.append("rules read a different value")
    if reported is not None and reported < 0.5:
        score -= 0.10
        reasons.append("model reported low confidence")
    return round(min(score, CAP), 4), reasons


def _money(fields: Mapping[str, Found], key: str) -> Decimal | None:
    f = fields.get(key)
    try:
        return Decimal(f.value) if f and f.value is not None else None
    except InvalidOperation:
        return None


def problems(doc_type: DocType, fields: Mapping[str, Found]) -> dict[str, str]:
    """Model-sourced field keys that fail a cross-field check, with the reason."""
    model = {k for k, f in fields.items() if f.origin is FieldOrigin.MODEL}
    if not model:
        return {}
    out: dict[str, str] = {}

    def flag(keys: tuple[str, ...], reason: str) -> None:
        for k in keys:
            if k in model:
                out.setdefault(k, reason)

    subtotal, total, tax, discount = (_money(fields, k) for k in ("subtotal", "total", "tax", "discount"))
    if subtotal is not None and total is not None and tax is not None:
        if abs(subtotal - abs(discount or Decimal(0)) + tax - total) > TOLERANCE:
            flag(("subtotal", "tax", "total", "discount"), "amounts do not add up")
    if total is not None and subtotal is not None and tax is None and total < subtotal - TOLERANCE:
        flag(("total", "subtotal"), "total is below the subtotal")
    pair = DATE_PAIRS.get(doc_type)
    if pair and all(fields.get(k) and fields[k].value for k in pair) and fields[pair[1]].value < fields[pair[0]].value:
        flag(pair, "dates are out of order")
    schema = SCHEMAS[doc_type]
    ids = {k: f.value for k, f in fields.items()
           if k in schema.keys and schema.field(k).value_type is ValueType.IDENTIFIER and f.value}
    for k, v in ids.items():
        if k in model and any(other != k and same_value(ValueType.TEXT, v, ov) for other, ov in ids.items()):
            flag((k,), "same identifier as another field")
        if k in model and not re.search(r"\d", v):
            flag((k,), "identifier has no digits")
        if k in model and "routing" in k and not aba_routing_valid(re.sub(r"\D", "", v)):
            flag((k,), "routing number fails its checksum")
    words, amount = fields.get("amount_written"), _money(fields, "amount")
    if doc_type is DocType.CHECK and words and words.value and amount is not None:
        spelled = written_amount(words.value)
        if spelled is not None and spelled != amount:
            flag(("amount", "amount_written"), "written and numeric amounts differ")
    return out


def cross_check(doc_type: DocType, fields: Mapping[str, Found]) -> dict[str, Found]:
    bad = problems(doc_type, fields)
    return {k: replace(f, confidence=min(f.confidence, DOWNGRADED)) if k in bad else f for k, f in fields.items()}
