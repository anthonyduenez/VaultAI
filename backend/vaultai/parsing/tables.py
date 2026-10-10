"""Column tables recovered from line spans: a header row of known column titles, then rows until a stop line."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from vaultai.extraction import Item, evidence
from vaultai.extraction.normalize import parse_money
from vaultai.parsing.layout import Doc, Line, Span

LINE_ITEM_COLUMNS: Mapping[str, re.Pattern[str]] = {
    "description": re.compile(r"^(description|item|items|service|services|product|details|particulars)$"),
    "quantity": re.compile(r"^(qty|quantity|hrs|hours|units|qty\.)$"),
    "unit_price": re.compile(r"^(unit price|price|rate|unit cost|each|unit)$"),
    "amount": re.compile(r"^(amount|line total|total|ext\.? price|extended|amount \(usd\))$"),
}
LINE_ITEM_STOP = re.compile(r"^(sub-?total|total|tax|sales tax|vat|balance|amount due|discount|shipping|continued)\b")

DELIVERABLE_COLUMNS: Mapping[str, re.Pattern[str]] = {
    "description": re.compile(r"^(deliverables?|milestones?|item|description)$"),
    "due": re.compile(r"^(due date|due|date|target date|completion date)$"),
}


@dataclass(frozen=True)
class Column:
    role: str
    x0: float
    x1: float


@dataclass(frozen=True)
class Row:
    line: Line
    cells: dict[str, str]


def _header(line: Line, roles: Mapping[str, re.Pattern[str]], required: frozenset[str]) -> list[Column] | None:
    cols: list[Column] = []
    for span in line.spans:
        title = span.text.lower().strip(" :")
        role = next((r for r, p in roles.items() if p.match(title)), None)
        if role is None:
            return None
        cols.append(Column(role, span.x0, span.x1))
    return cols if required <= {c.role for c in cols} and len(cols) >= 2 else None


def _column_for(span: Span, cols: list[Column]) -> Column:
    def overlap(c: Column) -> float:
        return min(c.x1, span.x1) - max(c.x0, span.x0)

    best = max(cols, key=overlap)
    if overlap(best) > 0:
        return best
    centre = (span.x0 + span.x1) / 2
    return min(cols, key=lambda c: abs((c.x0 + c.x1) / 2 - centre))


def read_rows(doc: Doc, roles: Mapping[str, re.Pattern[str]], required: frozenset[str], stop: re.Pattern[str]) -> list[Row]:
    """Rows of every table whose header matches `roles`; a table may continue on the next page under a repeated header."""
    rows: list[Row] = []
    for page in doc.pages:
        cols: list[Column] | None = None
        for line in (ln for ln in page.lines if ln.flag is None):
            if cols is None:
                cols = _header(line, roles, required)
                continue
            if stop.match(line.text.lower()) or len(line.spans) < 2:
                cols = None
                continue
            cells: dict[str, str] = {}
            for span in line.spans:
                role = _column_for(span, cols).role
                cells[role] = f"{cells[role]} {span.text}" if role in cells else span.text
            rows.append(Row(line, cells))
    return rows


def _decimal(text: str | None) -> Decimal | None:
    if not text:
        return None
    t = text.replace(",", "").strip()
    if not re.fullmatch(r"\d+(?:\.\d+)?", t):
        return None
    try:
        return Decimal(t)
    except InvalidOperation:
        return None


def line_items(doc: Doc) -> list[Item]:
    items: list[Item] = []
    for row in read_rows(doc, LINE_ITEM_COLUMNS, frozenset({"description", "amount"}), LINE_ITEM_STOP):
        description = row.cells.get("description", "").strip()
        amount = parse_money(row.cells.get("amount", ""))
        price = parse_money(row.cells.get("unit_price", ""))
        if not description or (amount is None and price is None):
            continue
        items.append(Item(
            description=description,
            quantity=_decimal(row.cells.get("quantity")),
            unit_price=price.amount if price else None,
            amount=amount.amount if amount else None,
            evidence=evidence(row.line),
        ))
    return items
