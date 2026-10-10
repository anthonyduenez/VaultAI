"""Minimal deterministic PDF writer for sample and test fixtures.

Writes real text-layer PDFs (Type1 base fonts, WinAnsi encoding) with no
timestamps, so the same input always produces the same bytes and sha256.
Coordinates are in points with the origin at the top-left of the page.
"""

from __future__ import annotations

import zlib

LETTER = (612.0, 792.0)
FONTS = {"F1": "Helvetica", "F2": "Helvetica-Bold", "F3": "Courier", "F4": "Times-Italic"}

Color = tuple[float, float, float]
BLACK: Color = (0, 0, 0)
WHITE: Color = (1, 1, 1)
GRAY: Color = (0.45, 0.45, 0.45)


def _escape(text: str) -> bytes:
    raw = text.encode("cp1252", errors="replace")
    return raw.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


def _num(v: float) -> bytes:
    return f"{v:.2f}".rstrip("0").rstrip(".").encode() or b"0"


class Page:
    def __init__(self, width: float, height: float) -> None:
        self.width = width
        self.height = height
        self._ops: list[bytes] = []

    def _y(self, y: float) -> float:
        return self.height - y

    def text(
        self,
        x: float,
        y: float,
        s: str,
        size: float = 10,
        font: str = "F1",
        color: Color = BLACK,
    ) -> None:
        r, g, b = color
        self._ops.append(
            b"BT /%s %s Tf %s %s %s rg %s %s Td (%s) Tj ET"
            % (font.encode(), _num(size), _num(r), _num(g), _num(b), _num(x), _num(self._y(y)), _escape(s))
        )

    def text_right(
        self, x_right: float, y: float, s: str, size: float = 10, font: str = "F1", color: Color = BLACK
    ) -> None:
        self.text(x_right - text_width(s, size, font), y, s, size, font, color)

    def text_center(
        self, x_center: float, y: float, s: str, size: float = 10, font: str = "F1", color: Color = BLACK
    ) -> None:
        self.text(x_center - text_width(s, size, font) / 2, y, s, size, font, color)

    def paragraph(
        self,
        x: float,
        y: float,
        s: str,
        size: float = 10,
        width: float = 470,
        leading: float | None = None,
        font: str = "F1",
        color: Color = BLACK,
    ) -> float:
        """Wrap and draw text; return the y of the next line."""
        leading = leading or size * 1.35
        for line in wrap(s, size, width, font):
            self.text(x, y, line, size, font, color)
            y += leading
        return y

    def line(self, x1: float, y1: float, x2: float, y2: float, width: float = 0.75, color: Color = BLACK) -> None:
        r, g, b = color
        self._ops.append(
            b"q %s w %s %s %s RG %s %s m %s %s l S Q"
            % (_num(width), _num(r), _num(g), _num(b), _num(x1), _num(self._y(y1)), _num(x2), _num(self._y(y2)))
        )

    def rect(
        self, x: float, y: float, w: float, h: float, stroke: Color | None = BLACK, fill: Color | None = None, width: float = 0.75
    ) -> None:
        ops = [b"q", b"%s w" % _num(width)]
        if stroke:
            ops.append(b"%s %s %s RG" % tuple(_num(c) for c in stroke))
        if fill:
            ops.append(b"%s %s %s rg" % tuple(_num(c) for c in fill))
        paint = b"B" if stroke and fill else (b"f" if fill else b"S")
        ops.append(b"%s %s %s %s re %s Q" % (_num(x), _num(self._y(y + h)), _num(w), _num(h), paint))
        self._ops.append(b" ".join(ops))

    def signature(self, x: float, y: float, w: float = 140) -> None:
        """A handwriting-like stroke used to mark a signed signature line."""
        y0 = self._y(y)
        pts = [x, y0, x + w * 0.2, y0 + 18, x + w * 0.3, y0 - 12, x + w * 0.45, y0 + 4]
        pts2 = [x + w * 0.6, y0 + 16, x + w * 0.75, y0 - 10, x + w, y0 + 6]
        self._ops.append(
            b"q 1.2 w 0.1 0.15 0.45 RG %s %s m %s %s %s %s %s %s c %s %s %s %s %s %s c S Q"
            % tuple(_num(v) for v in pts[:2] + pts[2:8] + pts2)
        )

    def content(self) -> bytes:
        return b"\n".join(self._ops)


class PdfDoc:
    def __init__(self) -> None:
        self.pages: list[Page] = []

    def page(self, size: tuple[float, float] = LETTER) -> Page:
        p = Page(*size)
        self.pages.append(p)
        return p

    def to_bytes(self, compress: bool = True, extra_catalog: bytes = b"") -> bytes:
        objects: list[bytes] = []

        def add(body: bytes) -> int:
            objects.append(body)
            return len(objects)

        catalog = add(b"")
        pages_obj = add(b"")
        font_refs = {name: add(b"<< /Type /Font /Subtype /Type1 /BaseFont /%s /Encoding /WinAnsiEncoding >>" % base.encode())
                     for name, base in FONTS.items()}
        fonts = b" ".join(b"/%s %d 0 R" % (n.encode(), ref) for n, ref in font_refs.items())
        kids = []
        for p in self.pages:
            data = p.content()
            if compress:
                data = zlib.compress(data, 9)
                stream = add(b"<< /Length %d /Filter /FlateDecode >>\nstream\n%s\nendstream" % (len(data), data))
            else:
                stream = add(b"<< /Length %d >>\nstream\n%s\nendstream" % (len(data), data))
            kids.append(add(
                b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 %s %s] /Resources << /Font << %s >> >> /Contents %d 0 R >>"
                % (pages_obj, _num(p.width), _num(p.height), fonts, stream)
            ))
        objects[catalog - 1] = b"<< /Type /Catalog /Pages %d 0 R %s>>" % (pages_obj, extra_catalog + b" " if extra_catalog else b"")
        objects[pages_obj - 1] = b"<< /Type /Pages /Kids [%s] /Count %d >>" % (
            b" ".join(b"%d 0 R" % k for k in kids), len(kids))

        out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets = []
        for i, body in enumerate(objects, start=1):
            offsets.append(len(out))
            out += b"%d 0 obj\n%s\nendobj\n" % (i, body)
        xref = len(out)
        out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
        for off in offsets:
            out += b"%010d 00000 n \n" % off
        out += b"trailer\n<< /Size %d /Root %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, catalog, xref)
        return bytes(out)


# Adobe AFM advance widths (1/1000 em) for WinAnsi 32..126, so right-aligned and wrapped text lands where it renders.
_HELVETICA = """
278 278 355 556 556 889 667 191 333 333 389 584 278 333 278 278 556 556 556 556 556 556 556 556 556 556 278 278 584 584
584 556 1015 667 667 722 722 667 611 778 722 278 500 667 556 833 722 778 667 778 722 667 611 722 667 944 667 667 611 278
278 278 469 556 333 556 556 500 556 556 278 556 556 222 222 500 222 833 556 556 556 556 333 500 278 556 500 722 500 500
500 334 260 334 584
"""
_HELVETICA_BOLD = """
278 333 474 556 556 889 722 238 333 333 389 584 278 333 278 278 556 556 556 556 556 556 556 556 556 556 333 333 584 584
584 611 975 722 722 722 722 667 611 778 722 278 556 722 611 833 722 778 667 778 722 667 611 722 667 944 667 667 611 333
278 333 584 556 333 556 611 556 611 556 333 611 611 278 278 556 278 889 611 611 611 611 389 556 333 611 556 778 556 556
500 389 280 389 584
"""
_AFM = {
    font: dict(zip(map(chr, range(32, 127)), map(int, table.split())))
    for font, table in (("F1", _HELVETICA), ("F2", _HELVETICA_BOLD))
}
_EM = {"F3": 0.6, "F4": 0.45}


def text_width(s: str, size: float, font: str = "F1") -> float:
    afm = _AFM.get(font)
    if afm is None:
        return len(s) * size * _EM.get(font, 0.5)
    return sum(afm.get(c, 556) for c in s) * size / 1000


def wrap(s: str, size: float, width: float, font: str = "F1") -> list[str]:
    lines: list[str] = []
    current = ""
    for word in s.split():
        trial = f"{current} {word}" if current else word
        if current and text_width(trial, size, font) > width:
            lines.append(current)
            current = word
        else:
            current = trial
    return [*lines, current] if current else lines or [""]
