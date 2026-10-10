"""Decide whether an uploaded file may enter VaultAI, before anything is stored.

Static checks are pure byte inspection: size, an explicit allowlist of
extensions, magic bytes (client MIME types are ignored), PDF encryption and
active content, and image dimensions read from headers before any decode.
The page count comes from the sandboxed parser. Every rejection is a short
fixed sentence that never repeats file content.
"""

from __future__ import annotations

import os
import re
import secrets
import unicodedata
import zlib
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from vaultai.config import Settings
from vaultai.intake.filenames import extension
from vaultai.parsing import isolate
from vaultai.parsing.layout import TEXT_LINES_PER_PAGE

MIME = {"pdf": "application/pdf", "png": "image/png", "jpg": "image/jpeg", "tiff": "image/tiff", "txt": "text/plain"}
SIGNATURES: tuple[tuple[str, bytes], ...] = (
    ("pdf", b"%PDF-"),
    ("png", b"\x89PNG\r\n\x1a\n"),
    ("jpg", b"\xff\xd8\xff"),
    ("tiff", b"II*\x00"),
    ("tiff", b"MM\x00*"),
)
FOREIGN: tuple[bytes, ...] = (
    b"MZ", b"\x7fELF", b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xca\xfe\xba\xbe", b"PK\x03\x04", b"Rar!",
    b"7z\xbc\xaf", b"\x1f\x8b", b"#!", b"GIF8", b"\xd0\xcf\x11\xe0", b"BM",
)
ACTIVE_PDF_NAMES = frozenset({b"JavaScript", b"JS", b"Launch", b"EmbeddedFile", b"EmbeddedFiles", b"RichMedia", b"XFA"})
MAX_TEXT_BYTES = 2 * 1024 * 1024
MIN_PRINTABLE = 0.95
_STREAM_BUDGET = 64 * 1024 * 1024
_STREAM_MAX = 8 * 1024 * 1024

EMPTY = "The file is empty."
TOO_LARGE = "The file is larger than the upload limit."
UNSUPPORTED = "This file type is not supported. Use PDF, PNG, JPEG, TIFF, or plain text."
SPOOFED = "The file's contents do not match its extension."
INVALID = {
    "pdf": "The file is not a valid PDF.",
    "png": "The file is not a valid PNG image.",
    "jpg": "The file is not a valid JPEG image.",
    "tiff": "The file is not a valid TIFF image.",
}
NOT_TEXT = "The text file is not valid UTF-8 text."
TEXT_TOO_LARGE = "Text files are limited to 2 MB."
ENCRYPTED = "Password-protected PDFs are not supported."
ACTIVE = "The PDF contains active content (scripts, launch actions, embedded files, or dynamic forms)."
TOO_MANY_PIXELS = "The image is larger than the pixel limit."
TOO_MANY_PAGES = "The document has more pages than the limit."
NO_PAGES = "The document has no pages."
UNSAFE = "The file could not be parsed safely."


@dataclass(frozen=True)
class Rejected:
    reason: str


@dataclass(frozen=True)
class Sniffed:
    ext: str
    mime: str


@dataclass(frozen=True)
class Accepted:
    ext: str
    mime: str
    page_count: int


def sniff(data: bytes) -> str | None:
    kind = next((k for k, magic in SIGNATURES if data.startswith(magic)), None)
    if kind:
        return kind
    return "foreign" if any(data.startswith(m) for m in FOREIGN) else None


_NAME = re.compile(rb"/([^\s/<>\[\]()%{}]+)")
_STREAM = re.compile(rb"stream\r?\n")


def _unescape(name: bytes) -> bytes:
    return re.sub(rb"#([0-9A-Fa-f]{2})", lambda m: bytes([int(m.group(1), 16)]), name)


def pdf_names(data: bytes) -> set[bytes]:
    chunks = [data]
    budget = _STREAM_BUDGET
    for m in _STREAM.finditer(data):
        if budget <= 0:
            break
        end = data.find(b"endstream", m.end())
        if end == -1:
            break
        try:
            inflated = zlib.decompressobj().decompress(data[m.end():end], min(_STREAM_MAX, budget))
        except zlib.error:
            continue
        budget -= len(inflated)
        chunks.append(inflated)
    return {_unescape(m.group(1)) for chunk in chunks for m in _NAME.finditer(chunk)}


def png_size(data: bytes) -> tuple[int, int] | None:
    if len(data) < 24 or data[12:16] != b"IHDR":
        return None
    return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")


_SOF = frozenset({0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF})


def jpeg_size(data: bytes) -> tuple[int, int] | None:
    i = 2
    while i + 4 <= len(data):
        if data[i] != 0xFF:
            return None
        marker = data[i + 1]
        if marker == 0xFF:
            i += 1
            continue
        if marker in (0x01, *range(0xD0, 0xDA)):
            i += 2
            continue
        length = int.from_bytes(data[i + 2:i + 4], "big")
        if marker in _SOF:
            if i + 9 > len(data):
                return None
            return int.from_bytes(data[i + 7:i + 9], "big"), int.from_bytes(data[i + 5:i + 7], "big")
        i += 2 + length
    return None


def _text_ok(data: bytes) -> bool:
    if b"\x00" in data:
        return False
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return False
    if not text.strip():
        return False
    printable = sum(1 for ch in text if ch in "\t\n\r\f" or unicodedata.category(ch)[0] != "C")
    return printable / len(text) >= MIN_PRINTABLE


def static_check(settings: Settings, name: str, data: bytes) -> Sniffed | Rejected:
    if not data:
        return Rejected(EMPTY)
    if len(data) > settings.max_file_bytes:
        return Rejected(TOO_LARGE)
    ext = extension(name)
    if ext is None:
        return Rejected(UNSUPPORTED)
    kind = sniff(data)
    if ext == "txt":
        if kind is not None:
            return Rejected(SPOOFED)
        if len(data) > MAX_TEXT_BYTES:
            return Rejected(TEXT_TOO_LARGE)
        return Sniffed(ext, MIME[ext]) if _text_ok(data) else Rejected(NOT_TEXT)
    if kind != ext:
        return Rejected(SPOOFED if kind else INVALID[ext])
    if ext == "pdf":
        names = pdf_names(data)
        if b"Encrypt" in names:
            return Rejected(ENCRYPTED)
        if settings.reject_active_pdf and names & ACTIVE_PDF_NAMES:
            return Rejected(ACTIVE)
    if ext in ("png", "jpg"):
        size = png_size(data) if ext == "png" else jpeg_size(data)
        if size is None or 0 in size:
            return Rejected(INVALID[ext])
        if size[0] * size[1] > settings.max_image_pixels:
            return Rejected(TOO_MANY_PIXELS)
    return Sniffed(ext, MIME[ext])


@contextmanager
def staged(settings: Settings, ext: str, data: bytes) -> Iterator[Path]:
    """The upload in an owner-only temp file for the parser to probe. Deleted on exit; hard-link it to keep it."""
    settings.tmp_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = settings.tmp_dir / f"probe-{secrets.token_hex(8)}.{ext}"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        yield path
    finally:
        path.unlink(missing_ok=True)


def probe_check(settings: Settings, sniffed: Sniffed, data: bytes, path: Path) -> Accepted | Rejected:
    if sniffed.ext == "txt":
        pages = -(-len(data.decode("utf-8-sig").splitlines() or [""]) // TEXT_LINES_PER_PAGE)
        return Rejected(TOO_MANY_PAGES) if pages > settings.max_pages else Accepted(sniffed.ext, sniffed.mime, pages)
    try:
        info = isolate.probe(settings, path, sniffed.ext)
    except isolate.ParseFailed:
        return Rejected(UNSAFE)
    if info.get("active") and settings.reject_active_pdf:
        return Rejected(ACTIVE)
    if info.get("too_large"):
        return Rejected(TOO_MANY_PIXELS)
    pages = int(info.get("pages", 0))
    if pages < 1:
        return Rejected(NO_PAGES)
    if pages > settings.max_pages:
        return Rejected(TOO_MANY_PAGES)
    return Accepted(sniffed.ext, sniffed.mime, pages)
