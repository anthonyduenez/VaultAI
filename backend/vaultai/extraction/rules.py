"""Deterministic field extraction: the fast path that runs before any model.

Three sources, in one registry per document type:
  1. label/value matching with each FieldSpec's aliases (or its label when specific),
     on the same span, the next span, or the line below;
  2. positional patterns (issuer at the top, document title, MICR line, card
     mask, written amount, signature blocks);
  3. generic "Label: value" pairs on forms and letters, stored as kv: fields.
Legal documents add analysis.contracts on top. Nothing here guesses: a value
that does not parse for its type is not returned.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace

from vaultai.domain import DocType, Evidence, ValueType
from vaultai.extraction import NEXT_LINE, PATTERN, SAME_LINE, Found, evidence
from vaultai.extraction.normalize import (
    WRITTEN_AMOUNT,
    clean_text,
    find_dates,
    find_identifier,
    find_money,
    money_str,
    same_identifier,
    slug,
    written_amount,
)
from vaultai.parsing.layout import Doc, Line, Span, page_of
from vaultai.schemas import KV_PREFIX, SCHEMAS, DocSchema, FieldSpec

# Two independent positional signals (first line and the most prominent type) for an unlabeled name.
PROMINENT = 0.9
TOP_LINES = 8


@dataclass(frozen=True)
class Ctx:
    doc: Doc
    schema: DocSchema
    date_order: str


def scaled(doc: Doc, line: Line, confidence: float) -> float:
    if doc.is_ocr(line) and line.confidence is not None:
        return round(confidence * line.confidence, 4)
    return confidence


def found(ctx: Ctx, key: str, line: Line, value: str | None, raw: str | None, confidence: float,
          currency: str | None = None) -> Found:
    return Found(key, value, raw, scaled(ctx.doc, line, confidence), evidence(line), currency)


# ---------------------------------------------------------------- typed reading of a value string


def read_value(ctx: Ctx, spec: FieldSpec, text: str) -> tuple[str | None, str, str | None] | None:
    """(canonical value, printed substring, currency) for `text` read as spec.value_type, or None."""
    vt = spec.value_type
    if vt is ValueType.MONEY:
        hits = find_money(text, require_marker=True)
        return (money_str(hits[0].amount), hits[0].raw, hits[0].currency) if hits else None
    if vt is ValueType.DATE:
        hits = find_dates(text, ctx.date_order)
        if not hits:
            return None
        h = hits[0]
        return (h.value.isoformat() if h.value else None, h.raw, None)
    if vt is ValueType.IDENTIFIER:
        ident = find_identifier(text)
        return (ident, ident, None) if ident else None
    if vt is ValueType.ENUM:
        word = clean_text(text).upper()
        options = {o.upper(): o for o in spec.options}
        return (options[word], clean_text(text), None) if word in options else None
    if vt is ValueType.BOOL:
        return None
    value = clean_text(text)
    if not value or not re.search(r"[A-Za-z0-9]", value) or re.fullmatch(r"[_\s]+", value):
        return None
    return (value, value, None)


# ---------------------------------------------------------------- label/value matching

_LABEL_TAIL = re.compile(r"^\s*(?:\([^)]{0,24}\))?\s*(?:\d+(?:\.\d+)?\s*%)?\s*(?P<sep>[:#]|\.(?=\s|$))?\s*")


def _after_label(text: str, alias: str) -> tuple[str, bool] | None:
    if not text.lower().startswith(alias):
        return None
    rest = text[len(alias):]
    if rest[:1].isalnum():
        return None
    m = _LABEL_TAIL.match(rest)
    return rest[m.end():], bool(m.group("sep"))


def is_label(span: Span) -> bool:
    return span.text.rstrip().endswith(":")


def _below(doc: Doc, line: Line, span: Span) -> tuple[Line, Span] | None:
    """The span directly under `span` on the next line of the same page."""
    page = doc.page(page_of(line.id))
    rows = [ln for ln in page.lines if ln.flag is None]
    idx = rows.index(line)
    for nxt in rows[idx + 1: idx + 3]:
        for s in nxt.spans:
            if s.x1 > span.x0 - 0.02 and s.x0 < span.x1 + 0.15 and abs(s.x0 - span.x0) < 0.12:
                return nxt, s
    return None


def _aliases(spec: FieldSpec) -> tuple[str, ...]:
    """A field without aliases is found by its own label, when that label is specific (two words or more)."""
    label = spec.label.lower()
    return spec.aliases or ((label,) if len(label.split()) >= 2 else ())


def label_value(ctx: Ctx, spec: FieldSpec, taken: set[tuple[str, int]]) -> Found | None:
    text_type = spec.value_type in (ValueType.TEXT, ValueType.LONG_TEXT)
    for alias in _aliases(spec):
        for line in ctx.doc.visible:
            for i, span in enumerate(line.spans):
                if (line.id, i) in taken:
                    continue
                hit = _after_label(span.text, alias)
                if hit is None:
                    continue
                rest, has_sep = hit
                if rest and not has_sep and text_type and len(alias.split()) < 3:
                    continue
                candidates: list[tuple[Line, str, float]] = []
                if rest:
                    candidates.append((line, rest, SAME_LINE))
                elif i + 1 < len(line.spans) and not is_label(line.spans[i + 1]):
                    candidates.append((line, line.spans[i + 1].text, SAME_LINE))
                else:
                    below = _below(ctx.doc, line, span)
                    if below and not is_label(below[1]):
                        candidates.append((below[0], below[1].text, NEXT_LINE))
                for value_line, text, conf in candidates:
                    read = read_value(ctx, spec, text)
                    if read is None:
                        continue
                    taken.add((line.id, i))
                    value, raw, currency = read
                    return found(ctx, spec.key, value_line, value, raw, conf, currency)
    return None


# ---------------------------------------------------------------- positional patterns

Extractor = Callable[[Ctx], Iterable[Found]]

DOC_TITLE_WORDS = re.compile(
    r"^(invoice|tax invoice|receipt|sales receipt|quote|quotation|proposal|estimate|purchase order|statement|"
    r"memorandum|memo|check|cheque|bill|credit note)$", re.I)
_PHONE = re.compile(r"^\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}$")
ADDRESS = re.compile(r"^\d+[A-Za-z]?\s+[\w .'#-]+,\s*[A-Za-z .'-]+,\s*[A-Z]{2}\s+\d{5}(?:-\d{4})?$")
_STREET = re.compile(r"^\d+[A-Za-z]?\s+[\w .'#-]+$")
_CITY = re.compile(r"^[A-Za-z .'-]+,\s*[A-Z]{2}\s+\d{5}(?:-\d{4})?$")
ORG_SUFFIX = re.compile(
    r"\b(LLC|L\.L\.C\.|Inc\.?|Incorporated|Corp\.?|Corporation|Company|Co\.|Ltd\.?|LLP|LP|Group|Partners|Associates|Bank)\b")


def top_lines(doc: Doc) -> list[Line]:
    return [ln for ln in doc.pages[0].lines if ln.flag is None][:TOP_LINES] if doc.pages else []


def _height(line: Line) -> float:
    return (line.bbox[3] - line.bbox[1]) if line.bbox else 0.0


def _plain_name(span: Span) -> bool:
    """Could this span be a business name: not a label, title word, date, amount, contact detail, address, or bare id."""
    t = clean_text(span.text)
    return bool(
        re.search(r"[A-Za-z]{2}", t) and not is_label(span) and ":" not in t and not DOC_TITLE_WORDS.match(t)
        and not find_dates(t) and not _PHONE.match(t) and "@" not in t and not find_money(t, require_marker=True)
        and not (ADDRESS.match(t) or _STREET.match(t) or _CITY.match(t)) and find_identifier(t) != t
    )


def issuer(key: str) -> Extractor:
    """The business named first at the top of page 1 (letterhead, store name, check payer)."""

    def extract(ctx: Ctx) -> Iterable[Found]:
        tops = top_lines(ctx.doc)
        for line in tops[:3]:
            span = next((s for s in line.spans if _plain_name(s)), None)
            if span is None:
                continue
            prominent = line is tops[0] and _height(line) >= max(_height(t) for t in tops)
            value = clean_text(span.text)
            yield found(ctx, key, line, value, value, PROMINENT if prominent else PATTERN)
            return

    return extract


def title(key: str) -> Extractor:
    """First span of the most prominent line near the top of page 1."""

    def extract(ctx: Ctx) -> Iterable[Found]:
        tops = top_lines(ctx.doc)
        if not tops:
            return
        line = max(tops, key=_height)
        span = next((s for s in line.spans if re.search(r"[A-Za-z]{3}", s.text)), None)
        if span is None or _height(line) < 1.2 * sorted(_height(t) for t in tops)[len(tops) // 2]:
            return
        value = clean_text(span.text)
        yield found(ctx, key, line, value, value, PROMINENT)

    return extract


def address_after_issuer(key: str) -> Extractor:
    def extract(ctx: Ctx) -> Iterable[Found]:
        tops = top_lines(ctx.doc)
        for i, line in enumerate(tops[1:5], start=1):
            first = clean_text(line.spans[0].text) if line.spans else ""
            if ADDRESS.match(first):
                yield found(ctx, key, line, first, first, PATTERN)
                return
            nxt = tops[i + 1] if i + 1 < len(tops) else None
            if _STREET.match(first) and nxt and nxt.spans and _CITY.match(clean_text(nxt.spans[0].text)):
                value = f"{first}, {clean_text(nxt.spans[0].text)}"
                yield found(ctx, key, line, value, value, PATTERN)
                return

    return extract


_CARD = re.compile(
    r"\b(VISA|MASTERCARD|MC|AMEX|AMERICAN EXPRESS|DISCOVER|DEBIT|CREDIT|EBT)\b[\s:]*(?:[*xX•]{2,}[\s-]*)+\d{4}\b", re.I)


def card_payment(ctx: Ctx) -> Iterable[Found]:
    for line in ctx.doc.visible:
        m = _CARD.search(line.text)
        if m:
            yield found(ctx, "payment_method", line, m.group(0), m.group(0), PATTERN)
            return


# ---------------------------------------------------------------- checks

MICR = re.compile(r"[A⑆](\d{9})[A⑆]\s*([\d -]+?)\s*[C⑈]\s*(\d+)")
_BANK = re.compile(r"\b(Bank|Credit Union|Savings|Bancorp|Trust Company|Federal Credit)\b", re.I)


def bank_name(ctx: Ctx) -> Iterable[Found]:
    for line in top_lines(ctx.doc)[:4]:
        for span in line.spans:
            if _BANK.search(span.text) and not is_label(span):
                value = clean_text(span.text)
                yield found(ctx, "bank_name", line, value, value, PATTERN)
                return


def micr(ctx: Ctx) -> Iterable[Found]:
    for line in ctx.doc.visible:
        m = MICR.search(line.text)
        if not m:
            continue
        routing, account, number = m.group(1), m.group(2).replace(" ", "").replace("-", ""), m.group(3)
        yield found(ctx, "routing_number", line, routing, routing, PATTERN)
        yield found(ctx, "account_number", line, account, account, PATTERN)
        yield found(ctx, KV_PREFIX + "micr_check_number", line, number, number, PATTERN)
        return


def check_number(ctx: Ctx) -> Iterable[Found]:
    """A bare 3-8 digit number on the right of the top lines, corroborated by the MICR line when present."""
    micr_number = next((m.group(3) for ln in ctx.doc.visible if (m := MICR.search(ln.text))), None)
    for line in top_lines(ctx.doc)[:3]:
        for span in line.spans:
            if span.x0 > 0.5 and re.fullmatch(r"(?:no\.?\s*)?\d{3,8}", span.text.strip(), re.I):
                value = re.sub(r"\D", "", span.text)
                conf = PROMINENT if micr_number and same_identifier(value, micr_number) else PATTERN
                yield found(ctx, "check_number", line, value, span.text.strip(), conf)
                return


def check_amounts(ctx: Ctx) -> Iterable[Found]:
    """The amount box (money on the payee line, or a span holding only an amount) and the written amount line."""
    written = None
    for line in ctx.doc.visible:
        text = line.text.strip().strip("*")
        if WRITTEN_AMOUNT.match(text) and written_amount(text) is not None:
            written = (line, clean_text(text), written_amount(text))
            break
    box = None
    for line in ctx.doc.visible:
        on_payee_line = "order of" in line.text.lower()
        for span in line.spans:
            hits = find_money(span.text, require_marker=True)
            if hits and (on_payee_line or clean_text(span.text) == hits[0].raw.strip()):
                box = (line, hits[0])
                break
        if box:
            break
    if written:
        yield found(ctx, "amount_written", written[0], written[1], written[1], PATTERN)
    if box:
        line, money = box
        conf = PROMINENT if written and written[2] == money.amount else PATTERN
        yield found(ctx, "amount", line, money_str(money.amount), money.raw, conf, money.currency)


# ---------------------------------------------------------------- signatures

_BY = re.compile(r"^by:?(?:\s|$)", re.I)
_SIGNATURE_LABEL = re.compile(r"^(?:authorized\s+|customer\s+|employee\s+|applicant\s+)?signature\b", re.I)
_CLOSING = re.compile(r"^(sincerely|regards|best regards|kind regards|very truly yours|yours truly|respectfully|cordially),?$", re.I)
_ACCEPTANCE = re.compile(r"\b(accepted and agreed|agreed and accepted|acknowledged and agreed|accepted by|agreed to by)\b", re.I)
_NAME_LABEL = re.compile(r"^name:\s*(?P<name>.*)$", re.I)


@dataclass(frozen=True)
class SignatureBlock:
    signed: bool
    party: str | None
    name: str | None
    acceptance: bool
    closing: bool
    evidence: Evidence


def _mark_in(ctx: Ctx, line: Line, x0: float, x1: float, above: float, below: float) -> bool:
    page = ctx.doc.page(page_of(line.id))
    h = _height(line)
    y0, y1 = line.bbox[1] - above * h, line.bbox[3] + below * h
    return any(m[0] < x1 and m[2] > x0 and m[1] < y1 and m[3] > y0 for m in page.marks)


def _column_text(rows: list[Line], start: int, step: int, x0: float, x1: float, limit: int) -> Iterable[tuple[Line, str]]:
    i = start + step
    while 0 <= i < len(rows) and abs(i - start) <= limit:
        for s in rows[i].spans:
            if s.x0 >= x0 - 0.03 and s.x0 < x1:
                yield rows[i], s.text
        i += step


def signature_blocks(ctx: Ctx) -> list[SignatureBlock]:
    """Signature lines and whether each carries a drawn signature.

    A block counts as signed only when a hand-drawn curve or an embedded image sits on its
    signature line (or a conformed "/s/" signature is typed there). A typed name alone does not
    count. Scanned pages carry no vector marks, so their blocks are not reported at all.
    """
    blocks: list[SignatureBlock] = []
    for page in ctx.doc.pages:
        if page.text_method != "pdf_text":
            continue
        rows = [ln for ln in page.lines if ln.flag is None and ln.bbox]
        for idx, line in enumerate(rows):
            if _CLOSING.match(line.text.strip()) and idx + 1 < len(rows):
                name_line = rows[idx + 1]
                x0 = line.bbox[0] - 0.02
                gap = (name_line.bbox[1] - line.bbox[3]) / max(_height(name_line), 1e-6)
                signed = _mark_in(ctx, name_line, x0, x0 + 0.45, gap + 0.5, 0.2)
                org = rows[idx + 2].text.split(",", 1)[-1].strip() if idx + 2 < len(rows) else None
                blocks.append(SignatureBlock(signed, org if org and ORG_SUFFIX.search(org) else None,
                                             clean_text(name_line.text), False, True, evidence(name_line)))
                continue
            for si, span in enumerate(line.spans):
                x1 = line.spans[si + 1].x0 if si + 1 < len(line.spans) else min(1.0, span.x0 + 0.45)
                if _BY.match(span.text):
                    signed = "/s/" in span.text or _mark_in(ctx, line, span.x0 - 0.02, x1, 2.5, 1.0)
                    above = [t for _, t in _column_text(rows, idx, -1, span.x0, x1, 3)]
                    party = next((clean_text(t) for t in above if ORG_SUFFIX.search(t) and not _ACCEPTANCE.search(t)), None)
                    name = next((m["name"] for _, t in _column_text(rows, idx, 1, span.x0, x1, 2)
                                 if (m := _NAME_LABEL.match(t))), None)
                    name = clean_text(name) if name and re.search(r"[A-Za-z]", name) else None
                    acceptance = any(_ACCEPTANCE.search(ln.text) for ln in rows[max(0, idx - 4): idx])
                    blocks.append(SignatureBlock(signed, party, name, acceptance, False, evidence(line)))
                elif _SIGNATURE_LABEL.match(span.text.strip()) and len(span.text.split()) <= 3:
                    signed = _mark_in(ctx, line, span.x0 - 0.05, span.x0 + 0.45, 5.0, 0.0)
                    blocks.append(SignatureBlock(signed, None, None, False, False, evidence(line)))
    return blocks


def signature_present(ctx: Ctx) -> Iterable[Found]:
    blocks = [b for b in signature_blocks(ctx) if not b.closing]
    if blocks:
        signed = next((b for b in blocks if b.signed), None)
        b = signed or blocks[0]
        line = ctx.doc.by_id[b.evidence.line_id]
        yield found(ctx, "signature_present", line, "true" if signed else "false", b.evidence.text, PATTERN)


# ---------------------------------------------------------------- generic key/value pairs

_KV = re.compile(r"^(?P<label>[A-Za-z][A-Za-z0-9 /&()'.-]{1,40}?)\s*:\s*(?P<value>\S.*)?$")


def key_values(ctx: Ctx, taken: set[tuple[str, int]]) -> Iterable[Found]:
    seen: set[str] = set()
    for line in ctx.doc.visible:
        for i, span in enumerate(line.spans):
            if (line.id, i) in taken:
                continue
            m = _KV.match(span.text.strip())
            if not m or len(m["label"].split()) > 5:
                continue
            value = m["value"]
            if not value and i + 1 < len(line.spans) and not is_label(line.spans[i + 1]):
                value = line.spans[i + 1].text
                taken.add((line.id, i + 1))
            value = clean_text(value or "")
            key = KV_PREFIX + slug(m["label"])
            if not value or not re.search(r"[A-Za-z0-9]", value) or key in seen:
                continue
            seen.add(key)
            yield found(ctx, key, line, value, value, SAME_LINE)


def kv_label(key: str) -> str:
    return key[len(KV_PREFIX):].replace("_", " ").capitalize()


# ---------------------------------------------------------------- registry


def _legal(ctx: Ctx) -> Iterable[Found]:
    # analysis.contracts builds on this module, so it is imported at call time.
    from vaultai.analysis.contracts import legal_fields

    return legal_fields(ctx)


SPECIFIC: dict[DocType, tuple[Extractor, ...]] = {
    DocType.CHECK: (issuer("payer"), bank_name, check_number, check_amounts, micr, signature_present),
    DocType.RECEIPT: (issuer("merchant"), address_after_issuer("merchant_address"), card_payment),
    DocType.INVOICE: (issuer("vendor"), address_after_issuer("vendor_address")),
    DocType.PURCHASE_ORDER: (issuer("buyer"),),
    DocType.PROPOSAL: (issuer("vendor"),),
    DocType.POLICY: (title("title"), _legal),
    DocType.ADMIN_FORM: (title("form_title"), signature_present),
    DocType.CORRESPONDENCE: (),
    DocType.OTHER: (title("title"), issuer("organization")),
    DocType.CONTRACT: (title("title"), _legal),
    DocType.STATEMENT_OF_WORK: (title("title"), _legal),
    DocType.AGREEMENT_LETTER: (issuer("sender_organization"), _legal),
}
KV_TYPES = frozenset({DocType.ADMIN_FORM, DocType.CORRESPONDENCE, DocType.OTHER})
DOLLAR_CODES = ("USD", "CAD", "AUD", "MXN")


def _merge(into: dict[str, Found], candidates: Iterable[Found]) -> None:
    for f in candidates:
        current = into.get(f.key)
        if current is None or f.confidence > current.confidence:
            into[f.key] = f


def _currency(ctx: Ctx, fields: dict[str, Found]) -> Found | None:
    if "currency" not in ctx.schema.keys or "currency" in fields:
        return None
    dollars = {c for ln in ctx.doc.visible for c in DOLLAR_CODES if re.search(rf"\b{c}\b", ln.text)}
    for key in (ctx.schema.total, *ctx.schema.keys):
        f = fields.get(key or "")
        if f is None or f.currency is None or f.evidence is None:
            continue
        # "$" alone is read as USD unless the document names another dollar currency.
        currency = next(iter(dollars)) if f.currency == "USD" and len(dollars) == 1 else f.currency
        return replace(f, key="currency", value=currency, raw_text=f.raw_text, currency=None,
                       confidence=min(f.confidence, PATTERN))
    return None


def extract_fields(doc: Doc, doc_type: DocType, date_order: str) -> dict[str, Found]:
    ctx = Ctx(doc, SCHEMAS[doc_type], date_order)
    fields: dict[str, Found] = {}
    taken: set[tuple[str, int]] = set()
    for spec in ctx.schema.fields:
        if spec.value_type is not ValueType.BOOL:
            _merge(fields, filter(None, [label_value(ctx, spec, taken)]))
    for extractor in SPECIFIC[doc_type]:
        _merge(fields, extractor(ctx))
    if doc_type in KV_TYPES:
        _merge(fields, key_values(ctx, taken))
    currency = _currency(ctx, fields)
    if currency:
        fields["currency"] = currency
    return {k: f for k, f in fields.items() if k in ctx.schema.keys or k.startswith(KV_PREFIX)}
