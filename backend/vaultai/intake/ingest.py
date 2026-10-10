"""Bring one uploaded file into VaultAI: preflight, dedupe, store, and record. Never raises for bad input."""

from __future__ import annotations

import hashlib
import sqlite3
import uuid

from vaultai import db
from vaultai.context import AppContext
from vaultai.domain import AuditAction, DocStatus, DocType, IntakeItem, PreflightStatus, Source
from vaultai.intake import preflight
from vaultai.intake.filenames import display_name
from vaultai.logging_utils import get_logger
from vaultai.storage import Storage

log = get_logger("vaultai.intake")

_ITEM_SQL = (
    "SELECT i.*, d.status AS doc_status, d.doc_type AS doc_type, d.stage AS stage "
    "FROM intake i LEFT JOIN documents d ON d.id = i.document_id WHERE i.id = ?"
)


def _item(conn: sqlite3.Connection, intake_id: int) -> IntakeItem:
    r = conn.execute(_ITEM_SQL, (intake_id,)).fetchone()
    return IntakeItem(
        id=r["id"], batch_id=r["batch_id"], filename=r["filename"], source=r["source"], mime=r["mime"],
        size_bytes=r["size_bytes"], page_count=r["page_count"], preflight=r["preflight"], error=r["error"],
        duplicate_of=r["duplicate_of"], document_id=r["document_id"], doc_status=r["doc_status"],
        doc_type=r["doc_type"], stage=r["stage"], created_at=r["created_at"],
    )


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
        return _item(conn, intake_id)


def ingest_bytes(ctx: AppContext, *, filename: str, data: bytes, source: Source, batch_id: str,
                 doc_type_hint: DocType | None = None) -> IntakeItem:
    name = display_name(filename)
    actor = ctx.settings.reviewer_name
    verdict = preflight.static_check(ctx.settings, name, data)
    sha = hashlib.sha256(data).hexdigest() if not isinstance(verdict, preflight.Rejected) else None
    if sha:
        with ctx.db() as conn:
            existing = conn.execute("SELECT id FROM documents WHERE sha256 = ?", (sha,)).fetchone()
        if existing:
            return _duplicate(ctx, batch_id, name, source, len(data), existing["id"])
        verdict = preflight.probe_check(ctx.settings, verdict, data)

    if isinstance(verdict, preflight.Rejected):
        log.info("preflight rejected size=%d", len(data))
        with ctx.db() as conn, db.transaction(conn):
            intake_id = _intake(conn, batch_id=batch_id, name=name, source=source, mime=None, size=len(data),
                                pages=None, outcome=PreflightStatus.REJECTED, error=verdict.reason)
            db.audit(conn, AuditAction.PREFLIGHT_REJECT, actor, note=f"{name}: {verdict.reason}")
            return _item(conn, intake_id)

    doc_id = uuid.uuid4().hex
    stored = Storage.new_name(verdict.ext)
    path = ctx.storage.write_new(ctx.storage.raw, stored, data)
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
            db.audit(conn, AuditAction.IMPORT, actor, doc_id=doc_id, after=DocStatus.IMPORTED.value, note=source.value)
            item = _item(conn, intake_id)
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
