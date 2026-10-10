"""Database rows to domain models. The only place that knows how API shapes map onto table columns."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterable, Sequence
from typing import TypeVar

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
    ObligationStatus,
    PageInfo,
    Provenance,
    Summary,
    ValidationIssue,
)

T = TypeVar("T")


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


def load_summaries(conn: sqlite3.Connection, where: str = "", params: Sequence[object] = ()) -> list[DocumentSummary]:
    return [summary_from_row(r) for r in conn.execute(f"{SUMMARY_SQL} {where}", params)]


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


# One JSON-array parameter instead of one per id, so a selection never hits SQLite's bound-variable limit.
_IN_IDS = "IN (SELECT value FROM json_each(?))"


def _by_doc(rows: Iterable[sqlite3.Row], from_row: Callable[[sqlite3.Row], T]) -> dict[str, list[T]]:
    grouped: dict[str, list[T]] = {}
    for row in rows:
        grouped.setdefault(row["doc_id"], []).append(from_row(row))
    return grouped


def fields_by_doc(conn: sqlite3.Connection, doc_ids: Sequence[str]) -> dict[str, list[ExtractedField]]:
    rows = conn.execute(f"SELECT * FROM fields WHERE doc_id {_IN_IDS} ORDER BY id", (json.dumps(doc_ids),))
    return _by_doc(rows, field_from_row)


def load_fields(conn: sqlite3.Connection, doc_id: str) -> list[ExtractedField]:
    return fields_by_doc(conn, [doc_id]).get(doc_id, [])


def human_edit_counts(conn: sqlite3.Connection, doc_ids: Sequence[str]) -> dict[str, int]:
    rows = conn.execute(
        f"SELECT doc_id, COUNT(*) FROM fields WHERE doc_id {_IN_IDS} AND edited_at IS NOT NULL GROUP BY doc_id",
        (json.dumps(doc_ids),),
    )
    return dict(rows.fetchall())


def issues_by_doc(conn: sqlite3.Connection, doc_ids: Sequence[str]) -> dict[str, list[ValidationIssue]]:
    rows = conn.execute(
        f"SELECT * FROM issues WHERE doc_id {_IN_IDS} "
        "ORDER BY CASE severity WHEN 'error' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END, id",
        (json.dumps(doc_ids),),
    )
    return _by_doc(rows, issue_from_row)


def load_issues(conn: sqlite3.Connection, doc_id: str) -> list[ValidationIssue]:
    return issues_by_doc(conn, [doc_id]).get(doc_id, [])


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


def obligations_by_doc(conn: sqlite3.Connection, doc_ids: Sequence[str]) -> dict[str, list[Obligation]]:
    rows = conn.execute(
        f"{OBLIGATION_SQL} WHERE o.doc_id {_IN_IDS} ORDER BY o.due_date IS NULL, o.due_date, o.id",
        (json.dumps(doc_ids),),
    )
    return _by_doc(rows, obligation_from_row)


def load_obligations(conn: sqlite3.Connection, doc_id: str) -> list[Obligation]:
    return obligations_by_doc(conn, [doc_id]).get(doc_id, [])


def open_dated_obligations(conn: sqlite3.Connection) -> list[Obligation]:
    rows = conn.execute(f"{OBLIGATION_SQL} WHERE o.status = ? AND o.due_date IS NOT NULL", (ObligationStatus.OPEN.value,))
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
