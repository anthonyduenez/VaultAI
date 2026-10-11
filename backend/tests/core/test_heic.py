"""iPhone HEIC photos take the same path as JPEG photos.

The fixtures were made on macOS by rendering samples/scanner/receipt_harbor_fuel.pdf at 200 dpi onto gray, saving it
as JPEG (once stored sideways with EXIF Orientation 6), and converting that, a blank 8064x6048 JPEG, and a 64x64 PNG
with `sips -s format heic` (or `avif`). two_images_second_primary.heic holds a white 64x48 and a black 40x80 image,
written by ImageIO, with its pitm box patched to make the second one primary.
Regenerate with fixtures/make_fixtures.py and fixtures/two_images.swift (macOS only).
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

from core.conftest import ctx_with, doc, ingest, lines, png_bytes, run

FIXTURES = Path(__file__).with_name("fixtures")
RECEIPT = (FIXTURES / "receipt_harbor_fuel.heic").read_bytes()
ROTATED = (FIXTURES / "receipt_harbor_fuel_rotated.heic").read_bytes()
HUGE = (FIXTURES / "blank_8064x6048.heic").read_bytes()
AVIF = (FIXTURES / "gray_64x64.avif").read_bytes()
TWO_IMAGES = (FIXTURES / "two_images_second_primary.heic").read_bytes()

UNSUPPORTED = "This file type is not supported. Use PDF, PNG, JPEG, HEIC, TIFF, or plain text."
SPOOFED = "The file's contents do not match its extension."

needs_tesseract = pytest.mark.skipif(shutil.which("tesseract") is None, reason="tesseract is not installed")


def _pages(ctx, doc_id: str) -> list[tuple[int, float, float]]:
    with ctx.db() as conn:
        return [tuple(r) for r in conn.execute("SELECT page_no, width, height FROM pages WHERE doc_id = ? ORDER BY page_no",
                                               (doc_id,))]


def test_heic_and_heif_photos_are_accepted_under_the_users_name(mock_ctx):
    items = [
        ingest(mock_ctx, "IMG_0001.HEIC", RECEIPT),
        ingest(mock_ctx, "scan.heif", ROTATED),
        ingest(mock_ctx, "generic-brand.heic", RECEIPT.replace(b"ftypheic", b"ftypmif1", 1)),
    ]
    assert [(i.preflight.value, i.filename, i.mime, i.page_count) for i in items] == [
        ("accepted", "IMG_0001.HEIC", "image/heic", 1),
        ("accepted", "scan.heif", "image/heic", 1),
        ("accepted", "generic-brand.heic", "image/heic", 1),
    ]
    assert sorted(p.suffix for p in mock_ctx.storage.raw.iterdir()) == [".heic", ".heic", ".heic"]


def test_sideways_heic_renders_as_one_portrait_page(mock_ctx):
    doc_id = run(mock_ctx, "IMG_0002.HEIC", ROTATED)
    assert _pages(mock_ctx, doc_id) == [(1, 920.0, 1843.0)]
    assert [p.name for p in mock_ctx.storage.derived_doc_dir(doc_id).iterdir()] == ["p1.png"]


def test_only_the_primary_image_of_a_heic_becomes_a_page(mock_ctx):
    doc_id = run(mock_ctx, "IMG_0005.HEIC", TWO_IMAGES)
    assert (doc(mock_ctx, doc_id)["page_count"], _pages(mock_ctx, doc_id)) == (1, [(1, 40.0, 80.0)])


@needs_tesseract
def test_heic_receipt_photo_is_read_by_ocr(settings):
    ctx = ctx_with(settings, ocr_engine="tesseract")
    doc_id = run(ctx, "IMG_0001.HEIC", RECEIPT)
    with ctx.db() as conn:
        method = conn.execute("SELECT text_method FROM pages WHERE doc_id = ?", (doc_id,)).fetchone()[0]
    row = doc(ctx, doc_id)
    assert (row["doc_type"], row["counterparty"], row["total_amount"], method) == (
        "receipt", "HARBOR FUEL & MART", "61.40", "ocr")


@needs_tesseract
def test_rotated_heic_is_read_upright(settings):
    ctx = ctx_with(settings, ocr_engine="tesseract")
    doc_id = run(ctx, "IMG_0002.HEIC", ROTATED)
    assert lines(ctx, doc_id)[0]["text"] == "HARBOR FUEL & MART"


def test_heic_over_the_pixel_limit_is_rejected(mock_ctx):
    item = ingest(mock_ctx, "IMG_0004.HEIC", HUGE)
    assert (item.preflight.value, item.error, list(mock_ctx.storage.raw.iterdir())) == (
        "rejected", "The image is larger than the pixel limit.", [])


@pytest.mark.parametrize(
    ("name", "data", "error"),
    [
        ("photo.avif", AVIF, UNSUPPORTED),
        ("photo.heic", AVIF, UNSUPPORTED),
        ("photo.heif", b"\0\0\0\x18ftypmif1\0\0\0\0mif1avif" + b"\0" * 64, UNSUPPORTED),
        ("photo.heic", RECEIPT.replace(b"ftypheic\0\0\0\0heicmif1", b"ftypmif1\0\0\0\0miafmif1", 1),
         "The file is not a valid HEIC image."),
        ("photo.jpg", RECEIPT, SPOOFED),
        ("photo.heic", png_bytes(fmt="JPEG"), SPOOFED),
    ],
    ids=["avif", "avif-named-heic", "mif1-avif-brand", "mif1-without-hevc-brand", "heic-named-jpg", "jpeg-named-heic"],
)
def test_non_hevc_and_mislabeled_heif_files_are_rejected(mock_ctx, name, data, error):
    item = ingest(mock_ctx, name, data)
    assert (item.preflight.value, item.error) == ("rejected", error)


def test_heic_is_decoded_only_in_the_parser_subprocess(mock_ctx):
    doc_id = run(mock_ctx, "IMG_0001.HEIC", RECEIPT)
    assert (_pages(mock_ctx, doc_id), "pi_heif" in sys.modules) == ([(1, 920.0, 1843.0)], False)
