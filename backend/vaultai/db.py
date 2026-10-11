"""SQLite schema, connections, and the audit trail.

The database is the source of truth for records; raw files live under
data/raw and are referenced by stored_name only. Every connection is
per-thread and short-lived; WAL mode lets the UI read while workers write.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from vaultai.domain import EXPORTABLE, AuditAction, Box, DocStatus, Evidence, check_transition

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
  id TEXT PRIMARY KEY,
  filename TEXT NOT NULL,
  stored_name TEXT NOT NULL UNIQUE,
  sha256 TEXT NOT NULL UNIQUE,
  size_bytes INTEGER NOT NULL,
  mime TEXT NOT NULL,
  page_count INTEGER,
  source TEXT NOT NULL,
  status TEXT NOT NULL,
  stage TEXT,
  doc_type TEXT,
  doc_type_confidence REAL,
  classification_method TEXT,
  doc_type_hint TEXT,
  title TEXT,
  counterparty TEXT,
  doc_date TEXT,
  total_amount TEXT,
  currency TEXT,
  primary_id TEXT,
  next_deadline TEXT,
  confidence REAL,
  risk_level TEXT NOT NULL DEFAULT 'none',
  injection_suspected INTEGER NOT NULL DEFAULT 0,
  route TEXT,
  model_kind TEXT,
  model_name TEXT,
  schema_version TEXT,
  ocr_engine TEXT,
  processing_ms INTEGER,
  error TEXT,
  deferred_at TEXT,
  approved_at TEXT,
  approved_by TEXT,
  approval_kind TEXT,
  summary_json TEXT,
  created_at TEXT NOT NULL,
  processed_at TEXT,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_documents_status ON documents(status);
CREATE INDEX IF NOT EXISTS idx_documents_type ON documents(doc_type);

CREATE TABLE IF NOT EXISTS pages (
  doc_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  page_no INTEGER NOT NULL,
  width REAL NOT NULL,
  height REAL NOT NULL,
  text_method TEXT NOT NULL,
  ocr_confidence REAL,
  page_hash TEXT NOT NULL,
  image_name TEXT,
  lines_json TEXT NOT NULL,
  PRIMARY KEY (doc_id, page_no)
);

CREATE TABLE IF NOT EXISTS fields (
  id INTEGER PRIMARY KEY,
  doc_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  key TEXT NOT NULL,
  label TEXT NOT NULL,
  value_type TEXT NOT NULL,
  required INTEGER NOT NULL DEFAULT 0,
  ai_value TEXT,
  raw_text TEXT,
  currency TEXT,
  confidence REAL NOT NULL,
  ai_origin TEXT NOT NULL,
  page INTEGER,
  bbox_json TEXT,
  line_id TEXT,
  evidence_text TEXT,
  model_name TEXT,
  schema_version TEXT NOT NULL,
  extracted_at TEXT NOT NULL,
  -- A field is human-edited iff edited_at is set; human_value may legitimately be NULL (cleared).
  human_value TEXT,
  edited_by TEXT,
  edited_at TEXT,
  UNIQUE (doc_id, key)
);

CREATE TABLE IF NOT EXISTS line_items (
  id INTEGER PRIMARY KEY,
  doc_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  idx INTEGER NOT NULL,
  description TEXT NOT NULL,
  quantity TEXT,
  unit_price TEXT,
  amount TEXT,
  page INTEGER,
  bbox_json TEXT,
  line_id TEXT,
  evidence_text TEXT
);

CREATE INDEX IF NOT EXISTS idx_line_items_doc ON line_items(doc_id);

CREATE TABLE IF NOT EXISTS issues (
  id INTEGER PRIMARY KEY,
  doc_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  code TEXT NOT NULL,
  severity TEXT NOT NULL,
  message TEXT NOT NULL,
  field_key TEXT,
  page INTEGER,
  bbox_json TEXT,
  line_id TEXT,
  evidence_text TEXT,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_issues_doc ON issues(doc_id);

CREATE TABLE IF NOT EXISTS clauses (
  id INTEGER PRIMARY KEY,
  doc_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  kind TEXT NOT NULL,
  heading TEXT NOT NULL,
  text TEXT NOT NULL,
  risk TEXT NOT NULL,
  risk_reason TEXT,
  interpretation TEXT,
  origin TEXT NOT NULL,
  page INTEGER,
  bbox_json TEXT,
  line_id TEXT,
  evidence_text TEXT
);

CREATE INDEX IF NOT EXISTS idx_clauses_doc ON clauses(doc_id);

CREATE TABLE IF NOT EXISTS obligations (
  id INTEGER PRIMARY KEY,
  doc_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  kind TEXT NOT NULL,
  party TEXT,
  description TEXT NOT NULL,
  due_date TEXT,
  status TEXT NOT NULL DEFAULT 'open',
  origin TEXT NOT NULL,
  page INTEGER,
  bbox_json TEXT,
  line_id TEXT,
  evidence_text TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_obligations_due ON obligations(due_date);
CREATE INDEX IF NOT EXISTS idx_obligations_doc ON obligations(doc_id);

-- No foreign key: audit rows outlive the documents they describe.
CREATE TABLE IF NOT EXISTS audit_log (
  id INTEGER PRIMARY KEY,
  at TEXT NOT NULL,
  actor TEXT NOT NULL,
  action TEXT NOT NULL,
  doc_id TEXT,
  field_key TEXT,
  before TEXT,
  after TEXT,
  note TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_doc ON audit_log(doc_id);

CREATE TABLE IF NOT EXISTS intake (
  id INTEGER PRIMARY KEY,
  batch_id TEXT NOT NULL,
  filename TEXT NOT NULL,
  source TEXT NOT NULL,
  mime TEXT,
  size_bytes INTEGER NOT NULL,
  page_count INTEGER,
  preflight TEXT NOT NULL,
  error TEXT,
  duplicate_of TEXT,
  document_id TEXT REFERENCES documents(id) ON DELETE SET NULL,
  dismissed INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_intake_doc ON intake(document_id);

CREATE TABLE IF NOT EXISTS exports (
  id TEXT PRIMARY KEY,
  created_at TEXT NOT NULL,
  actor TEXT NOT NULL,
  format TEXT NOT NULL,
  filters_json TEXT NOT NULL,
  record_count INTEGER NOT NULL,
  stored_name TEXT NOT NULL,
  sha256 TEXT NOT NULL,
  duration_ms INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS capture_sessions (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  status TEXT NOT NULL,
  token_hash TEXT,
  code TEXT,
  device_id TEXT,
  created_at TEXT NOT NULL,
  expires_at TEXT,
  intake_id INTEGER,
  document_id TEXT
);

CREATE TABLE IF NOT EXISTS capture_pages (
  id INTEGER PRIMARY KEY,
  session_id TEXT NOT NULL REFERENCES capture_sessions(id) ON DELETE CASCADE,
  idx INTEGER NOT NULL,
  stored_name TEXT NOT NULL,
  width INTEGER NOT NULL,
  height INTEGER NOT NULL,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_capture_pages_session ON capture_pages(session_id);

-- OCR and model outputs keyed by content hash + engine/model/schema version.
CREATE TABLE IF NOT EXISTS cache (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  created_at TEXT NOT NULL
);

-- Per-document processing metrics: stage timings, model calls, tokens, prompt and model versions. No document text.
CREATE TABLE IF NOT EXISTS doc_metrics (
  doc_id TEXT PRIMARY KEY REFERENCES documents(id) ON DELETE CASCADE,
  metrics_json TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS counters (
  name TEXT PRIMARY KEY,
  value INTEGER NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS doc_fts USING fts5(
  doc_id UNINDEXED, filename, body, tokenize = 'porter unicode61'
);
"""


def now() -> datetime:
    return datetime.now(UTC)


def iso(dt: datetime | None = None) -> str:
    return (dt or now()).isoformat(timespec="milliseconds")


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=10, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 10000")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def audit(
    conn: sqlite3.Connection,
    action: AuditAction,
    actor: str,
    *,
    doc_id: str | None = None,
    field_key: str | None = None,
    before: str | None = None,
    after: str | None = None,
    note: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO audit_log (at, actor, action, doc_id, field_key, before, after, note) VALUES (?,?,?,?,?,?,?,?)",
        (iso(), actor, action.value, doc_id, field_key, before, after, note),
    )


def set_status(
    conn: sqlite3.Connection,
    doc_id: str,
    target: DocStatus,
    *,
    actor: str,
    action: AuditAction | None = None,
    note: str | None = None,
    columns: dict[str, object] | None = None,
    at: datetime | None = None,
) -> DocStatus:
    """The one way a document's status changes. Returns the previous status.

    Enforces the state machine, clears approval columns whenever the target is not an
    approved state and clears deferral unless the caller sets it, then audits `action`.
    Column names come from code, never from requests.
    """
    row = conn.execute("SELECT status FROM documents WHERE id = ?", (doc_id,)).fetchone()
    if row is None:
        raise LookupError(f"document {doc_id} not found")
    current = DocStatus(row["status"])
    check_transition(current, target)
    changes: dict[str, object] = {"deferred_at": None}
    if target not in EXPORTABLE:
        changes |= {"approved_at": None, "approved_by": None, "approval_kind": None}
    changes |= columns or {}
    assignments = "".join(f", {column} = ?" for column in changes)
    conn.execute(
        f"UPDATE documents SET status = ?{assignments}, updated_at = ? WHERE id = ?",
        (target.value, *changes.values(), iso(at), doc_id),
    )
    if action is not None:
        audit(conn, action, actor, doc_id=doc_id, before=current.value, after=target.value, note=note)
    return current


def bump(conn: sqlite3.Connection, name: str, by: int = 1) -> None:
    conn.execute(
        "INSERT INTO counters (name, value) VALUES (?, ?) ON CONFLICT(name) DO UPDATE SET value = value + excluded.value",
        (name, by),
    )


def bbox_to_json(bbox: Box | None) -> str | None:
    return json.dumps([round(v, 5) for v in bbox]) if bbox else None


def evidence_from_row(row: sqlite3.Row) -> Evidence | None:
    """Rows of fields, line_items, issues, clauses, and obligations share the evidence columns."""
    if row["page"] is None and row["line_id"] is None and row["evidence_text"] is None:
        return None
    bbox = json.loads(row["bbox_json"]) if row["bbox_json"] else None
    return Evidence(page=row["page"], bbox=tuple(bbox) if bbox else None, line_id=row["line_id"], text=row["evidence_text"])


def evidence_columns(ev: Evidence | None) -> tuple[int | None, str | None, str | None, str | None]:
    """(page, bbox_json, line_id, evidence_text) for INSERTs into any evidence-bearing table."""
    if ev is None:
        return (None, None, None, None)
    return (ev.page, bbox_to_json(ev.bbox), ev.line_id, ev.text)
