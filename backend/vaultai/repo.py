"""Database rows to domain models. The only place that knows how API shapes map onto table columns."""

from __future__ import annotations

import json
import sqlite3

from vaultai.db import evidence_from_row
from vaultai.schemas import ENUM_OPTIONS
from vaultai.domain import (
    AuditEntry,
    CapturePage,
    CaptureSession,
    Clause,
    DocumentDetail,
    DocumentSummary,
    ExportFilters,
    ExportJob,
    ExtractedField,
    FieldOrigin,
    IntakeItem,
    LineItem,
    Obligation,
    PageInfo,
    Provenance,
    Summary,
    ValidationIssue,
)


class Problem(Exception):
    """A failure the client can act on. The HTTP layer renders it as ApiError with this status."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def not_found() -> Problem:
    return Problem(404, "not_found", "Not found.")


SUMMARY_SQL = """
SELECT d.*,
  (SELECT COUNT(*) FROM issues i WHERE i.doc_id = d.id) AS issue_count,
  (SELECT COUNT(*) FROM issues i WHERE i.doc_id = d.id AND i.severity = 'error') AS error_count
FROM documents d
"""

_SUMMARY_COLUMNS = tuple(
    k for k in DocumentSummary.model_fields if k not in {"deferred", "injection_suspected"}
)


def summary_from_row(row: sqlite3.Row) -> DocumentSummary:
    data = {k: row[k] for k in _SUMMARY_COLUMNS}
    data["deferred"] = row["deferred_at"] is not None
    data["injection_suspected"] = bool(row["injection_suspected"])
    return DocumentSummary.model_validate(data)


def doc_row(conn: sqlite3.Connection, doc_id: str) -> sqlite3.Row:
    row = conn.execute(SUMMARY_SQL + " WHERE d.id = ?", (doc_id,)).fetchone()
    if row is None:
        raise not_found()
    return row


def get_summary(conn: sqlite3.Connection, doc_id: str) -> DocumentSummary:
    return summary_from_row(doc_row(conn, doc_id))


def field_from_row(row: sqlite3.Row) -> ExtractedField:
    edited = row["edited_at"] is not None
    return ExtractedField(
        key=row["key"],
        label=row["label"],
        value_type=row["value_type"],
        required=bool(row["required"]),
        value=row["human_value"] if edited else row["ai_value"],
        ai_value=row["ai_value"],
        raw_text=row["raw_text"],
        currency=row["currency"],
        options=list(ENUM_OPTIONS.get(row["key"], ())) if row["value_type"] == "enum" else [],
        confidence=row["confidence"],
        origin=FieldOrigin.HUMAN if edited else row["ai_origin"],
        ai_origin=row["ai_origin"],
        evidence=evidence_from_row(row),
        edited_by=row["edited_by"],
        edited_at=row["edited_at"],
        model_name=row["model_name"],
        schema_version=row["schema_version"],
        extracted_at=row["extracted_at"],
    )


def load_fields(conn: sqlite3.Connection, doc_id: str) -> list[ExtractedField]:
    rows = conn.execute("SELECT * FROM fields WHERE doc_id = ? ORDER BY id", (doc_id,))
    return [field_from_row(r) for r in rows]


def load_issues(conn: sqlite3.Connection, doc_id: str) -> list[ValidationIssue]:
    rows = conn.execute(
        "SELECT * FROM issues WHERE doc_id = ? "
        "ORDER BY CASE severity WHEN 'error' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END, id",
        (doc_id,),
    )
    return [issue_from_row(r) for r in rows]


def issue_from_row(row: sqlite3.Row) -> ValidationIssue:
    return ValidationIssue(
        id=row["id"],
        code=row["code"],
        severity=row["severity"],
        message=row["message"],
        field_key=row["field_key"],
        evidence=evidence_from_row(row),
        created_at=row["created_at"],
    )


OBLIGATION_SQL = "SELECT o.*, d.filename AS doc_filename FROM obligations o JOIN documents d ON d.id = o.doc_id"


def obligation_from_row(row: sqlite3.Row) -> Obligation:
    return Obligation(
        id=row["id"],
        doc_id=row["doc_id"],
        doc_filename=row["doc_filename"],
        kind=row["kind"],
        party=row["party"],
        description=row["description"],
        due_date=row["due_date"],
        status=row["status"],
        origin=row["origin"],
        evidence=evidence_from_row(row),
    )


def load_obligations(conn: sqlite3.Connection, doc_id: str) -> list[Obligation]:
    rows = conn.execute(
        OBLIGATION_SQL + " WHERE o.doc_id = ? ORDER BY o.due_date IS NULL, o.due_date, o.id", (doc_id,)
    )
    return [obligation_from_row(r) for r in rows]


def get_obligation(conn: sqlite3.Connection, obligation_id: int) -> Obligation:
    row = conn.execute(OBLIGATION_SQL + " WHERE o.id = ?", (obligation_id,)).fetchone()
    if row is None:
        raise not_found()
    return obligation_from_row(row)


def audit_from_row(row: sqlite3.Row) -> AuditEntry:
    return AuditEntry.model_validate(dict(row))


AUDIT_SQL = "SELECT a.*, d.filename AS doc_filename FROM audit_log a LEFT JOIN documents d ON d.id = a.doc_id"


def load_audit(conn: sqlite3.Connection, *, doc_id: str | None = None, limit: int = 100) -> list[AuditEntry]:
    if doc_id is None:
        rows = conn.execute(AUDIT_SQL + " ORDER BY a.id DESC LIMIT ?", (limit,))
    else:
        rows = conn.execute(AUDIT_SQL + " WHERE a.doc_id = ? ORDER BY a.id DESC LIMIT ?", (doc_id, limit))
    return [audit_from_row(r) for r in rows]


def provenance_from_row(row: sqlite3.Row) -> Provenance:
    return Provenance(
        sha256=row["sha256"],
        original_filename=row["filename"],
        model_kind=row["model_kind"],
        model_name=row["model_name"],
        schema_version=row["schema_version"],
        ocr_engine=row["ocr_engine"],
        route=row["route"],
        processed_at=row["processed_at"],
        processing_ms=row["processing_ms"],
    )


def get_detail(conn: sqlite3.Connection, doc_id: str) -> DocumentDetail:
    row = doc_row(conn, doc_id)
    line_items = [
        LineItem(
            idx=r["idx"],
            description=r["description"],
            quantity=r["quantity"],
            unit_price=r["unit_price"],
            amount=r["amount"],
            evidence=evidence_from_row(r),
        )
        for r in conn.execute("SELECT * FROM line_items WHERE doc_id = ? ORDER BY idx", (doc_id,))
    ]
    clauses = [
        Clause(
            id=r["id"],
            kind=r["kind"],
            heading=r["heading"],
            text=r["text"],
            risk=r["risk"],
            risk_reason=r["risk_reason"],
            interpretation=r["interpretation"],
            origin=r["origin"],
            evidence=evidence_from_row(r),
        )
        for r in conn.execute("SELECT * FROM clauses WHERE doc_id = ? ORDER BY id", (doc_id,))
    ]
    pages = [
        PageInfo(
            page_no=r["page_no"],
            width=r["width"],
            height=r["height"],
            text_method=r["text_method"],
            ocr_confidence=r["ocr_confidence"],
            has_image=r["image_name"] is not None,
            lines=json.loads(r["lines_json"]),
        )
        for r in conn.execute("SELECT * FROM pages WHERE doc_id = ? ORDER BY page_no", (doc_id,))
    ]
    return DocumentDetail(
        **summary_from_row(row).model_dump(),
        fields=load_fields(conn, doc_id),
        line_items=line_items,
        issues=load_issues(conn, doc_id),
        clauses=clauses,
        obligations=load_obligations(conn, doc_id),
        summary=Summary.model_validate_json(row["summary_json"]) if row["summary_json"] else None,
        pages=pages,
        history=load_audit(conn, doc_id=doc_id, limit=1000),
        provenance=provenance_from_row(row),
    )


INTAKE_SQL = """
SELECT i.*, d.status AS doc_status, d.doc_type AS doc_type, d.stage AS stage
FROM intake i LEFT JOIN documents d ON d.id = i.document_id
"""


def intake_from_row(row: sqlite3.Row) -> IntakeItem:
    return IntakeItem(
        id=row["id"],
        batch_id=row["batch_id"],
        filename=row["filename"],
        source=row["source"],
        mime=row["mime"],
        size_bytes=row["size_bytes"],
        page_count=row["page_count"],
        preflight=row["preflight"],
        error=row["error"],
        duplicate_of=row["duplicate_of"],
        document_id=row["document_id"],
        doc_status=row["doc_status"],
        doc_type=row["doc_type"],
        stage=row["stage"],
        created_at=row["created_at"],
    )


def get_intake(conn: sqlite3.Connection, intake_id: int) -> IntakeItem:
    row = conn.execute(INTAKE_SQL + " WHERE i.id = ?", (intake_id,)).fetchone()
    if row is None:
        raise not_found()
    return intake_from_row(row)


def list_intake(conn: sqlite3.Connection, limit: int = 500) -> list[IntakeItem]:
    rows = conn.execute(INTAKE_SQL + " WHERE i.dismissed = 0 ORDER BY i.id DESC LIMIT ?", (limit,))
    return [intake_from_row(r) for r in rows]


def capture_row(conn: sqlite3.Connection, session_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM capture_sessions WHERE id = ?", (session_id,)).fetchone()
    if row is None:
        raise not_found()
    return row


def get_capture_session(conn: sqlite3.Connection, session_id: str) -> CaptureSession:
    row = capture_row(conn, session_id)
    pages = [
        CapturePage(id=p["id"], idx=p["idx"], width=p["width"], height=p["height"])
        for p in conn.execute("SELECT * FROM capture_pages WHERE session_id = ? ORDER BY idx", (session_id,))
    ]
    return CaptureSession(
        id=row["id"],
        kind=row["kind"],
        status=row["status"],
        device_id=row["device_id"],
        pages=pages,
        created_at=row["created_at"],
        expires_at=row["expires_at"],
        intake_id=row["intake_id"],
        document_id=row["document_id"],
    )


def export_from_row(row: sqlite3.Row) -> ExportJob:
    return ExportJob(
        id=row["id"],
        created_at=row["created_at"],
        actor=row["actor"],
        format=row["format"],
        filters=ExportFilters.model_validate_json(row["filters_json"]),
        record_count=row["record_count"],
        filename=export_display_name(row["created_at"], row["format"]),
        sha256=row["sha256"],
        duration_ms=row["duration_ms"],
    )


def export_display_name(created_at: str, fmt: str) -> str:
    stamp = created_at[:19].replace("-", "").replace(":", "").replace("T", "-")
    return f"vaultai-export-{stamp}.{fmt}"
