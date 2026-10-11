"""Phone pairing sessions: a short-lived, single-use upload link shown as a QR code on the desktop.

The token travels only in the URL fragment and the X-Pairing-Token header and is
stored as its SHA-256. Any session that is unknown, expired, cancelled, or used
answers 404 so a phone cannot learn which sessions exist.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import socket
import sqlite3
import threading
from datetime import datetime, timedelta
from typing import Literal

import segno
from PIL import Image, ImageOps
from pydantic import TypeAdapter, ValidationError

from vaultai import db, repo
from vaultai.capture.assemble import apply_edit, capture_filename, ingest_pages, open_image
from vaultai.config import Settings
from vaultai.context import AppContext
from vaultai.domain import (
    ApiModel,
    AuditAction,
    Box,
    CaptureKind,
    CaptureSession,
    CaptureStatus,
    IntakeItem,
    PhonePairing,
    PreflightStatus,
    Source,
)
from vaultai.repo import Problem

IMAGE_MAGIC = (b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n")
LAN_PROBE = ("10.255.255.255", 1)
_submit_lock = threading.Lock()


class PhonePageEdit(ApiModel):
    rotation: Literal[0, 90, 180, 270] = 0
    crop: Box | None = None


_EDITS = TypeAdapter(list[PhonePageEdit])


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def lan_ip(settings: Settings) -> str:
    if settings.phone_host:
        return settings.phone_host
    try:
        # connect() on UDP only selects the outbound interface; no packet is sent.
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(LAN_PROBE)
            return str(probe.getsockname()[0])
    except OSError:
        return "127.0.0.1"


def qr_svg(url: str) -> str:
    return segno.make(url, error="m").svg_inline(scale=5)


def create_pairing(ctx: AppContext, conn: sqlite3.Connection, *, now: datetime) -> PhonePairing:
    settings = ctx.settings
    session_id = secrets.token_urlsafe(16)
    token = secrets.token_urlsafe(32)
    code = f"{secrets.randbelow(10**6):06d}"
    conn.execute(
        "INSERT INTO capture_sessions (id, kind, status, token_hash, code, created_at, expires_at) VALUES (?,?,?,?,?,?,?)",
        (session_id, CaptureKind.PHONE.value, CaptureStatus.ACTIVE.value, token_hash(token), code, db.iso(now),
         db.iso(now + timedelta(seconds=settings.phone_session_ttl_s))),
    )
    url = f"http://{lan_ip(settings)}:{settings.phone_port}/m/{session_id}#{token}"
    return PhonePairing(session=repo.get_capture_session(conn, session_id), url=url, code=code, qr_svg=qr_svg(url))


def _phone_row(conn: sqlite3.Connection, session_id: str) -> sqlite3.Row:
    row = repo.capture_row(conn, session_id)
    if row["kind"] != CaptureKind.PHONE.value:
        raise repo.not_found()
    return row


def _is_open(row: sqlite3.Row, now: datetime) -> bool:
    return row["status"] == CaptureStatus.ACTIVE.value and now < datetime.fromisoformat(row["expires_at"])


def desktop_view(conn: sqlite3.Connection, session_id: str, *, now: datetime) -> CaptureSession:
    row = _phone_row(conn, session_id)
    if row["status"] == CaptureStatus.ACTIVE.value and not _is_open(row, now):
        conn.execute("UPDATE capture_sessions SET status = ? WHERE id = ?", (CaptureStatus.EXPIRED.value, session_id))
    return repo.get_capture_session(conn, session_id)


def cancel(conn: sqlite3.Connection, session_id: str) -> None:
    _phone_row(conn, session_id)
    conn.execute(
        "UPDATE capture_sessions SET status = ? WHERE id = ? AND status = ?",
        (CaptureStatus.CANCELLED.value, session_id, CaptureStatus.ACTIVE.value),
    )


def open_session(conn: sqlite3.Connection, session_id: str, *, now: datetime) -> sqlite3.Row:
    """The session behind the mobile page; no token is available there because it lives in the URL fragment."""
    row = conn.execute(
        "SELECT * FROM capture_sessions WHERE id = ? AND kind = ?", (session_id, CaptureKind.PHONE.value)
    ).fetchone()
    if row is None or not _is_open(row, now):
        raise repo.not_found()
    return row


def verify(conn: sqlite3.Connection, session_id: str, token: str | None, *, now: datetime) -> sqlite3.Row:
    row = open_session(conn, session_id, now=now)
    if not token or not hmac.compare_digest(token_hash(token), row["token_hash"]):
        raise repo.not_found()
    return row


def status_payload(row: sqlite3.Row, settings: Settings) -> dict[str, object]:
    return {
        "status": row["status"],
        "code": row["code"],
        "max_pages": settings.phone_max_pages,
        "expires_at": row["expires_at"],
    }


def parse_edits(raw: str | None, count: int) -> list[PhonePageEdit]:
    try:
        edits = _EDITS.validate_json(raw or "")
    except ValidationError:
        raise Problem(422, "invalid_edits", "Page edits could not be read.") from None
    if len(edits) != count:
        raise Problem(422, "invalid_edits", "Send one edit entry per page.")
    return edits


def decode_photo(data: bytes, settings: Settings) -> Image.Image:
    if not data.startswith(IMAGE_MAGIC):
        raise Problem(422, "invalid_image", "Pages must be JPEG or PNG photos.")
    image = ImageOps.exif_transpose(open_image(data, settings.max_image_pixels, ("JPEG", "PNG")))
    return image if image.mode in {"RGB", "L"} else image.convert("RGB")


def submit(
    ctx: AppContext, session_id: str, token: str | None, pages: list[bytes], edits_raw: str | None, *, now: datetime
) -> IntakeItem:
    settings = ctx.settings
    with _submit_lock:
        with ctx.db() as conn:
            verify(conn, session_id, token, now=now)
        if not pages:
            raise Problem(422, "no_pages", "Take at least one photo.")
        if len(pages) > settings.phone_max_pages:
            raise Problem(422, "too_many_pages", f"Send at most {settings.phone_max_pages} pages.")
        if any(len(p) > settings.phone_max_image_mb * 1024 * 1024 for p in pages):
            raise Problem(422, "page_too_large", f"Each photo must be at most {settings.phone_max_image_mb} MB.")
        edits = parse_edits(edits_raw, len(pages))
        images = [apply_edit(decode_photo(p, settings), e.rotation, e.crop) for p, e in zip(pages, edits, strict=True)]
        item = ingest_pages(ctx, images, filename=capture_filename(None, "Phone capture", now), source=Source.PHONE)
        if item.preflight is PreflightStatus.ACCEPTED:
            with ctx.db() as conn, db.transaction(conn):
                conn.execute(
                    "UPDATE capture_sessions SET status = ?, intake_id = ?, document_id = ?, token_hash = NULL WHERE id = ?",
                    (CaptureStatus.COMPLETED.value, item.id, item.document_id, session_id),
                )
                db.audit(conn, AuditAction.CAPTURE, settings.reviewer_name, doc_id=item.document_id,
                         note=f"Phone capture, {len(images)} pages")
        return item
