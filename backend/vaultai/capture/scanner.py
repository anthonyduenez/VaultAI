"""Desktop scanning: SANE devices through scanimage, or a mock feeder for demos, plus scanner capture sessions."""

from __future__ import annotations

import io
import random
import re
import secrets
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Literal, Protocol

from PIL import Image, ImageDraw

from vaultai import db, repo
from vaultai.capture.assemble import apply_edit, capture_filename, ingest_pages, open_image
from vaultai.config import Settings
from vaultai.context import AppContext
from vaultai.domain import (
    AssembleRequest,
    AuditAction,
    CaptureKind,
    CaptureStatus,
    IntakeItem,
    PreflightStatus,
    ScanRequest,
    ScannerDevice,
    Source,
)
from vaultai.repo import Problem
from vaultai.storage import Storage

SCAN_TIMEOUT_S = 120
LIST_TIMEOUT_S = 30
# A scan validates its device against this listing; reprobing would add a USB/network round trip to every page.
LISTING_TTL_S = 30
MAX_SESSION_PAGES = 100
SCAN_FAILED = Problem(502, "scan_failed", "The scanner did not return a page. Check the paper and try again.")
SANE_DEVICE = re.compile(r"^device `(?P<id>[^']+)' is a (?P<name>.+)$", re.MULTILINE)


class ScannerBackend(Protocol):
    name: Literal["sane", "mock"]

    def devices(self) -> list[ScannerDevice]: ...

    def has_device(self, device_id: str) -> bool: ...

    def scan(self, device_id: str, request: ScanRequest) -> list[Image.Image]: ...


def _run(argv: list[str], timeout: int) -> bytes:
    try:
        result = subprocess.run(argv, capture_output=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise SCAN_FAILED from None
    if result.returncode != 0:
        raise SCAN_FAILED
    return result.stdout


class SaneScanner:
    name: Literal["sane"] = "sane"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._listing: tuple[float, dict[str, str]] | None = None

    @staticmethod
    def available() -> bool:
        return shutil.which("scanimage") is not None

    def _probe(self) -> dict[str, str]:
        out = _run(["scanimage", "-L"], LIST_TIMEOUT_S).decode("utf-8", errors="replace")
        listed = {m["id"]: m["name"].strip() for m in SANE_DEVICE.finditer(out)}
        self._listing = (time.monotonic(), listed)
        return listed

    def _listed(self) -> dict[str, str]:
        if self._listing is not None and time.monotonic() - self._listing[0] < LISTING_TTL_S:
            return self._listing[1]
        return self._probe()

    def _duplex(self, device_id: str) -> bool:
        try:
            return b"Duplex" in _run(["scanimage", "-d", device_id, "-A"], LIST_TIMEOUT_S)
        except Problem:
            return False

    def devices(self) -> list[ScannerDevice]:
        return [
            ScannerDevice(id=i, name=n, backend="sane", duplex=self._duplex(i)) for i, n in self._probe().items()
        ]

    def has_device(self, device_id: str) -> bool:
        return device_id in self._listed()

    def scan(self, device_id: str, request: ScanRequest) -> list[Image.Image]:
        if not self.has_device(device_id):
            raise repo.not_found()
        argv = [
            "scanimage", "-d", device_id, "--format=png", "--resolution", str(request.dpi),
            "--mode", "Color" if request.color == "color" else "Gray",
        ]
        if not request.duplex:
            blobs = [_run(argv, SCAN_TIMEOUT_S)]
        else:
            with tempfile.TemporaryDirectory(dir=self.settings.tmp_dir) as tmp:
                _run([*argv, "--source", "ADF Duplex", f"--batch={tmp}/page-%03d.png", "--batch-count=2"],
                     SCAN_TIMEOUT_S)
                blobs = [p.read_bytes() for p in sorted(Path(tmp).glob("page-*.png"))]
        images = [open_image(b, self.settings.max_image_pixels, ("PNG",)) for b in blobs if b]
        if not images:
            raise SCAN_FAILED
        return images


MOCK_DEVICE = ScannerDevice(id="mock:feeder", name="VaultAI demo scanner (mock)", backend="mock", duplex=True)
# pdfium is not thread-safe; the core only uses it in subprocesses, so this lock covers every in-process caller.
_PDFIUM_LOCK = threading.Lock()


def scanned_look(image: Image.Image, seed: int) -> Image.Image:
    """A slight deterministic skew and speckle so demo pages look like they came off a feeder."""
    rng = random.Random(seed)
    white = 255 if image.mode == "L" else (255, 255, 255)
    out = image.rotate(rng.uniform(-0.8, 0.8), resample=Image.Resampling.BICUBIC, fillcolor=white)
    draw = ImageDraw.Draw(out)
    for _ in range(out.width * out.height // 4000):
        shade = rng.randrange(140, 225)
        draw.point((rng.randrange(out.width), rng.randrange(out.height)),
                   fill=shade if out.mode == "L" else (shade, shade, shade))
    return out


class MockScanner:
    name: Literal["mock"] = "mock"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._cursor = 0
        self._lock = threading.Lock()

    def _pages(self) -> list[tuple[Path, int]]:
        import pypdfium2 as pdfium

        folder = self.settings.samples_dir / "scanner"
        pages: list[tuple[Path, int]] = []
        with _PDFIUM_LOCK:
            for path in sorted(folder.glob("*.pdf")) if folder.is_dir() else []:
                pdf = pdfium.PdfDocument(path)
                pages.extend((path, i) for i in range(len(pdf)))
                pdf.close()
        return pages

    def _render(self, path: Path, index: int, request: ScanRequest, seed: int) -> Image.Image:
        import pypdfium2 as pdfium

        with _PDFIUM_LOCK:
            pdf = pdfium.PdfDocument(path)
            try:
                image = pdf[index].render(scale=request.dpi / 72, grayscale=request.color == "gray").to_pil()
            finally:
                pdf.close()
        return scanned_look(image.convert("L" if request.color == "gray" else "RGB"), seed)

    def devices(self) -> list[ScannerDevice]:
        return [MOCK_DEVICE]

    def has_device(self, device_id: str) -> bool:
        return device_id == MOCK_DEVICE.id

    def scan(self, device_id: str, request: ScanRequest) -> list[Image.Image]:
        if not self.has_device(device_id):
            raise repo.not_found()
        pages = self._pages()
        if not pages:
            raise Problem(503, "scanner_empty", "The demo scanner has no sample pages to feed.")
        count = 2 if request.duplex else 1
        with self._lock:
            start = self._cursor
            self._cursor += count
        return [self._render(*pages[n % len(pages)], request, seed=n) for n in range(start, start + count)]


BACKENDS: dict[str, Callable[[Settings], ScannerBackend | None]] = {
    "auto": lambda s: SaneScanner(s) if SaneScanner.available() else MockScanner(s),
    "sane": lambda s: SaneScanner(s) if SaneScanner.available() else None,
    "mock": MockScanner,
    "none": lambda s: None,
}


def build_scanner(settings: Settings) -> ScannerBackend | None:
    return BACKENDS[settings.scanner_backend](settings)


def backend_names(settings: Settings) -> list[str]:
    scanner = build_scanner(settings)
    return [scanner.name] if scanner is not None else []


# ---------------------------------------------------------------- sessions


def _png(image: Image.Image) -> bytes:
    buf = io.BytesIO()
    image.save(buf, "PNG")
    return buf.getvalue()


def _session_dir(ctx: AppContext, session_id: str) -> Path:
    return ctx.storage.resolve(ctx.storage.captures, session_id)


def _open_session(conn: sqlite3.Connection, session_id: str) -> sqlite3.Row:
    row = repo.capture_row(conn, session_id)
    if row["kind"] != CaptureKind.SCANNER.value:
        raise repo.not_found()
    if row["status"] != CaptureStatus.ACTIVE.value:
        raise Problem(409, "session_closed", "This scan session is already finished.")
    return row


def _page_row(conn: sqlite3.Connection, session_id: str, page_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM capture_pages WHERE id = ? AND session_id = ?", (page_id, session_id)).fetchone()
    if row is None:
        raise repo.not_found()
    return row


def create_session(conn: sqlite3.Connection, scanner: ScannerBackend | None, device_id: str, *, now: datetime) -> str:
    if scanner is None or not scanner.has_device(device_id):
        raise repo.not_found()
    session_id = secrets.token_urlsafe(16)
    conn.execute(
        "INSERT INTO capture_sessions (id, kind, status, device_id, created_at) VALUES (?,?,?,?,?)",
        (session_id, CaptureKind.SCANNER.value, CaptureStatus.ACTIVE.value, device_id, db.iso(now)),
    )
    return session_id


def scan_into(
    ctx: AppContext, scanner: ScannerBackend | None, session_id: str, request: ScanRequest, *, now: datetime
) -> None:
    with ctx.db() as conn:
        row = _open_session(conn, session_id)
        have = conn.execute("SELECT COUNT(*) FROM capture_pages WHERE session_id = ?", (session_id,)).fetchone()[0]
    if have + (2 if request.duplex else 1) > MAX_SESSION_PAGES:
        raise Problem(422, "too_many_pages", f"A scan session holds at most {MAX_SESSION_PAGES} pages.")
    if scanner is None:
        raise repo.not_found()
    images = scanner.scan(row["device_id"], request)
    names = [(ctx.storage.write_new(_session_dir(ctx, session_id), Storage.new_name("png"), _png(img)).name, img)
             for img in images]
    with ctx.db() as conn, db.transaction(conn):
        _open_session(conn, session_id)
        next_idx = conn.execute(
            "SELECT COALESCE(MAX(idx) + 1, 0) FROM capture_pages WHERE session_id = ?", (session_id,)
        ).fetchone()[0]
        for offset, (name, img) in enumerate(names):
            conn.execute(
                "INSERT INTO capture_pages (session_id, idx, stored_name, width, height, created_at) VALUES (?,?,?,?,?,?)",
                (session_id, next_idx + offset, name, img.width, img.height, db.iso(now)),
            )


def retake(
    ctx: AppContext, scanner: ScannerBackend | None, session_id: str, page_id: int, request: ScanRequest
) -> None:
    with ctx.db() as conn:
        row = _open_session(conn, session_id)
        _page_row(conn, session_id, page_id)
    if scanner is None:
        raise repo.not_found()
    image = scanner.scan(row["device_id"], request.model_copy(update={"duplex": False}))[0]
    name = ctx.storage.write_new(_session_dir(ctx, session_id), Storage.new_name("png"), _png(image)).name
    with ctx.db() as conn, db.transaction(conn):
        old = _page_row(conn, session_id, page_id)
        conn.execute(
            "UPDATE capture_pages SET stored_name = ?, width = ?, height = ? WHERE id = ?",
            (name, image.width, image.height, page_id),
        )
    ctx.storage.resolve(_session_dir(ctx, session_id), old["stored_name"]).unlink(missing_ok=True)


def delete_page(ctx: AppContext, conn: sqlite3.Connection, session_id: str, page_id: int) -> None:
    _open_session(conn, session_id)
    page = _page_row(conn, session_id, page_id)
    with db.transaction(conn):
        conn.execute("DELETE FROM capture_pages WHERE id = ?", (page_id,))
        conn.execute("UPDATE capture_pages SET idx = idx - 1 WHERE session_id = ? AND idx > ?", (session_id, page["idx"]))
    ctx.storage.resolve(_session_dir(ctx, session_id), page["stored_name"]).unlink(missing_ok=True)


def page_path(ctx: AppContext, conn: sqlite3.Connection, session_id: str, page_id: int) -> Path:
    repo.capture_row(conn, session_id)
    path = ctx.storage.resolve(_session_dir(ctx, session_id), _page_row(conn, session_id, page_id)["stored_name"])
    if not path.is_file():
        raise repo.not_found()
    return path


def _discard_pages(ctx: AppContext, conn: sqlite3.Connection, session_id: str) -> None:
    conn.execute("DELETE FROM capture_pages WHERE session_id = ?", (session_id,))
    shutil.rmtree(_session_dir(ctx, session_id), ignore_errors=True)


def cancel(ctx: AppContext, conn: sqlite3.Connection, session_id: str) -> None:
    row = repo.capture_row(conn, session_id)
    if row["kind"] != CaptureKind.SCANNER.value:
        raise repo.not_found()
    with db.transaction(conn):
        if row["status"] == CaptureStatus.ACTIVE.value:
            conn.execute("UPDATE capture_sessions SET status = ? WHERE id = ?", (CaptureStatus.CANCELLED.value, session_id))
        _discard_pages(ctx, conn, session_id)


def assemble(ctx: AppContext, session_id: str, request: AssembleRequest, *, now: datetime) -> IntakeItem:
    with ctx.db() as conn:
        _open_session(conn, session_id)
        pages = {r["id"]: r for r in conn.execute("SELECT * FROM capture_pages WHERE session_id = ?", (session_id,))}
    ids = [edit.id for edit in request.pages]
    if len(set(ids)) != len(ids) or not set(ids) <= pages.keys():
        raise Problem(422, "invalid_pages", "Each page must belong to this session and appear once.")
    folder = _session_dir(ctx, session_id)
    images = [
        apply_edit(
            open_image(ctx.storage.resolve(folder, pages[e.id]["stored_name"]).read_bytes(),
                       ctx.settings.max_image_pixels, ("PNG",)),
            e.rotation, e.crop,
        )
        for e in request.pages
    ]
    item = ingest_pages(
        ctx, images, filename=capture_filename(request.title, "Scan", now), source=Source.SCANNER,
        doc_type_hint=request.doc_type_hint,
    )
    if item.preflight is PreflightStatus.ACCEPTED:
        with ctx.db() as conn, db.transaction(conn):
            conn.execute(
                "UPDATE capture_sessions SET status = ?, intake_id = ?, document_id = ? WHERE id = ?",
                (CaptureStatus.COMPLETED.value, item.id, item.document_id, session_id),
            )
            _discard_pages(ctx, conn, session_id)
            db.audit(conn, AuditAction.CAPTURE, ctx.settings.reviewer_name, doc_id=item.document_id,
                     note=f"Scanner capture, {len(images)} pages")
    return item
