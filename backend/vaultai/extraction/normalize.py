"""Pure normalizers from printed text to typed values.

Each finder returns the printed substring alongside the value so evidence can
quote exactly what the document says. Ambiguous input yields None, never a guess.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

CENT = Decimal("0.01")

CURRENCY_SYMBOLS = {"$": "USD", "US$": "USD", "€": "EUR", "£": "GBP", "¥": "JPY"}
CURRENCY_CODES = ("USD", "EUR", "GBP", "CAD", "AUD", "JPY", "MXN")

_CODE = "|".join(CURRENCY_CODES)
_MONEY = re.compile(
    rf"(?P<neg>-|\()?\s*(?P<pre>US\$|[$€£¥]|(?:{_CODE})\b)?\s*"
    r"(?<![\d.,])(?P<num>\d{1,3}(?:\.\d{3})+,\d{2}|\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?|\d+(?:[.,]\d{1,2})?)(?![\d/%]|[.,]\d)"
    rf"(?:\s*(?P<post>[€£]|(?:{_CODE})\b))?\)?"
)


@dataclass(frozen=True)
class Money:
    amount: Decimal
    currency: str | None
    raw: str
    start: int = 0


def _number(num: str, currency: str | None) -> Decimal | None:
    if re.fullmatch(r"\d{1,3}(?:\.\d{3})+,\d{2}", num) or re.fullmatch(r"\d+,\d{2}", num):
        if currency != "EUR":
            return None
        num = num.replace(".", "").replace(",", ".")
    else:
        num = num.replace(",", "")
    try:
        return Decimal(num).quantize(CENT)
    except InvalidOperation:
        return None


def find_money(text: str, *, require_marker: bool = False) -> list[Money]:
    """Money amounts in reading order. With require_marker, bare numbers without a currency or cents are skipped."""
    out: list[Money] = []
    for m in _MONEY.finditer(text):
        marker = m.group("pre") or m.group("post")
        currency = CURRENCY_SYMBOLS.get(marker, marker) if marker else None
        num = m.group("num")
        has_cents = bool(re.search(r"[.,]\d{2}$", num))
        if require_marker and not marker and not has_cents:
            continue
        value = _number(num, currency)
        if value is None:
            continue
        if m.group("neg"):
            value = -value
        out.append(Money(value, currency, m.group(0).strip(), m.start()))
    return out


def parse_money(text: str) -> Money | None:
    found = find_money(text)
    return found[0] if found else None


MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
_MONTH = r"(?P<mon>jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?"
_DATE_PATTERNS = (
    ("iso", re.compile(r"\b(?P<y>\d{4})-(?P<a>\d{1,2})-(?P<b>\d{1,2})\b")),
    ("numeric", re.compile(r"\b(?P<a>\d{1,2})[/.-](?P<b>\d{1,2})[/.-](?P<y>\d{4}|\d{2})\b")),
    ("month_first", re.compile(rf"\b{_MONTH}\s+(?P<d>\d{{1,2}})(?:st|nd|rd|th)?,?\s+(?P<y>\d{{4}})\b", re.I)),
    ("day_first", re.compile(rf"\b(?P<d>\d{{1,2}})(?:st|nd|rd|th)?\s+{_MONTH},?\s+(?P<y>\d{{4}})\b", re.I)),
)


@dataclass(frozen=True)
class DateHit:
    value: date | None
    raw: str
    start: int


def _ymd(kind: str, m: re.Match, date_order: str) -> tuple[int, int, int]:
    if kind == "iso":
        return int(m["y"]), int(m["a"]), int(m["b"])
    if kind == "numeric":
        y = int(m["y"])
        y = y + 2000 if y < 100 else y
        a, b = int(m["a"]), int(m["b"])
        return (y, a, b) if date_order == "MDY" else (y, b, a)
    return int(m["y"]), MONTHS[m["mon"].lower()[:3]], int(m["d"])


def find_dates(text: str, date_order: str = "MDY") -> list[DateHit]:
    hits: list[DateHit] = []
    taken: list[tuple[int, int]] = []
    for kind, pattern in _DATE_PATTERNS:
        for m in pattern.finditer(text):
            if any(m.start() < e and s < m.end() for s, e in taken):
                continue
            y, mo, d = _ymd(kind, m, date_order)
            try:
                value: date | None = date(y, mo, d)
            except ValueError:
                value = None
            hits.append(DateHit(value, m.group(0), m.start()))
            taken.append((m.start(), m.end()))
    return sorted(hits, key=lambda h: h.start)


_UNITS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen "
    "sixteen seventeen eighteen nineteen".split())}
_TENS = {w: 10 * i for i, w in enumerate("_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()) if w != "_"}
_SCALES = {"thousand": 1_000, "million": 1_000_000, "billion": 1_000_000_000}
WRITTEN_AMOUNT = re.compile(
    r"^(?P<words>[A-Za-z][A-Za-z\- ]*?)\s+(?:dollars\s+)?(?:and\s+)?(?P<cents>\d{1,2})/100\b(?:\s+dollars)?", re.I
)


def written_amount(text: str) -> Decimal | None:
    """'One thousand two hundred fifty and 00/100 DOLLARS' -> 1250.00. None when any word is not a number word."""
    m = WRITTEN_AMOUNT.match(text.strip().strip("*"))
    if not m:
        return None
    total = current = 0
    seen = False
    for word in re.split(r"[\s-]+", m["words"].lower()):
        if word in {"and", "dollars", "dollar", ""}:
            continue
        if word in _UNITS:
            current += _UNITS[word]
        elif word in _TENS:
            current += _TENS[word]
        elif word == "hundred":
            current = (current or 1) * 100
        elif word in _SCALES:
            total += (current or 1) * _SCALES[word]
            current = 0
        else:
            return None
        seen = True
    if not seen:
        return None
    return (Decimal(total + current) + Decimal(int(m["cents"])) / 100).quantize(CENT)


_SMALL_NUMBERS = {**_UNITS, **_TENS}


def number_word(text: str) -> int | None:
    """'sixty (60)' -> 60, '60' -> 60, 'thirty' -> 30."""
    m = re.search(r"\((\d+)\)", text)
    if m:
        return int(m.group(1))
    m = re.search(r"\b(\d+)\b", text)
    if m:
        return int(m.group(1))
    words = re.split(r"[\s-]+", text.strip().lower())
    if words and all(w in _SMALL_NUMBERS for w in words):
        return sum(_SMALL_NUMBERS[w] for w in words)
    return None


_IDENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9\-/.#]*[A-Za-z0-9]|[0-9]")


def find_identifier(text: str) -> str | None:
    """First token that contains a digit, as printed (leading zeros kept)."""
    for m in _IDENT.finditer(text):
        if any(ch.isdigit() for ch in m.group()):
            return m.group()
    return None


def same_identifier(a: str, b: str) -> bool:
    """Printed check numbers often carry leading zeros on the MICR line only."""
    return a.strip().lstrip("0") == b.strip().lstrip("0")


def aba_routing_valid(routing: str) -> bool:
    if not re.fullmatch(r"\d{9}", routing):
        return False
    d = [int(c) for c in routing]
    return (3 * (d[0] + d[3] + d[6]) + 7 * (d[1] + d[4] + d[7]) + (d[2] + d[5] + d[8])) % 10 == 0


def money_str(value: Decimal) -> str:
    return str(value.quantize(CENT))


def clean_text(text: str) -> str:
    """Trim label punctuation, check-style fill characters, and blank-line underscores."""
    return re.sub(r"^[\s:#*.\-–]+|[\s*_,;:\-–]+$", "", " ".join(text.split()))


def slug(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")[:48]
