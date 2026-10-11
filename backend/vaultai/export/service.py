"""What an export contains: approved records only by default, never mixed with records still under review."""

from __future__ import annotations

import hashlib
import secrets
import sqlite3
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime

from vaultai import db, repo, search
from vaultai.context import AppContext
from vaultai.domain import (
    APP_VERSION,
    DOC_TYPE_LABELS,
    EXPORTABLE,
    REVIEWABLE,
    SCHEMA_VERSION,
    ApprovalKind,
    AuditAction,
    DocStatus,
    DocumentSummary,
    ExportFilters,
    ExportFormat,
    ExportJob,
    ExportPreview,
    ExportRow,
    ExtractedField,
    FieldOrigin,
    Obligation,
    Provenance,
    TypeCount,
    ValidationIssue,
)
from vaultai.export import Column, Kind, Table
from vaultai.export.csv_export import render_csv
from vaultai.export.xlsx import render_xlsx
from vaultai.storage import Storage

NOTICE = (
    "Values marked AI were extracted by VaultAI and approved by a human or by the auto-approval policy shown per row. "
    "VaultAI is a document assistant, not legal or financial advice."
)

APPROVAL_LABEL: dict[ApprovalKind, str] = {ApprovalKind.HUMAN: "Human", ApprovalKind.POLICY: "Auto-approval policy"}
ORIGIN_LABEL: dict[FieldOrigin, str] = {
    FieldOrigin.RULES: "AI-rules",
    FieldOrigin.MODEL: "AI-model",
    FieldOrigin.HUMAN: "Human-edited",
}
MEDIA_TYPES: dict[ExportFormat, str] = {
    ExportFormat.XLSX: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ExportFormat.CSV: "text/csv; charset=utf-8",
}

SHEET_INFO = "Export Info"
SHEET_APPROVED = "Approved Records"
SHEET_NEEDS_REVIEW = "Needs Review"
SHEET_EVIDENCE = "Source Evidence"
SHEET_METADATA = "Processing Metadata"
SHEET_WARNINGS = "Validation Warnings"
SHEET_OBLIGATIONS = "Obligations and Deadlines"

T, ID, M, D, DT, N, I = Kind.TEXT, Kind.ID, Kind.MONEY, Kind.DATE, Kind.DATETIME, Kind.NUMBER, Kind.INT

INFO_COLUMNS = (Column("Field", T, 26), Column("Value", T, 110))
RECORD_COLUMNS = (
    Column("Document ID", ID, 34), Column("File", T, 32), Column("File SHA-256", ID, 66), Column("Type", T, 18),
    Column("Counterparty", T, 28), Column("Document Date", D, 14), Column("Identifier", ID, 20),
    Column("Total", M, 14), Column("Currency", T, 10), Column("Status", T, 13), Column("Approval", T, 20),
    Column("Approved By", T, 16), Column("Approved At", DT, 20), Column("Confidence", N, 12),
    Column("Human Edits", I, 12), Column("Model", T, 22), Column("Schema Version", T, 15), Column("Processed At", DT, 20),
)
EVIDENCE_COLUMNS = (
    Column("Document ID", ID, 34), Column("File", T, 32), Column("Field", T, 24), Column("Value", T, 30),
    Column("Raw Text", T, 30), Column("Value Origin", T, 14), Column("AI Value", T, 30), Column("Confidence", N, 12),
    Column("Page", I, 8), Column("BBox", T, 30), Column("Line ID", ID, 10), Column("Evidence Text", T, 50),
    Column("Edited By", T, 16), Column("Edited At", DT, 20),
)
METADATA_COLUMNS = (
    Column("Document ID", ID, 34), Column("File", T, 32), Column("File SHA-256", ID, 66), Column("Source", T, 10),
    Column("MIME Type", T, 18), Column("Size (bytes)", I, 14), Column("Pages", I, 8), Column("Route", T, 10),
    Column("Model Kind", T, 14), Column("Model", T, 22), Column("OCR Engine", T, 16), Column("Schema Version", T, 15),
    Column("Processing (ms)", I, 15), Column("Imported At", DT, 20), Column("Processed At", DT, 20),
    Column("Classification", T, 14), Column("Type Confidence", N, 15),
)
WARNING_COLUMNS = (
    Column("Document ID", ID, 34), Column("File", T, 32), Column("Severity", T, 10), Column("Code", T, 24),
    Column("Field", T, 20), Column("Message", T, 60), Column("Page", I, 8), Column("Evidence Text", T, 50),
)
OBLIGATION_COLUMNS = (
    Column("Document ID", ID, 34), Column("File", T, 32), Column("Kind", T, 12), Column("Party", T, 24),
    Column("Description", T, 60), Column("Due Date", D, 14), Column("Status", T, 10), Column("Origin", T, 14),
    Column("Page", I, 8), Column("Evidence Text", T, 50),
)


@dataclass(frozen=True)
class Record:
    doc: DocumentSummary
    provenance: Provenance
    human_edits: int


@dataclass(frozen=True)
class Selection:
    approved: list[Record]
    needs_review: list[Record]
    needs_review_count: int


@dataclass(frozen=True)
class Details:
    fields: dict[str, list[ExtractedField]]
    issues: dict[str, list[ValidationIssue]]
    obligations: dict[str, list[Obligation]]


def _where(filters: ExportFilters, statuses: frozenset[DocStatus]) -> tuple[str, list[object]]:
    return search.where(search.DocumentQuery(
        statuses=tuple(statuses), doc_types=tuple(filters.doc_types), date_from=filters.date_from,
        date_to=filters.date_to,
    ))


def _records(conn: sqlite3.Connection, filters: ExportFilters, statuses: frozenset[DocStatus]) -> list[Record]:
    where, params = _where(filters, statuses)
    rows = conn.execute(
        f"{repo.SUMMARY_SQL}{where} ORDER BY d.doc_date IS NULL, d.doc_date, d.filename, d.id", params
    ).fetchall()
    edits = repo.human_edit_counts(conn, [r["id"] for r in rows])
    return [Record(repo.summary_from_row(r), repo.provenance_from_row(r), edits.get(r["id"], 0)) for r in rows]


def select_records(conn: sqlite3.Connection, filters: ExportFilters) -> Selection:
    where, params = _where(filters, REVIEWABLE)
    return Selection(
        approved=_records(conn, filters, EXPORTABLE),
        needs_review=_records(conn, filters, REVIEWABLE) if filters.include_needs_review else [],
        needs_review_count=conn.execute(f"SELECT COUNT(*) FROM documents d{where}", params).fetchone()[0],
    )


def load_details(conn: sqlite3.Connection, records: list[Record]) -> Details:
    ids = [r.doc.id for r in records]
    return Details(repo.fields_by_doc(conn, ids), repo.issues_by_doc(conn, ids), repo.obligations_by_doc(conn, ids))


def sheet_names(filters: ExportFilters) -> list[str]:
    if filters.format is ExportFormat.CSV:
        return [SHEET_APPROVED]
    return [
        SHEET_INFO, SHEET_APPROVED,
        *([SHEET_NEEDS_REVIEW] if filters.include_needs_review else []),
        *([SHEET_EVIDENCE] if filters.include_evidence else []),
        SHEET_METADATA, SHEET_WARNINGS, SHEET_OBLIGATIONS,
    ]


def _type_label(doc: DocumentSummary) -> str | None:
    return DOC_TYPE_LABELS[doc.doc_type] if doc.doc_type else None


def _record_row(r: Record) -> tuple[object, ...]:
    d = r.doc
    return (
        d.id, d.filename, r.provenance.sha256, _type_label(d), d.counterparty, d.doc_date, d.primary_id,
        d.total_amount, d.currency, d.status.value, APPROVAL_LABEL.get(d.approval_kind) if d.approval_kind else None,
        d.approved_by, d.approved_at, d.confidence, r.human_edits, r.provenance.model_name,
        r.provenance.schema_version, r.provenance.processed_at,
    )


def _bbox(f: ExtractedField) -> str | None:
    if f.evidence is None or f.evidence.bbox is None:
        return None
    return ", ".join(f"{v:.4f}" for v in f.evidence.bbox)


def _evidence_rows(records: list[Record], fields: dict[str, list[ExtractedField]]) -> list[tuple[object, ...]]:
    return [
        (
            r.doc.id, r.doc.filename, f.label, f.value, f.raw_text, ORIGIN_LABEL[f.origin], f.ai_value, f.confidence,
            f.evidence.page if f.evidence else None, _bbox(f), f.evidence.line_id if f.evidence else None,
            f.evidence.text if f.evidence else None, f.edited_by, f.edited_at,
        )
        for r in records
        for f in fields.get(r.doc.id, [])
    ]


def _metadata_rows(records: list[Record]) -> list[tuple[object, ...]]:
    return [
        (
            r.doc.id, r.doc.filename, p.sha256, r.doc.source.value, r.doc.mime, r.doc.size_bytes, r.doc.page_count,
            p.route.value if p.route else None, p.model_kind.value if p.model_kind else None, p.model_name,
            p.ocr_engine, p.schema_version, p.processing_ms, r.doc.created_at, p.processed_at,
            r.doc.classification_method.value if r.doc.classification_method else None, r.doc.doc_type_confidence,
        )
        for r in records
        for p in (r.provenance,)
    ]


def _warning_rows(records: list[Record], issues: dict[str, list[ValidationIssue]]) -> list[tuple[object, ...]]:
    return [
        (
            r.doc.id, r.doc.filename, i.severity.value, i.code.value, i.field_key, i.message,
            i.evidence.page if i.evidence else None, i.evidence.text if i.evidence else None,
        )
        for r in records
        for i in issues.get(r.doc.id, [])
    ]


def _obligation_rows(records: list[Record], obligations: dict[str, list[Obligation]]) -> list[tuple[object, ...]]:
    return [
        (
            r.doc.id, r.doc.filename, o.kind.value, o.party, o.description, o.due_date, o.status.value,
            ORIGIN_LABEL[o.origin], o.evidence.page if o.evidence else None, o.evidence.text if o.evidence else None,
        )
        for r in records
        for o in obligations.get(r.doc.id, [])
    ]


def _describe_filters(filters: ExportFilters) -> list[tuple[str, str]]:
    types = ", ".join(DOC_TYPE_LABELS[t] for t in filters.doc_types) or "All"
    start = filters.date_from.isoformat() if filters.date_from else "any"
    end = filters.date_to.isoformat() if filters.date_to else "any"
    dates = "Any" if not (filters.date_from or filters.date_to) else f"{start} to {end}"
    return [
        ("Document Types", types),
        ("Document Dates", dates),
        ("Needs Review Sheet", "Included" if filters.include_needs_review else "Not included"),
        ("Source Evidence", "Included" if filters.include_evidence else "Not included"),
    ]


def build_tables(
    selection: Selection, details: Details, filters: ExportFilters, *, export_id: str, actor: str, created_at: datetime
) -> list[Table]:
    approved = selection.approved
    info: list[tuple[object, ...]] = [
        ("Export ID", export_id),
        ("Created At", created_at.strftime("%Y-%m-%d %H:%M:%S UTC")),
        ("Exported By", actor),
        ("Format", filters.format.value.upper()),
        *_describe_filters(filters),
        ("Approved Records", str(len(approved))),
        ("Needs Review Records", str(len(selection.needs_review))),
        ("Times", "All times are UTC."),
        ("VaultAI Version", APP_VERSION),
        ("Schema Version", SCHEMA_VERSION),
        ("Notice", NOTICE),
    ]
    tables = {
        SHEET_INFO: lambda: Table(SHEET_INFO, INFO_COLUMNS, info),
        SHEET_APPROVED: lambda: Table(SHEET_APPROVED, RECORD_COLUMNS, [_record_row(r) for r in approved]),
        SHEET_NEEDS_REVIEW: lambda: Table(
            SHEET_NEEDS_REVIEW, RECORD_COLUMNS, [_record_row(r) for r in selection.needs_review]
        ),
        SHEET_EVIDENCE: lambda: Table(SHEET_EVIDENCE, EVIDENCE_COLUMNS, _evidence_rows(approved, details.fields)),
        SHEET_METADATA: lambda: Table(SHEET_METADATA, METADATA_COLUMNS, _metadata_rows(approved)),
        SHEET_WARNINGS: lambda: Table(SHEET_WARNINGS, WARNING_COLUMNS, _warning_rows(approved, details.issues)),
        SHEET_OBLIGATIONS: lambda: Table(
            SHEET_OBLIGATIONS, OBLIGATION_COLUMNS, _obligation_rows(approved, details.obligations)
        ),
    }
    return [tables[name]() for name in sheet_names(filters)]


def _export_row(r: Record) -> ExportRow:
    d = r.doc
    return ExportRow(
        doc_id=d.id, filename=d.filename, doc_type=d.doc_type, status=d.status, counterparty=d.counterparty,
        doc_date=d.doc_date, primary_id=d.primary_id, total_amount=d.total_amount, currency=d.currency,
        approval_kind=d.approval_kind, approved_by=d.approved_by, human_edits=r.human_edits,
    )


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def _warnings(selection: Selection, filters: ExportFilters) -> list[str]:
    warnings: list[str] = []
    if not selection.approved:
        warnings.append("No approved records match these filters.")
    pending = selection.needs_review_count
    if pending and not filters.include_needs_review:
        warnings.append(f"{_plural(pending, 'matching record still needs', 'matching records still need')} review and "
                        f"{'is' if pending == 1 else 'are'} not included.")
    if pending and filters.include_needs_review and filters.format is ExportFormat.XLSX:
        warnings.append(f"{_plural(pending, 'record that still needs review goes', 'records that still need review go')}"
                        " to a separate Needs Review sheet and not into Approved Records.")
    if filters.include_needs_review and filters.format is ExportFormat.CSV:
        warnings.append("CSV contains approved records only. Choose XLSX to include the Needs Review sheet.")
    accepted = sum(r.doc.error_count > 0 for r in selection.approved)
    if accepted:
        warnings.append(f"{_plural(accepted, 'approved record has', 'approved records have')} validation errors a "
                        "reviewer accepted with a note.")
    return warnings


def preview(conn: sqlite3.Connection, filters: ExportFilters) -> ExportPreview:
    selection = select_records(conn, filters)
    types = Counter(r.doc.doc_type for r in selection.approved if r.doc.doc_type is not None)
    rows = selection.approved + (selection.needs_review if filters.format is ExportFormat.XLSX else [])
    return ExportPreview(
        record_count=len(selection.approved),
        needs_review_count=selection.needs_review_count,
        by_type=[TypeCount(doc_type=t, count=n) for t, n in types.most_common()],
        sheets=sheet_names(filters),
        rows=[_export_row(r) for r in rows],
        warnings=_warnings(selection, filters),
    )


def create_export(ctx: AppContext, filters: ExportFilters, *, actor: str, now: datetime) -> ExportJob:
    """Select, render, write, and mark records exported in one transaction, so a record reopened meanwhile is never marked."""
    started = time.perf_counter()
    export_id = secrets.token_hex(8)
    created_at = db.iso(now)
    with ctx.db() as conn, db.transaction(conn):
        selection = select_records(conn, filters)
        details = load_details(conn, selection.approved)
        tables = build_tables(selection, details, filters, export_id=export_id, actor=actor, created_at=now)
        data = render_xlsx(tables) if filters.format is ExportFormat.XLSX else render_csv(tables[0])
        stored_name = Storage.new_name(filters.format.value)
        ctx.storage.write_new(ctx.storage.exports, stored_name, data)
        sha256 = hashlib.sha256(data).hexdigest()
        duration_ms = round((time.perf_counter() - started) * 1000)
        conn.execute(
            "INSERT INTO exports (id, created_at, actor, format, filters_json, record_count, stored_name, sha256, "
            "duration_ms) VALUES (?,?,?,?,?,?,?,?,?)",
            (export_id, created_at, actor, filters.format.value, filters.model_dump_json(), len(selection.approved),
             stored_name, sha256, duration_ms),
        )
        for record in selection.approved:
            db.set_status(conn, record.doc.id, DocStatus.EXPORTED, actor=actor, action=AuditAction.EXPORT,
                          note=f"Export {export_id}", at=now)
        db.audit(conn, AuditAction.EXPORT, actor, after=repo.export_display_name(created_at, filters.format.value),
                 note=f"Export {export_id}: {_plural(len(selection.approved), 'approved record', 'approved records')}")
        row = conn.execute("SELECT * FROM exports WHERE id = ?", (export_id,)).fetchone()
    return repo.export_from_row(row)
