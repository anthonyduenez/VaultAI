"""Deleting one document, or every byte VaultAI has stored. Audit rows describe what was removed."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

from vaultai import db, repo
from vaultai.context import AppContext
from vaultai.domain import AuditAction


def delete_document(ctx: AppContext, conn: sqlite3.Connection, doc_id: str, *, actor: str) -> None:
    """Rows cascade from documents; intake rows keep their history with document_id set null by the foreign key."""
    row = conn.execute("SELECT filename, sha256, stored_name FROM documents WHERE id = ?", (doc_id,)).fetchone()
    if row is None:
        raise repo.not_found()
    with db.transaction(conn):
        conn.execute("DELETE FROM doc_fts WHERE doc_id = ?", (doc_id,))
        conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
        db.audit(conn, AuditAction.DELETE, actor, doc_id=doc_id, note=f"{row['filename']} (sha256 {row['sha256'][:12]})")
    ctx.storage.delete_document_files(doc_id, row["stored_name"])


def delete_all(ctx: AppContext, *, actor: str) -> None:
    """Callers stop background work first. Leaves a fresh database whose only row records the wipe."""
    ctx.storage.wipe()
    db_path = ctx.settings.db_path
    for suffix in ("", "-wal", "-shm"):
        Path(f"{db_path}{suffix}").unlink(missing_ok=True)
    with closing(db.connect(db_path)) as conn:
        db.init_db(conn)
        db.audit(conn, AuditAction.DELETE_ALL, actor, note="Deleted every document, derived file, capture, export, and record.")
