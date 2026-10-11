"""CSV writer for the Approved Records view: UTF-8 with BOM, text cells defused against formula injection."""

from __future__ import annotations

import csv
import io
from datetime import UTC, date, datetime
from decimal import Decimal

from vaultai.export import FORMULA_PREFIXES, TEXT_KINDS, Kind, Table


def neutralize(text: str) -> str:
    return f"'{text}" if text.startswith(FORMULA_PREFIXES) else text


def _cell(value: object, kind: Kind) -> str:
    if value is None:
        return ""
    if kind in TEXT_KINDS:
        return neutralize(str(value))
    if kind is Kind.MONEY:
        return format(Decimal(str(value)), "f")
    if isinstance(value, datetime):
        return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def render_csv(table: Table) -> bytes:
    buf = io.StringIO(newline="")
    writer = csv.writer(buf, lineterminator="\r\n")
    writer.writerow([c.header for c in table.columns])
    for row in table.rows:
        writer.writerow([_cell(v, c.kind) for c, v in zip(table.columns, row, strict=True)])
    return ("﻿" + buf.getvalue()).encode("utf-8")
