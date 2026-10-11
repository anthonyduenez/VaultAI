"""What the model sees: a small, structured, citable view of one document.

Every line keeps its id and its exact text, so a quote the model copies can be
checked against the stored line. Structure is added around lines, never inside
them: the prompt renders page, table, clause, signature, and column notes from
the attributes set here. Quarantined lines (hidden or injection) never appear.

The view also fixes what plain top-to-bottom order gets wrong:
  * two-column blocks, such as an invoice header with "Bill To" on the left and
    "Invoice Number" on the right, are read one column at a time, so each label
    sits next to its own value. Both halves keep the line's id;
  * page numbers, "continued" notes, and headers or footers repeated on every
    page are dropped after their first appearance.

Selection functions then pick the few lines a task needs instead of sending the
whole document: the lines near each missing field's labels, lines holding a
value of the right type, and the document's opening lines.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace

from vaultai.analysis.contracts import CLOSING, clauses, is_heading
from vaultai.extraction.rules import ORG_SUFFIX
from vaultai.domain import LEGAL_DOC_TYPES, ClauseKind, DocType, ValueType
from vaultai.extraction.normalize import find_dates, find_money
from vaultai.model.base import DocLine
from vaultai.parsing.layout import Doc, Line, Span, page_of
from vaultai.parsing.tables import DELIVERABLE_COLUMNS, LINE_ITEM_COLUMNS, LINE_ITEM_STOP, find_tables
from vaultai.schemas import FieldSpec

COLUMN_GAP = "   "
EDGE_ZONE = 0.08
LOW_OCR = 0.75
GUTTER_MIN = 0.04
GUTTER_RANGE = (0.15, 0.85)
MIN_BLOCK_LINES = 3
REPEAT_MIN_CHARS = 25
LINE_COST = 12

_PAGE_NUMBER = re.compile(r"^(?:page\s+)?\d{1,4}(?:\s*(?:of|/)\s*\d{1,4})?$|^-\s*\d{1,4}\s*-$", re.I)
_CONTINUED = re.compile(r"^\(?continued(?: on (?:the )?next page)?\)?\.?$", re.I)
_LABEL_LIKE = re.compile(r"^[A-Za-z][\w .'/#&()-]{0,30}:")
_SIGNATURE_START = re.compile(r"\b(in witness whereof|accepted and agreed|agreed and accepted|acknowledged and agreed)\b"
                              r"|^authorized signature\b", re.I)
_IDENTIFIER = re.compile(r"(?<![\w/])(?=[\w#/-]*\d)[A-Za-z0-9][\w#/-]{2,}(?![\w/])")
_ISSUER_KEYS = frozenset({"vendor", "merchant", "payer", "buyer", "sender", "sender_organization", "organization",
                          "provider", "client", "title", "form_title", "parties", "recipient"})
_SIGNATURE_KEYS = frozenset({"signature_status", "signature_present", "signatories", "sender", "recipient"})


def _spaced(spans: Sequence[Span], fallback: str) -> str:
    return COLUMN_GAP.join(s.text for s in spans) if len(spans) > 1 else fallback


def _shape(text: str) -> str:
    return re.sub(r"\d+", "#", " ".join(text.casefold().split()))


def _in_edge(line: Line, index: int, count: int) -> bool:
    if line.bbox:
        return line.bbox[1] < EDGE_ZONE or line.bbox[3] > 1 - EDGE_ZONE
    return index < 2 or index >= count - 2


def _furniture(doc: Doc) -> set[str]:
    """Ids of page numbers, continuation notes, and lines repeated from an earlier page.

    A header or footer counts as repeated when it matches with digits ignored ("Page 2", a date); a body line
    only when its text is the same and long enough not to be an ordinary short row.
    """
    drop: set[str] = set()
    edges: set[str] = set()
    bodies: set[str] = set()
    for page in doc.pages:
        rows = [ln for ln in page.lines if ln.flag is None]
        here_edges: set[str] = set()
        here_bodies: set[str] = set()
        for i, ln in enumerate(rows):
            text = ln.text.strip()
            exact = " ".join(text.casefold().split())
            edge = _in_edge(ln, i, len(rows))
            if _PAGE_NUMBER.match(text) or _CONTINUED.match(text):
                drop.add(ln.id)
            elif (edge and _shape(text) in edges) or (len(text) >= REPEAT_MIN_CHARS and exact in bodies):
                drop.add(ln.id)
            if edge:
                here_edges.add(_shape(text))
            here_bodies.add(exact)
        edges |= here_edges
        bodies |= here_bodies
    return drop


def _free(line: Line) -> list[tuple[float, float]]:
    """Horizontal gaps inside the gutter range that no span covers and that do not split a label from its value."""
    lo, hi = GUTTER_RANGE
    gaps: list[tuple[float, float]] = []
    cursor = lo
    spans = sorted(line.spans, key=lambda s: s.x0)
    for i, s in enumerate(spans):
        if s.x0 > cursor:
            before = spans[i - 1] if i else None
            splits_label = before is not None and before.text.rstrip().endswith(":") and not _LABEL_LIKE.match(s.text)
            if not splits_label:
                gaps.append((cursor, min(s.x0, hi)))
        cursor = max(cursor, s.x1)
    if cursor < hi:
        gaps.append((cursor, hi))
    return [(a, b) for a, b in gaps if b - a >= GUTTER_MIN]


def _intersect(a: list[tuple[float, float]], b: list[tuple[float, float]]) -> list[tuple[float, float]]:
    out = [(max(x0, y0), min(x1, y1)) for x0, x1 in a for y0, y1 in b]
    return [(x0, x1) for x0, x1 in out if x1 - x0 >= GUTTER_MIN]


def _sides(line: Line, gutter: tuple[float, float]) -> tuple[list[Span], list[Span]]:
    mid = (gutter[0] + gutter[1]) / 2
    left = [s for s in line.spans if (s.x0 + s.x1) / 2 < mid]
    return left, [s for s in line.spans if s not in left]


def _blocks(rows: list[Line], skip: set[str]) -> list[tuple[int, int, tuple[float, float]]]:
    """(start, end, gutter) for runs of lines that read as two side-by-side columns."""
    out = []
    i = 0
    while i < len(rows):
        if rows[i].id in skip or not rows[i].spans:
            i += 1
            continue
        free = _free(rows[i])
        j = i + 1
        while j < len(rows) and rows[j].id not in skip and rows[j].spans:
            nxt = _intersect(free, _free(rows[j]))
            if not nxt:
                break
            free, j = nxt, j + 1
        if free and j - i >= MIN_BLOCK_LINES:
            gutter = max(free, key=lambda g: g[1] - g[0])
            two_sided = sum(all(_sides(ln, gutter)) for ln in rows[i:j])
            if two_sided >= 2:
                out.append((i, j, gutter))
                i = j
                continue
        i += 1
    return out


def _table_rows(doc: Doc) -> dict[str, str]:
    """Line id -> column description, for every line-item or deliverable table header and row."""
    out: dict[str, str] = {}
    for roles, required, stop in ((LINE_ITEM_COLUMNS, frozenset({"description", "amount"}), LINE_ITEM_STOP),
                                  (DELIVERABLE_COLUMNS, frozenset({"description", "due"}), re.compile(r"$^"))):
        for table in find_tables(doc, roles, required, stop):
            columns = " | ".join(c.role.replace("_", " ") for c in table.columns)
            for ln in (table.header, *(r.line for r in table.rows)):
                out.setdefault(ln.id, columns)
    return out


def _clause_heads(doc: Doc, doc_type: DocType) -> dict[str, str]:
    if doc_type in LEGAL_DOC_TYPES:
        return {c.evidence.line_id: c.kind.value.replace("_", " ") for c in clauses(doc)
                if c.evidence and c.evidence.line_id and c.kind is not ClauseKind.SIGNATURES}
    return {ln.id: "section" for ln in doc.visible if is_heading(ln)}


def view(doc: Doc, doc_type: DocType) -> tuple[DocLine, ...]:
    """Every visible line in reading order, annotated, without page furniture."""
    drop = _furniture(doc)
    tables = _table_rows(doc)
    heads = _clause_heads(doc, doc_type)
    out: list[DocLine] = []
    for page in doc.pages:
        ocr = page.text_method in ("ocr", "vision")
        rows = [ln for ln in page.lines if ln.flag is None and ln.id not in drop]
        signing = False
        pieces: list[tuple[Line, Sequence[Span], str | None]] = []
        blocks = {start: (end, gutter) for start, end, gutter in _blocks(rows, set(tables))}
        i = 0
        while i < len(rows):
            if i in blocks:
                end, gutter = blocks[i]
                block = rows[i:end]
                pieces += [(ln, left, "left") for ln in block if (left := _sides(ln, gutter)[0])]
                pieces += [(ln, right, "right") for ln in block if (right := _sides(ln, gutter)[1])]
                i = end
                continue
            pieces.append((rows[i], rows[i].spans, None))
            i += 1
        for line, spans, column in pieces:
            signing = signing or bool(_SIGNATURE_START.search(line.text) or CLOSING.match(line.text.strip()))
            out.append(DocLine(
                id=line.id,
                text=_spaced(spans, line.text if column is None else spans[0].text),
                page=page_of(line.id),
                ocr=line.confidence if ocr and line.confidence is not None and line.confidence < LOW_OCR else None,
                table=tables.get(line.id),
                clause=heads.get(line.id),
                signature=signing,
                column=column,
                position=len(out),
            ))
    return tuple(out)


def cost(line: DocLine) -> int:
    return len(line.id) + len(line.text) + LINE_COST


def _fit(lines: Iterable[DocLine], budget: int) -> list[DocLine]:
    out, used = [], 0
    for ln in lines:
        if used + cost(ln) > budget:
            continue
        out.append(ln)
        used += cost(ln)
    return out


def for_classification(lines: Sequence[DocLine], budget: int, first_page_lines: int = 40, later_page_lines: int = 5
                       ) -> tuple[DocLine, ...]:
    """The opening of page 1 and the top of every later page: titles, issuers, and repeated headers decide the type."""
    by_page: dict[int, int] = {}
    keep = []
    for ln in lines:
        n = by_page.get(ln.page, 0)
        if n < (first_page_lines if ln.page == 1 else later_page_lines):
            keep.append(ln)
        by_page[ln.page] = n + 1
    return tuple(_fit(keep, budget))


def _has_word(word: str, text: str) -> bool:
    return bool(re.search(rf"(?<!\w){re.escape(word)}(?!\w)", text))


def typed_candidate(value_type: ValueType, text: str, date_order: str = "MDY") -> bool:
    if value_type is ValueType.MONEY:
        return bool(find_money(text, require_marker=True))
    if value_type is ValueType.DATE:
        return any(h.value for h in find_dates(text, date_order))
    if value_type is ValueType.IDENTIFIER:
        return bool(_IDENTIFIER.search(text))
    return False


def for_fields(lines: Sequence[DocLine], fields: Sequence[FieldSpec], budget: int, date_order: str = "MDY"
               ) -> tuple[DocLine, ...]:
    """The lines most likely to hold the wanted fields, best first until the budget, returned in reading order."""
    labels = [[w.casefold() for w in (*spec.aliases, spec.label) if w] for spec in fields]
    keys = {spec.key for spec in fields}
    score = [0.0] * len(lines)
    for i, ln in enumerate(lines):
        low = ln.text.casefold()
        for spec, words in zip(fields, labels):
            if any(_has_word(w, low) for w in words):
                score[i] += 3
                for j, bonus in ((i + 1, 2.0), (i + 2, 1.0), (i - 1, 1.0)):
                    if 0 <= j < len(lines) and lines[j].page == ln.page:
                        score[j] += bonus
            if typed_candidate(spec.value_type, ln.text, date_order):
                score[i] += 1.5
        if ln.page in (0, 1) and ln.position < 6:
            score[i] += 3 if keys & _ISSUER_KEYS else 1
        if keys & _ISSUER_KEYS and ORG_SUFFIX.search(ln.text):
            score[i] += 1.5
        if ln.signature and keys & _SIGNATURE_KEYS:
            score[i] += 2
        if ln.table and not ln.clause:
            score[i] -= 1
    # Best lines first; whatever budget is left goes to the rest in reading order, so a short document is sent whole.
    ranked = sorted(range(len(lines)), key=lambda i: (-(score[i] > 0), -score[i] if score[i] > 0 else 0, -(score[i] == 0), i))
    chosen = {ln.position for ln in _fit((lines[i] for i in ranked), budget)}
    return tuple(ln for ln in lines if ln.position in chosen)


_OBLIGATION_CUE = re.compile(
    r"\b(shall|must|will|agrees?|agreed|required?|responsible|due|deadline|no later than|within|on or before|prior to|"
    r"renew\w*|terminat\w*|notice|pay\w*|fees?|deliver\w*|expir\w*|effective|sign\w*|accept\w*|submit\w*|complete)\b",
    re.I)


def for_analysis(lines: Sequence[DocLine], doc: Doc, date_order: str = "MDY") -> tuple[DocLine, ...]:
    """Paragraphs that state a duty, a date, an amount, renewal, termination, or signature, plus their headings."""
    from vaultai.analysis.contracts import paragraphs

    para_of: dict[str, int] = {}
    for n, para in enumerate(paragraphs(doc)):
        for ln in para:
            para_of[ln.id] = n
    wanted_paras = {para_of[ln.id] for ln in lines if ln.id in para_of and (
        _OBLIGATION_CUE.search(ln.text) or find_money(ln.text, require_marker=True)
        or any(h.value for h in find_dates(ln.text, date_order)))}
    keep: list[DocLine] = []
    for ln in lines:
        if ln.clause or ln.signature or para_of.get(ln.id) in wanted_paras or (ln.page in (0, 1) and ln.position < 4):
            keep.append(ln)
    return tuple(keep)


def chunks(lines: Sequence[DocLine], budget: int) -> list[tuple[DocLine, ...]]:
    """Greedy chunks under `budget`, preferring to break before a clause heading once a chunk is half full."""
    out: list[tuple[DocLine, ...]] = []
    current: list[DocLine] = []
    used = 0
    for ln in lines:
        c = cost(ln)
        if current and (used + c > budget or (ln.clause and used > budget // 2)):
            out.append(tuple(current))
            current, used = [], 0
        current.append(ln)
        used += c
    if current:
        out.append(tuple(current))
    return out


def plain(lines: Iterable[Line]) -> tuple[DocLine, ...]:
    """Lines without layout notes, for callers that only have a flat list."""
    return tuple(replace(DocLine(ln.id, ln.text), page=page_of(ln.id), position=i)
                 for i, ln in enumerate(lines) if ln.flag is None)
