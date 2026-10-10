"""Captured page images plus per-page edits become one PDF, which enters the normal intake path."""

from __future__ import annotations

import io
import re
import secrets
from datetime import datetime

from PIL import Image, UnidentifiedImageError

from vaultai.context import AppContext
from vaultai.domain import Box, DocType, IntakeItem, Source
from vaultai.intake.ingest import ingest_bytes
from vaultai.repo import Problem

ROTATIONS: dict[int, Image.Transpose | None] = {
    0: None,
    90: Image.Transpose.ROTATE_270,
    180: Image.Transpose.ROTATE_180,
    270: Image.Transpose.ROTATE_90,
}
MIN_CROP = 0.05
GRAY_MODES = frozenset({"1", "L", "LA", "I", "I;16"})
# One resolution covers every page (a Pillow limit); deriving it from the smallest page keeps every page at least
# letter size, whatever the scan dpi or phone camera.
PAGE_LONG_SIDE_IN = 11


def open_image(data: bytes, max_pixels: int, formats: tuple[str, ...]) -> Image.Image:
    Image.MAX_IMAGE_PIXELS = max_pixels
    try:
        image = Image.open(io.BytesIO(data), formats=list(formats))
        if image.width * image.height > max_pixels:
            raise Problem(422, "image_too_large", "The image has too many pixels.")
        image.load()
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, SyntaxError, ValueError):
        raise Problem(422, "invalid_image", "Pages must be JPEG or PNG images.") from None
    return image


def check_crop(crop: Box) -> Box:
    x0, y0, x1, y1 = crop
    if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1) or x1 - x0 < MIN_CROP or y1 - y0 < MIN_CROP:
        raise Problem(422, "invalid_crop", "A crop must stay inside the page and keep at least 5% of each side.")
    return crop


def apply_edit(image: Image.Image, rotation: int, crop: Box | None) -> Image.Image:
    """Rotation is clockwise and applied first; the crop is normalized to the rotated page, as the preview shows it."""
    transpose = ROTATIONS[rotation]
    out = image.transpose(transpose) if transpose is not None else image
    if crop is not None:
        x0, y0, x1, y1 = check_crop(crop)
        w, h = out.size
        out = out.crop((round(x0 * w), round(y0 * h), round(x1 * w), round(y1 * h)))
    return out


def _pdf_page(image: Image.Image) -> Image.Image:
    # convert() copies even to the same mode, which doubles peak memory across a stack of phone photos.
    mode = "L" if image.mode in GRAY_MODES else "RGB"
    return image if image.mode == mode else image.convert(mode)


def images_to_pdf(images: list[Image.Image]) -> bytes:
    pages = [_pdf_page(img) for img in images]
    dpi = max(36, round(min(max(p.size) for p in pages) / PAGE_LONG_SIDE_IN))
    buf = io.BytesIO()
    pages[0].save(buf, "PDF", save_all=True, append_images=pages[1:], resolution=dpi)
    return buf.getvalue()


def capture_filename(title: str | None, default: str, now: datetime) -> str:
    cleaned = re.sub(r"\s+", " ", re.sub(r"[^\w\s\-.,()&']", "", title or "")).strip()[:120]
    return f"{cleaned or default} {now.astimezone():%Y-%m-%d %H%M}.pdf"


def ingest_pages(
    ctx: AppContext,
    images: list[Image.Image],
    *,
    filename: str,
    source: Source,
    doc_type_hint: DocType | None = None,
) -> IntakeItem:
    return ingest_bytes(
        ctx, filename=filename, data=images_to_pdf(images), source=source, batch_id=secrets.token_hex(8),
        doc_type_hint=doc_type_hint,
    )
