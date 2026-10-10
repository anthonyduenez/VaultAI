"""Bring one uploaded file into VaultAI: preflight, dedupe, store, and record. Never raises for bad input."""

from __future__ import annotations

import hashlib
import os
import sqlite3
import uuid
from pathlib import Path

from vaultai import db, repo
from vaultai.context import AppContext
from vaultai.domain import AuditAction, DocStatus, DocType, IntakeItem, PreflightStatus, Source
from vaultai.intake import preflight
from vaultai.intake.filenames import display_name
from vaultai.logging_utils import get_logger
from vaultai.storage import Storage

log = get_logger("vaultai.intake")


def _intake(conn: sqlite3.Connection, *, batch_id: str, name: str, source: Source, mime: str | None, size: int,
            pages: int | None, outcome: PreflightStatus, error: str | None = None, duplicate_of: str | None = None,
            document_id: str | None = None) -> int:
    cur = conn.execute(
        "INSERT INTO intake (batch_id, filename, source, mime, size_bytes, page_count, preflight, error, duplicate_of, "
        "document_id, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (batch_id, name, source.value, mime, size, pages, outcome.value, error, duplicate_of, document_id, db.iso()),
    )
    return int(cur.lastrowid)


def _duplicate(ctx: AppContext, batch_id: str, name: str, source: Source, size: int, existing_id: str) -> IntakeItem:
    with ctx.db() as conn, db.transaction(conn):
        row = conn.execute("SELECT mime, page_count FROM documents WHERE id = ?", (existing_id,)).fetchone()
        intake_id = _intake(conn, batch_id=batch_id, name=name, source=source, mime=row["mime"] if row else None,
                            size=size, pages=row["page_count"] if row else None, outcome=PreflightStatus.DUPLICATE,
                            duplicate_of=existing_id)
        return repo.get_intake(conn, intake_id)


def _rejected(ctx: AppContext, batch_id: str, name: str, source: Source, size: int, reason: str) -> IntakeItem:
    log.info("preflight rejected size=%d", size)
    with ctx.db() as conn, db.transaction(conn):
        intake_id = _intake(conn, batch_id=batch_id, name=name, source=source, mime=None, size=size, pages=None,
                            outcome=PreflightStatus.REJECTED, error=reason)
        db.audit(conn, AuditAction.PREFLIGHT_REJECT, ctx.settings.reviewer_name, note=f"{name}: {reason}")
        return repo.get_intake(conn, intake_id)


def _store(ctx: AppContext, staged: Path, stored: str, data: bytes) -> Path:
    """Move the probed file into raw storage. A hard link never replaces an existing file."""
    path = ctx.storage.resolve(ctx.storage.raw, stored)
    try:
        os.link(staged, path)
    except OSError:
        # Filesystems without hard links (exFAT, some network shares) get a fresh write; O_EXCL never overwrites either.
        return ctx.storage.write_new(ctx.storage.raw, stored, data)
    return path


def ingest_bytes(ctx: AppContext, *, filename: str, data: bytes, source: Source, batch_id: str,
                 doc_type_hint: DocType | None = None) -> IntakeItem:
    name = display_name(filename)
    sniffed = preflight.static_check(ctx.settings, name, data)
    if isinstance(sniffed, preflight.Rejected):
        return _rejected(ctx, batch_id, name, source, len(data), sniffed.reason)
    sha = hashlib.sha256(data).hexdigest()
    with ctx.db() as conn:
        existing = conn.execute("SELECT id FROM documents WHERE sha256 = ?", (sha,)).fetchone()
    if existing:
        return _duplicate(ctx, batch_id, name, source, len(data), existing["id"])

    with preflight.staged(ctx.settings, sniffed.ext, data) as staged:
        verdict = preflight.probe_check(ctx.settings, sniffed, data, staged)
        if isinstance(verdict, preflight.Rejected):
            return _rejected(ctx, batch_id, name, source, len(data), verdict.reason)
        stored = Storage.new_name(verdict.ext)
        path = _store(ctx, staged, stored, data)

    doc_id = uuid.uuid4().hex
    now = db.iso()
    try:
        with ctx.db() as conn, db.transaction(conn):
            conn.execute(
                "INSERT INTO documents (id, filename, stored_name, sha256, size_bytes, mime, page_count, source, status, "
                "doc_type_hint, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (doc_id, name, stored, sha, len(data), verdict.mime, verdict.page_count, source.value,
                 DocStatus.IMPORTED.value, doc_type_hint.value if doc_type_hint else None, now, now),
            )
            intake_id = _intake(conn, batch_id=batch_id, name=name, source=source, mime=verdict.mime, size=len(data),
                                pages=verdict.page_count, outcome=PreflightStatus.ACCEPTED, document_id=doc_id)
            db.audit(conn, AuditAction.IMPORT, ctx.settings.reviewer_name, doc_id=doc_id,
                     after=DocStatus.IMPORTED.value, note=source.value)
            item = repo.get_intake(conn, intake_id)
    except sqlite3.IntegrityError:
        # A concurrent upload of the same bytes won the UNIQUE(sha256) race.
        path.unlink(missing_ok=True)
        with ctx.db() as conn:
            existing = conn.execute("SELECT id FROM documents WHERE sha256 = ?", (sha,)).fetchone()
        if existing is None:
            raise
        return _duplicate(ctx, batch_id, name, source, len(data), existing["id"])
    log.info("imported doc=%s pages=%d", doc_id, verdict.page_count)
    return item


def _read_sample(path: Path, limit: int) -> bytes:
    # O_NOFOLLOW closes the gap between the symlink check and the open; one byte past the
    # limit lets preflight reject an oversized sample instead of importing a truncated one.
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "rb") as fh:
        return fh.read(limit + 1)


def ingest_samples(ctx: AppContext, *, batch_id: str) -> list[IntakeItem]:
    """Every regular, non-hidden file directly inside samples/demo, in name order. Symlinks are skipped."""
    demo = ctx.settings.samples_dir / "demo"
    if demo.is_symlink() or not demo.is_dir():
        return []
    return [
        ingest_bytes(ctx, filename=path.name, data=_read_sample(path, ctx.settings.max_file_bytes),
                     source=Source.SAMPLE, batch_id=batch_id)
        for path in sorted(demo.iterdir())
        if not path.name.startswith(".") and not path.is_symlink() and path.is_file()
    ]
