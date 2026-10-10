"""Sandboxed parser for untrusted document bytes.

Run only as a script by vaultai.parsing.isolate (`python -I worker.py`), with a
minimal environment, a temp cwd, and a timeout. Resource limits are applied
here, before any untrusted byte is read, rather than through preexec_fn,
which is unsafe in the threaded server. Reads one JSON request on stdin and
writes one JSON object to stdout.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import resource
import sys
import warnings

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

MIN_CONTRAST = 40
MIN_TEXT_HEIGHT = 0.004
MAX_MARK_IMAGE_AREA = 0.25


def _limit(req: dict) -> None:
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    cpu = int(req["cpu_s"])
    resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu + 1))
    resource.setrlimit(resource.RLIMIT_FSIZE, (int(req["fsize_bytes"]), int(req["fsize_bytes"])))
    if sys.platform.startswith("linux"):
        # macOS does not enforce RLIMIT_AS; there the timeout and RLIMIT_CPU bound the parser.
        resource.setrlimit(resource.RLIMIT_AS, (int(req["mem_bytes"]), int(req["mem_bytes"])))


def _pil(max_pixels: int):
    from PIL import Image

    Image.MAX_IMAGE_PIXELS = max_pixels
    warnings.simplefilter("error", Image.DecompressionBombWarning)
    return Image


def probe_pdf(req: dict) -> dict:
    import pypdfium2 as pdfium
    import pypdfium2.raw as raw

    pdf = pdfium.PdfDocument(req["path"])
    js = raw.FPDFDoc_GetJavaScriptActionCount(pdf.raw)
    xfa = pdf.get_formtype() in (raw.FORMTYPE_XFA_FULL, raw.FORMTYPE_XFA_FOREGROUND)
    return {"ok": True, "pages": len(pdf), "active": bool(js > 0 or pdf.count_attachments() > 0 or xfa)}


def probe_image(req: dict) -> dict:
    Image = _pil(req["max_pixels"])
    with Image.open(req["path"]) as im:
        frames = getattr(im, "n_frames", 1)
        for i in range(min(frames, req["max_pages"] + 1)):
            im.seek(i)
            if im.size[0] * im.size[1] > req["max_pixels"]:
                return {"ok": True, "pages": frames, "too_large": True}
    with Image.open(req["path"]) as im:
        im.verify()
    return {"ok": True, "pages": frames, "too_large": False}


def _save_png(img, out_dir: str, page_no: int) -> tuple[str, str]:
    buf = io.BytesIO()
    img.save(buf, format="PNG", compress_level=6)
    data = buf.getvalue()
    name = f"p{page_no}.png"
    with open(os.path.join(out_dir, name), "xb") as fh:
        fh.write(data)
    return name, hashlib.sha256(data).hexdigest()


def _scale(width: float, height: float, dpi: int, max_pixels: int) -> float:
    s = dpi / 72.0
    if width * height * s * s > max_pixels:
        s = (max_pixels / (width * height)) ** 0.5
    return s


def _displayed(box: tuple[float, float, float, float], rotation: int) -> tuple[float, float, float, float]:
    u0, v0, u1, v1 = box
    if rotation == 90:
        return (1 - v1, u0, 1 - v0, u1)
    if rotation == 180:
        return (1 - u1, 1 - v1, 1 - u0, 1 - v0)
    if rotation == 270:
        return (v0, 1 - u1, v1, 1 - u0)
    return box


def _normalized(left: float, bottom: float, right: float, top: float, size: tuple[float, float], rotation: int
                ) -> tuple[float, float, float, float]:
    uw, uh = size
    return _displayed((left / uw, 1 - top / uh, right / uw, 1 - bottom / uh), rotation)


def _marks(page, size: tuple[float, float], rotation: int) -> list[tuple[float, float, float, float]]:
    import pypdfium2.raw as raw

    out = []
    for obj in page.get_objects(filter=(raw.FPDF_PAGEOBJ_PATH, raw.FPDF_PAGEOBJ_IMAGE), max_depth=3):
        box = _normalized(*obj.get_pos(), size, rotation)
        if obj.type == raw.FPDF_PAGEOBJ_IMAGE:
            if (box[2] - box[0]) * (box[3] - box[1]) <= MAX_MARK_IMAGE_AREA:
                out.append(box)
            continue
        segments = raw.FPDFPath_CountSegments(obj.raw)
        curves = sum(
            1 for i in range(max(segments, 0))
            if raw.FPDFPathSegment_GetType(raw.FPDFPath_GetPathSegment(obj.raw, i)) == raw.FPDF_SEGMENT_BEZIERTO
        )
        if curves >= 2:
            out.append(box)
    return out


def _visible(gray, box_px: tuple[int, int, int, int]) -> bool:
    lo, hi = gray.crop(box_px).getextrema()
    return hi - lo >= MIN_CONTRAST


def parse_pdf(req: dict) -> dict:
    import pypdfium2 as pdfium

    from vaultai.parsing.layout import Word, merge_words

    _pil(req["max_pixels"])
    pdf = pdfium.PdfDocument(req["path"])
    pages = []
    for index in range(min(len(pdf), req["max_pages"])):
        page_no = index + 1
        page = pdf[index]
        width, height = page.get_size()
        rotation = page.get_rotation()
        # Text and object positions are in unrotated page space; get_size() and the render are rotated.
        unrotated = (height, width) if rotation in (90, 270) else (width, height)
        scale = _scale(width, height, req["dpi"], req["max_pixels"])
        img = page.render(scale=scale).to_pil().convert("RGB")
        gray = img.convert("L")
        pw, ph = gray.size

        textpage = page.get_textpage()
        words = []
        for i in range(textpage.count_rects()):
            left, bottom, right, top = textpage.get_rect(i)
            text = textpage.get_text_bounded(left, bottom, right, top)
            if not text.strip():
                continue
            box = _normalized(left, bottom, right, top, unrotated, rotation)
            outside = box[2] <= 0 or box[0] >= 1 or box[3] <= 0 or box[1] >= 1
            tiny = (box[3] - box[1]) < MIN_TEXT_HEIGHT
            hidden = outside or tiny
            if not hidden:
                px = (max(0, int(box[0] * pw)), max(0, int(box[1] * ph)), min(pw, int(box[2] * pw) + 1), min(ph, int(box[3] * ph) + 1))
                hidden = px[2] <= px[0] or px[3] <= px[1] or not _visible(gray, px)
            words.append(Word(" ".join(text.split()), box, None, hidden))

        lines = merge_words(words, page_no, height / width)
        image, image_sha = _save_png(img, req["out_dir"], page_no)
        pages.append({
            "page_no": page_no, "width": width, "height": height,
            "text_method": "pdf_text" if any(ln.flag is None for ln in lines) else "none",
            "image": image, "image_sha256": image_sha, "lines": [ln.to_json() for ln in lines],
            "marks": [[round(v, 5) for v in m] for m in _marks(page, unrotated, rotation)],
        })
    return {"ok": True, "page_count": len(pdf), "pages": pages}


def parse_image(req: dict) -> dict:
    Image = _pil(req["max_pixels"])
    from PIL import ImageOps, ImageSequence

    pages = []
    with Image.open(req["path"]) as im:
        for index, frame in enumerate(ImageSequence.Iterator(im)):
            if index >= req["max_pages"]:
                break
            page_no = index + 1
            img = ImageOps.exif_transpose(frame.copy()).convert("RGB")
            w, h = img.size
            if w * h > req["max_pixels"]:
                f = (req["max_pixels"] / (w * h)) ** 0.5
                img = img.resize((max(1, int(w * f)), max(1, int(h * f))))
            name, sha = _save_png(img, req["out_dir"], page_no)
            pages.append({
                "page_no": page_no, "width": float(img.size[0]), "height": float(img.size[1]),
                "text_method": "none", "image": name, "image_sha256": sha, "lines": [],
            })
    return {"ok": True, "page_count": len(pages), "pages": pages}


def parse_text(req: dict) -> dict:
    from vaultai.parsing.layout import text_pages

    with open(req["path"], "rb") as fh:
        text = fh.read().decode("utf-8-sig")
    pages = []
    for page_no, lines in enumerate(text_pages(text)[: req["max_pages"]], start=1):
        body = "\n".join(ln.text for ln in lines)
        pages.append({
            "page_no": page_no, "width": 612.0, "height": 792.0, "text_method": "plain_text", "image": None,
            "image_sha256": hashlib.sha256(body.encode()).hexdigest(), "lines": [ln.to_json() for ln in lines],
        })
    return {"ok": True, "page_count": len(pages), "pages": pages}


HANDLERS = {
    ("probe", "pdf"): probe_pdf,
    ("probe", "image"): probe_image,
    ("parse", "pdf"): parse_pdf,
    ("parse", "image"): parse_image,
    ("parse", "text"): parse_text,
}


def main() -> int:
    req = json.loads(sys.stdin.read())
    _limit(req)
    sys.path.insert(0, BACKEND_DIR)
    try:
        out = HANDLERS[(req["mode"], req["kind"])](req)
    except Exception as exc:
        out = {"ok": False, "error": type(exc).__name__}
    sys.stdout.write(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
