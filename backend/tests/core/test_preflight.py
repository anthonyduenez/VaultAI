from __future__ import annotations

import struct
import zlib
from dataclasses import replace

import pytest

from core.conftest import contract_pdf, ingest, invoice_pdf, png_bytes
from vaultai.domain import PreflightStatus


def _accepted(ctx, name: str, data: bytes) -> tuple[str, int | None, str | None]:
    item = ingest(ctx, name, data)
    return item.preflight.value, item.page_count, item.mime


def test_supported_types_are_accepted_with_sniffed_mime_and_page_count(mock_ctx):
    assert _accepted(mock_ctx, "invoice.pdf", invoice_pdf()) == ("accepted", 1, "application/pdf")
    assert _accepted(mock_ctx, "scan.png", png_bytes()) == ("accepted", 1, "image/png")
    assert _accepted(mock_ctx, "photo.JPEG", png_bytes(fmt="JPEG")) == ("accepted", 1, "image/jpeg")
    assert _accepted(mock_ctx, "fax.tif", png_bytes(fmt="TIFF", frames=3)) == ("accepted", 3, "image/tiff")
    assert _accepted(mock_ctx, "notes.txt", ("line\n" * 61).encode()) == ("accepted", 2, "text/plain")


@pytest.mark.parametrize(
    ("name", "data", "error"),
    [
        ("report.docx", b"PK\x03\x04" + b"\0" * 64, "This file type is not supported. Use PDF, PNG, JPEG, TIFF, or plain text."),
        ("invoice.pdf", b"MZ\x90\x00" + b"\0" * 200, "The file's contents do not match its extension."),
        ("image.jpg", png_bytes(), "The file's contents do not match its extension."),
        ("notes.txt", b"%PDF-1.4 not really text", "The file's contents do not match its extension."),
        ("invoice.pdf", b"hello, I am not a pdf", "The file is not a valid PDF."),
        ("empty.pdf", b"", "The file is empty."),
        ("latin1.txt", "caf\xe9 cr\xe8me".encode("latin-1"), "The text file is not valid UTF-8 text."),
        ("nul.txt", b"abc\x00def", "The text file is not valid UTF-8 text."),
    ],
)
def test_bad_types_are_rejected_with_a_fixed_reason(mock_ctx, name, data, error):
    item = ingest(mock_ctx, name, data)
    assert (item.preflight, item.error, item.document_id) == (PreflightStatus.REJECTED, error, None)


def test_oversized_file_is_rejected_before_parsing(settings):
    from conftest import make_ctx

    ctx = make_ctx(replace(settings, max_file_mb=1))
    item = ingest(ctx, "big.pdf", b"%PDF-1.4\n" + b"0" * (1024 * 1024 + 1))
    assert (item.preflight, item.error) == (PreflightStatus.REJECTED, "The file is larger than the upload limit.")


def test_truncated_pdf_is_rejected_as_unsafe(mock_ctx):
    data = invoice_pdf()
    item = ingest(mock_ctx, "cut.pdf", data[: len(data) // 3])
    assert (item.preflight, item.error) == (PreflightStatus.REJECTED, "The file could not be parsed safely.")


def _png_header(width: int, height: int) -> bytes:
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    chunk = b"IHDR" + ihdr
    return b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + chunk + struct.pack(">I", zlib.crc32(chunk)) + b"\0" * 32


def test_decompression_bomb_header_is_rejected_without_decoding(mock_ctx):
    item = ingest(mock_ctx, "bomb.png", _png_header(60000, 60000))
    assert (item.preflight, item.error) == (PreflightStatus.REJECTED, "The image is larger than the pixel limit.")


def _with_catalog_extra(extra: bytes) -> bytes:
    data = invoice_pdf()
    return data.replace(b"<< /Type /Catalog", b"<< /Type /Catalog " + extra, 1)


@pytest.mark.parametrize(
    "extra",
    [
        b"/OpenAction << /S /JavaScript /JS (app.alert(1)) >>",
        b"/OpenAction << /S /J#61vaScript /J#53 (app.alert(1)) >>",
        b"/OpenAction << /S /Launch /F (calc.exe) >>",
        b"/Names << /EmbeddedFiles 99 0 R >>",
    ],
)
def test_active_content_pdfs_are_rejected(mock_ctx, extra):
    item = ingest(mock_ctx, "active.pdf", _with_catalog_extra(extra))
    assert item.error == "The PDF contains active content (scripts, launch actions, embedded files, or dynamic forms)."


def test_active_content_is_allowed_when_policy_is_off(settings):
    from conftest import make_ctx

    ctx = make_ctx(replace(settings, reject_active_pdf=False))
    item = ingest(ctx, "active.pdf", _with_catalog_extra(b"/OpenAction << /S /JavaScript /JS (x) >>"))
    assert item.preflight is PreflightStatus.ACCEPTED


def test_encrypted_pdf_is_rejected(mock_ctx):
    data = invoice_pdf().replace(b"trailer\n<<", b"trailer\n<< /Encrypt 99 0 R", 1)
    item = ingest(mock_ctx, "locked.pdf", data)
    assert item.error == "Password-protected PDFs are not supported."


def test_too_many_pages_is_rejected(settings):
    from conftest import make_ctx

    ctx = make_ctx(replace(settings, max_pages=1))
    item = ingest(ctx, "fax.tiff", png_bytes(fmt="TIFF", frames=2))
    assert item.error == "The document has more pages than the limit."


def test_rejection_stores_nothing_and_audits_only_name_and_reason(mock_ctx):
    item = ingest(mock_ctx, "../../evil.pdf", b"MZ" + b"\0" * 100)
    with mock_ctx.db() as conn:
        audit = [dict(r) for r in conn.execute("SELECT action, note, doc_id FROM audit_log")]
        docs = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    assert (item.filename, docs, list(mock_ctx.storage.raw.iterdir())) == ("evil.pdf", 0, [])
    assert audit == [{"action": "preflight_reject", "note": "evil.pdf: The file's contents do not match its extension.",
                      "doc_id": None}]


def test_contract_pdf_passes_preflight(mock_ctx):
    assert ingest(mock_ctx, "msa.pdf", contract_pdf()).preflight is PreflightStatus.ACCEPTED
