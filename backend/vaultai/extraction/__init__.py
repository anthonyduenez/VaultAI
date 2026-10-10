"""Records produced by extraction: one Found per field value, one Item per line item."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from vaultai.domain import Evidence, FieldOrigin
from vaultai.parsing.layout import Line, page_of

# Confidence by how a value was located (scaled by OCR line confidence on scanned pages).
SAME_LINE = 0.95
NEXT_LINE = 0.85
PATTERN = 0.8


@dataclass(frozen=True)
class Found:
    key: str
    # Canonical string: Decimal for money, ISO for dates, "true"/"false", printed text otherwise.
    # None with raw_text set means the printed value could not be read (e.g. 02/30/2026).
    value: str | None
    raw_text: str | None
    confidence: float
    evidence: Evidence | None
    currency: str | None = None
    origin: FieldOrigin = FieldOrigin.RULES
    model_name: str | None = None


@dataclass(frozen=True)
class Item:
    description: str
    quantity: Decimal | None
    unit_price: Decimal | None
    amount: Decimal | None
    evidence: Evidence | None


def evidence(line: Line) -> Evidence:
    return Evidence(page=page_of(line.id), bbox=line.bbox, line_id=line.id, text=line.text)
