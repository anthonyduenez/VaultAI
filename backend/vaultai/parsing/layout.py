"""Page geometry: merge positioned text runs into lines with column spans.

Stdlib only, because the sandboxed parser worker imports it. Boxes are
normalized (x0, y0, x1, y1) in 0..1 with the origin at the top-left.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import cached_property

Box = tuple[float, float, float, float]

TEXT_LINES_PER_PAGE = 60
# A horizontal gap wider than this many line heights starts a new column span.
SPAN_GAP_HEIGHTS = 2.0


@dataclass(frozen=True)
class Word:
    text: str
    box: Box
    conf: float | None = None
    hidden: bool = False


@dataclass(frozen=True)
class Span:
    x0: float
    x1: float
    text: str


@dataclass(frozen=True)
class Line:
    id: str
    text: str
    bbox: Box | None
    confidence: float | None
    spans: tuple[Span, ...] = ()
    flag: str | None = None

    def to_json(self) -> dict:
        return {"id": self.id, "text": self.text, "bbox": list(self.bbox) if self.bbox else None,
                "confidence": self.confidence, "spans": [[s.x0, s.x1, s.text] for s in self.spans], "flag": self.flag}

    @staticmethod
    def from_json(d: dict) -> Line:
        return Line(
            id=d["id"], text=d["text"], bbox=tuple(d["bbox"]) if d.get("bbox") else None,
            confidence=d.get("confidence"), spans=tuple(Span(s[0], s[1], s[2]) for s in d.get("spans", ())),
            flag=d.get("flag"),
        )

    def page_line(self) -> dict:
        """The stored shape (domain.PageLine): no spans."""
        return {"id": self.id, "text": self.text, "bbox": list(self.bbox) if self.bbox else None,
                "confidence": self.confidence, "flag": self.flag}


@dataclass(frozen=True)
class Page:
    page_no: int
    width: float
    height: float
    text_method: str
    lines: tuple[Line, ...]
    image: str | None = None
    image_sha256: str | None = None
    ocr_confidence: float | None = None
    # Boxes of hand-drawn curves and small embedded images: how a signature shows up on a PDF page.
    marks: tuple[Box, ...] = ()


@dataclass
class _Row:
    hidden: bool
    words: list[Word] = field(default_factory=list)
    y0: float = 1.0
    y1: float = 0.0

    def add(self, w: Word) -> None:
        self.words.append(w)
        self.y0 = min(self.y0, w.box[1])
        self.y1 = max(self.y1, w.box[3])


def _same_row(row: _Row, w: Word) -> bool:
    if row.hidden != w.hidden:
        return False
    h = min(row.y1 - row.y0, w.box[3] - w.box[1])
    overlap = min(row.y1, w.box[3]) - max(row.y0, w.box[1])
    return h > 0 and overlap >= 0.5 * h


def _join(a: str, b: str) -> str:
    return f"{a}{b}" if not a or a.endswith(" ") or b.startswith(" ") else f"{a} {b}"


def merge_words(words: list[Word], page_no: int, aspect: float) -> list[Line]:
    """Group words that share a baseline into lines, top to bottom, and split each line into column spans.

    `aspect` is page height / width: normalized x and y use different page dimensions.
    """
    rows: list[_Row] = []
    for w in sorted((w for w in words if w.text.strip()), key=lambda w: (w.box[1] + w.box[3]) / 2):
        target = next((r for r in reversed(rows[-6:]) if _same_row(r, w)), None)
        if target is None:
            target = _Row(hidden=w.hidden)
            rows.append(target)
        target.add(w)
    rows.sort(key=lambda r: (r.y0 + r.y1) / 2)

    lines: list[Line] = []
    for row in rows:
        ws = sorted(row.words, key=lambda w: w.box[0])
        height = row.y1 - row.y0
        spans: list[list] = []
        for w in ws:
            if spans and w.box[0] - spans[-1][1] <= SPAN_GAP_HEIGHTS * height * aspect:
                spans[-1][1] = max(spans[-1][1], w.box[2])
                spans[-1][2] = _join(spans[-1][2], w.text)
            else:
                spans.append([w.box[0], w.box[2], w.text])
        span_objs = tuple(Span(round(s[0], 5), round(s[1], 5), " ".join(s[2].split())) for s in spans)
        text = " ".join(s.text for s in span_objs if s.text)
        if not text:
            continue
        confs = [w.conf for w in ws if w.conf is not None]
        lines.append(Line(
            id=f"p{page_no}.l{len(lines) + 1}",
            text=text,
            bbox=(round(min(w.box[0] for w in ws), 5), round(row.y0, 5), round(max(w.box[2] for w in ws), 5), round(row.y1, 5)),
            confidence=round(sum(confs) / len(confs), 4) if confs else 1.0,
            spans=span_objs,
            flag="hidden" if row.hidden else None,
        ))
    return lines


_TEXT_COLUMNS = 120.0
_COLUMN_GAP = re.compile(r"\S+(?: \S+)*")


def text_spans(raw: str) -> tuple[Span, ...]:
    """Plain-text columns: runs of two or more spaces separate spans; x is the character column / 120."""
    expanded = raw.expandtabs(8)
    return tuple(Span(round(m.start() / _TEXT_COLUMNS, 5), round(m.end() / _TEXT_COLUMNS, 5), m.group())
                 for m in _COLUMN_GAP.finditer(expanded))


def text_pages(text: str, per_page: int = TEXT_LINES_PER_PAGE) -> list[list[Line]]:
    """Plain text becomes pages of `per_page` source lines; blank lines count toward the page but are not kept."""
    raw = text.splitlines() or [""]
    pages: list[list[Line]] = []
    for page_no, start in enumerate(range(0, len(raw), per_page), start=1):
        spans = [sp for sp in (text_spans(r) for r in raw[start:start + per_page]) if sp]
        pages.append([
            Line(id=f"p{page_no}.l{i}", text=" ".join(s.text for s in sp), bbox=None, confidence=1.0, spans=sp)
            for i, sp in enumerate(spans, start=1)
        ])
    return pages


def page_of(line_id: str) -> int:
    return int(line_id[1:line_id.index(".")])


@dataclass(frozen=True)
class Doc:
    pages: tuple[Page, ...]

    @cached_property
    def lines(self) -> tuple[Line, ...]:
        return tuple(ln for p in self.pages for ln in p.lines)

    @cached_property
    def visible(self) -> tuple[Line, ...]:
        """Lines a person can see and that are not quarantined: the only text extraction reads."""
        return tuple(ln for ln in self.lines if ln.flag is None)

    @cached_property
    def by_id(self) -> dict[str, Line]:
        return {ln.id: ln for ln in self.lines}

    def page(self, page_no: int) -> Page:
        return self.pages[page_no - 1]

    def is_ocr(self, line: Line) -> bool:
        return self.page(page_of(line.id)).text_method in {"ocr", "vision"}
