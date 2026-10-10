"""Tables an export is made of. The service decides what goes in them; xlsx.py and csv_export.py decide how cells are written."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Kind(StrEnum):
    TEXT = "text"
    ID = "id"
    MONEY = "money"
    DATE = "date"
    DATETIME = "datetime"
    NUMBER = "number"
    INT = "int"


TEXT_KINDS = frozenset({Kind.TEXT, Kind.ID})

# A spreadsheet treats a typed cell starting with one of these as a formula.
FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


@dataclass(frozen=True)
class Column:
    header: str
    kind: Kind = Kind.TEXT
    width: int = 16


@dataclass(frozen=True)
class Table:
    name: str
    columns: tuple[Column, ...]
    rows: list[tuple[object, ...]]
