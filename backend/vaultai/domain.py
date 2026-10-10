"""VaultAI domain model.

Single source of truth for enums, the document review state machine, and every
shape that crosses the HTTP boundary. frontend/src/api/types.ts is generated
from this module by scripts/gen_types.py; never edit that file by hand.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = "2026.10.1"
APP_VERSION = "0.1.0"

# Normalized (x0, y0, x1, y1) in 0..1 page coordinates, origin top-left.
Box = tuple[float, float, float, float]


class DocType(StrEnum):
    CHECK = "check"
    RECEIPT = "receipt"
    INVOICE = "invoice"
    CONTRACT = "contract"
    AGREEMENT_LETTER = "agreement_letter"
    PURCHASE_ORDER = "purchase_order"
    STATEMENT_OF_WORK = "statement_of_work"
    PROPOSAL = "proposal"
    POLICY = "policy"
    ADMIN_FORM = "admin_form"
    CORRESPONDENCE = "correspondence"
    OTHER = "other"


DOC_TYPE_LABELS: dict[DocType, str] = {
    DocType.CHECK: "Check",
    DocType.RECEIPT: "Receipt",
    DocType.INVOICE: "Invoice",
    DocType.CONTRACT: "Contract",
    DocType.AGREEMENT_LETTER: "Agreement letter",
    DocType.PURCHASE_ORDER: "Purchase order",
    DocType.STATEMENT_OF_WORK: "Statement of work",
    DocType.PROPOSAL: "Proposal / quote",
    DocType.POLICY: "Business policy",
    DocType.ADMIN_FORM: "Administrative form",
    DocType.CORRESPONDENCE: "Correspondence",
    DocType.OTHER: "Other business document",
}

# Legal-adjacent types never auto-approve and always carry the issue-spotting disclaimer.
LEGAL_DOC_TYPES: frozenset[DocType] = frozenset(
    {DocType.CONTRACT, DocType.AGREEMENT_LETTER, DocType.STATEMENT_OF_WORK, DocType.POLICY}
)


class DocStatus(StrEnum):
    IMPORTED = "imported"
    PROCESSING = "processing"
    EXTRACTED = "extracted"
    VALIDATED = "validated"
    READY = "ready"
    NEEDS_REVIEW = "needs_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPORTED = "exported"
    FAILED = "failed"


_S = DocStatus
TRANSITIONS: dict[DocStatus, frozenset[DocStatus]] = {
    _S.IMPORTED: frozenset({_S.PROCESSING}),
    _S.PROCESSING: frozenset({_S.EXTRACTED, _S.FAILED}),
    _S.EXTRACTED: frozenset({_S.VALIDATED, _S.FAILED}),
    _S.VALIDATED: frozenset({_S.READY, _S.NEEDS_REVIEW, _S.APPROVED}),
    _S.READY: frozenset({_S.APPROVED, _S.REJECTED, _S.NEEDS_REVIEW, _S.PROCESSING}),
    _S.NEEDS_REVIEW: frozenset({_S.APPROVED, _S.REJECTED, _S.NEEDS_REVIEW, _S.PROCESSING}),
    _S.APPROVED: frozenset({_S.EXPORTED, _S.NEEDS_REVIEW, _S.PROCESSING}),
    _S.REJECTED: frozenset({_S.NEEDS_REVIEW, _S.PROCESSING}),
    _S.EXPORTED: frozenset({_S.EXPORTED, _S.NEEDS_REVIEW, _S.PROCESSING}),
    _S.FAILED: frozenset({_S.PROCESSING}),
}

# Statuses whose records may appear in an export of approved data.
EXPORTABLE: frozenset[DocStatus] = frozenset({_S.APPROVED, _S.EXPORTED})
# Statuses a human acts on in the review queue.
REVIEWABLE: frozenset[DocStatus] = frozenset({_S.READY, _S.NEEDS_REVIEW})


class InvalidTransition(ValueError):
    def __init__(self, current: DocStatus, target: DocStatus) -> None:
        super().__init__(f"cannot move a document from {current} to {target}")
        self.current = current
        self.target = target


def check_transition(current: DocStatus, target: DocStatus) -> None:
    if target not in TRANSITIONS[current]:
        raise InvalidTransition(current, target)


class Source(StrEnum):
    UPLOAD = "upload"
    FOLDER = "folder"
    SAMPLE = "sample"
    SCANNER = "scanner"
    PHONE = "phone"


class PreflightStatus(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    DUPLICATE = "duplicate"


class ValueType(StrEnum):
    TEXT = "text"
    LONG_TEXT = "long_text"
    MONEY = "money"
    DATE = "date"
    IDENTIFIER = "identifier"
    BOOL = "bool"
    ENUM = "enum"


class FieldOrigin(StrEnum):
    RULES = "rules"
    MODEL = "model"
    HUMAN = "human"


class ClassificationMethod(StrEnum):
    RULES = "rules"
    MODEL = "model"
    USER = "user"


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class IssueCode(StrEnum):
    MISSING_REQUIRED = "missing_required"
    LOW_OCR_CONFIDENCE = "low_ocr_confidence"
    LOW_EXTRACTION_CONFIDENCE = "low_extraction_confidence"
    UNREADABLE = "unreadable"
    INVALID_DATE = "invalid_date"
    DATE_ORDER = "date_order"
    CONFLICTING_VALUES = "conflicting_values"
    AMOUNT_MISMATCH = "amount_mismatch"
    WRITTEN_AMOUNT_MISMATCH = "written_amount_mismatch"
    TAX_INCONSISTENT = "tax_inconsistent"
    CURRENCY_MISMATCH = "currency_mismatch"
    UNUSUAL_AMOUNT = "unusual_amount"
    INVALID_IDENTIFIER = "invalid_identifier"
    DUPLICATE_DOCUMENT = "duplicate_document"
    MISSING_SIGNATURE = "missing_signature"
    AUTO_RENEWAL = "auto_renewal"
    RISKY_CLAUSE = "risky_clause"
    AMBIGUOUS_CLAUSE = "ambiguous_clause"
    UNUSUAL_OBLIGATION = "unusual_obligation"
    PROMPT_INJECTION = "prompt_injection"
    HIDDEN_TEXT = "hidden_text"
    UNGROUNDED_VALUE = "ungrounded_value"
    MODEL_OUTPUT_REJECTED = "model_output_rejected"
    MODEL_UNAVAILABLE = "model_unavailable"
    LEGAL_REVIEW_REQUIRED = "legal_review_required"


class ClauseKind(StrEnum):
    TERM = "term"
    RENEWAL = "renewal"
    TERMINATION = "termination"
    PAYMENT = "payment"
    DELIVERABLES = "deliverables"
    CONFIDENTIALITY = "confidentiality"
    INTELLECTUAL_PROPERTY = "intellectual_property"
    LIABILITY = "liability"
    INDEMNITY = "indemnity"
    WARRANTY = "warranty"
    DISPUTE_RESOLUTION = "dispute_resolution"
    GOVERNING_LAW = "governing_law"
    NON_COMPETE = "non_compete"
    DATA_PROTECTION = "data_protection"
    INSURANCE = "insurance"
    SERVICE_LEVEL = "service_level"
    SIGNATURES = "signatures"
    OTHER = "other"


class ClauseRisk(StrEnum):
    NONE = "none"
    ATTENTION = "attention"
    PROFESSIONAL_REVIEW = "professional_review"


class RiskLevel(StrEnum):
    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ObligationKind(StrEnum):
    OBLIGATION = "obligation"
    PAYMENT = "payment"
    DELIVERABLE = "deliverable"
    RENEWAL = "renewal"
    EXPIRATION = "expiration"
    NOTICE = "notice"
    SIGNATURE = "signature"


class ObligationStatus(StrEnum):
    OPEN = "open"
    DONE = "done"
    DISMISSED = "dismissed"


class ReviewAction(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"
    DEFER = "defer"
    REOPEN = "reopen"


class ApprovalKind(StrEnum):
    HUMAN = "human"
    POLICY = "policy"


class AuditAction(StrEnum):
    IMPORT = "import"
    PREFLIGHT_REJECT = "preflight_reject"
    PROCESS = "process"
    AUTO_APPROVE = "auto_approve"
    APPROVE = "approve"
    REJECT = "reject"
    DEFER = "defer"
    REOPEN = "reopen"
    EDIT_FIELD = "edit_field"
    REPROCESS = "reprocess"
    DELETE = "delete"
    DELETE_ALL = "delete_all"
    EXPORT = "export"
    OBLIGATION_UPDATE = "obligation_update"
    CAPTURE = "capture"


class TextMethod(StrEnum):
    PDF_TEXT = "pdf_text"
    OCR = "ocr"
    VISION = "vision"
    PLAIN_TEXT = "plain_text"
    NONE = "none"


class LineFlag(StrEnum):
    HIDDEN = "hidden"
    INJECTION = "injection"


class Route(StrEnum):
    FAST = "fast"
    MODEL = "model"
    VISION = "vision"


class ModelKind(StrEnum):
    MOCK = "mock"
    OPENAI_COMPAT = "openai_compat"


class CaptureKind(StrEnum):
    PHONE = "phone"
    SCANNER = "scanner"


class CaptureStatus(StrEnum):
    ACTIVE = "active"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class ExportFormat(StrEnum):
    XLSX = "xlsx"
    CSV = "csv"


class ActionCategory(StrEnum):
    NEEDS_REVIEW = "needs_review"
    READY = "ready"
    MISSING_INFO = "missing_info"
    RENEWAL = "renewal"
    PAYMENT_DUE = "payment_due"
    OBLIGATION = "obligation"
    AMOUNT_MISMATCH = "amount_mismatch"
    DUPLICATE = "duplicate"
    UNSIGNED = "unsigned"
    RISKY_CLAUSE = "risky_clause"
    INJECTION = "injection"
    PROCESSING_FAILURE = "processing_failure"
    RECENTLY_APPROVED = "recently_approved"


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=False)


# ---------------------------------------------------------------- documents


class Evidence(ApiModel):
    page: int | None = None
    bbox: Box | None = None
    line_id: str | None = None
    text: str | None = None


class PageLine(ApiModel):
    id: str
    text: str
    bbox: Box | None = None
    confidence: float | None = None
    flag: LineFlag | None = None


class PageInfo(ApiModel):
    page_no: int
    width: float
    height: float
    text_method: TextMethod
    ocr_confidence: float | None = None
    has_image: bool
    lines: list[PageLine]


class ExtractedField(ApiModel):
    key: str
    label: str
    value_type: ValueType
    required: bool
    value: str | None
    ai_value: str | None
    raw_text: str | None
    currency: str | None = None
    options: list[str] = Field(default_factory=list)
    confidence: float
    origin: FieldOrigin
    ai_origin: FieldOrigin
    evidence: Evidence | None = None
    edited_by: str | None = None
    edited_at: datetime | None = None
    model_name: str | None = None
    schema_version: str
    extracted_at: datetime


class LineItem(ApiModel):
    idx: int
    description: str
    quantity: Decimal | None = None
    unit_price: Decimal | None = None
    amount: Decimal | None = None
    evidence: Evidence | None = None


class ValidationIssue(ApiModel):
    id: int
    code: IssueCode
    severity: Severity
    message: str
    field_key: str | None = None
    evidence: Evidence | None = None
    created_at: datetime


class Clause(ApiModel):
    id: int
    kind: ClauseKind
    heading: str
    text: str
    risk: ClauseRisk
    risk_reason: str | None = None
    interpretation: str | None = None
    origin: FieldOrigin
    evidence: Evidence | None = None


class Obligation(ApiModel):
    id: int
    doc_id: str
    doc_filename: str
    kind: ObligationKind
    party: str | None = None
    description: str
    due_date: date | None = None
    status: ObligationStatus
    origin: FieldOrigin
    evidence: Evidence | None = None


class SummaryClaim(ApiModel):
    text: str
    evidence: list[Evidence]


class Summary(ApiModel):
    claims: list[SummaryClaim]
    generator: Literal["model", "template"]
    model_name: str | None = None
    generated_at: datetime


class AuditEntry(ApiModel):
    id: int
    at: datetime
    actor: str
    action: AuditAction
    doc_id: str | None = None
    doc_filename: str | None = None
    field_key: str | None = None
    before: str | None = None
    after: str | None = None
    note: str | None = None


class Provenance(ApiModel):
    sha256: str
    original_filename: str
    model_kind: ModelKind | None = None
    model_name: str | None = None
    schema_version: str | None = None
    ocr_engine: str | None = None
    route: Route | None = None
    processed_at: datetime | None = None
    processing_ms: int | None = None


class DocumentSummary(ApiModel):
    id: str
    filename: str
    source: Source
    mime: str
    size_bytes: int
    page_count: int | None = None
    status: DocStatus
    stage: str | None = None
    doc_type: DocType | None = None
    doc_type_confidence: float | None = None
    classification_method: ClassificationMethod | None = None
    title: str | None = None
    counterparty: str | None = None
    doc_date: date | None = None
    total_amount: Decimal | None = None
    currency: str | None = None
    primary_id: str | None = None
    next_deadline: date | None = None
    confidence: float | None = None
    risk_level: RiskLevel
    issue_count: int
    error_count: int
    injection_suspected: bool
    deferred: bool
    approval_kind: ApprovalKind | None = None
    approved_by: str | None = None
    approved_at: datetime | None = None
    created_at: datetime
    processed_at: datetime | None = None
    error: str | None = None


class DocumentDetail(DocumentSummary):
    fields: list[ExtractedField]
    line_items: list[LineItem]
    issues: list[ValidationIssue]
    clauses: list[Clause]
    obligations: list[Obligation]
    summary: Summary | None = None
    pages: list[PageInfo]
    history: list[AuditEntry]
    provenance: Provenance


class TypeCount(ApiModel):
    doc_type: DocType
    count: int


class StatusCount(ApiModel):
    status: DocStatus
    count: int


class DocumentList(ApiModel):
    items: list[DocumentSummary]
    total: int
    by_type: list[TypeCount]
    by_status: list[StatusCount]


# ---------------------------------------------------------------- intake


class IntakeItem(ApiModel):
    id: int
    batch_id: str
    filename: str
    source: Source
    mime: str | None = None
    size_bytes: int
    page_count: int | None = None
    preflight: PreflightStatus
    error: str | None = None
    duplicate_of: str | None = None
    document_id: str | None = None
    doc_status: DocStatus | None = None
    doc_type: DocType | None = None
    stage: str | None = None
    created_at: datetime


class ProcessRequest(ApiModel):
    intake_ids: list[int] | None = None


class ProcessResult(ApiModel):
    queued: list[str]


# ---------------------------------------------------------------- review


class FieldEdit(ApiModel):
    value: str | None = Field(default=None, max_length=4000)
    note: str | None = Field(default=None, max_length=1000)


class ReviewRequest(ApiModel):
    action: ReviewAction
    note: str | None = Field(default=None, max_length=1000)


class BatchReviewRequest(ApiModel):
    doc_ids: list[str] = Field(min_length=1, max_length=200)
    action: ReviewAction
    note: str | None = Field(default=None, max_length=1000)


class BatchItemResult(ApiModel):
    doc_id: str
    ok: bool
    status: DocStatus | None = None
    error: str | None = None


class BatchReviewResult(ApiModel):
    results: list[BatchItemResult]


class ReviewGroup(ApiModel):
    key: str
    doc_type: DocType | None = None
    counterparty: str | None = None
    doc_ids: list[str]


class ReviewQueue(ApiModel):
    items: list[DocumentSummary]
    groups: list[ReviewGroup]


class ObligationUpdate(ApiModel):
    status: ObligationStatus


# ---------------------------------------------------------------- action center


class ActionItem(ApiModel):
    id: str
    category: ActionCategory
    severity: Severity
    title: str
    detail: str | None = None
    doc_id: str | None = None
    doc_filename: str | None = None
    doc_type: DocType | None = None
    due_date: date | None = None
    obligation_id: int | None = None
    created_at: datetime


class CategoryCount(ApiModel):
    category: ActionCategory
    count: int


class ActionCenter(ApiModel):
    items: list[ActionItem]
    counts: list[CategoryCount]


# ---------------------------------------------------------------- dashboard + system


class MoneyTotal(ApiModel):
    currency: str
    approved_payables: Decimal
    approved_receipts: Decimal
    approved_checks: Decimal
    pending_review: Decimal


class Dashboard(ApiModel):
    total_documents: int
    processing: int
    needs_review: int
    ready: int
    approved: int
    rejected: int
    failed: int
    by_type: list[TypeCount]
    upcoming_deadlines: list[Obligation]
    renewals: list[Obligation]
    financial_totals: list[MoneyTotal]
    recent_activity: list[AuditEntry]


class ModelStatus(ApiModel):
    kind: ModelKind
    name: str
    endpoint: str | None = None
    reachable: bool
    vision: bool
    detail: str


class SystemStatus(ApiModel):
    app_version: str
    schema_version: str
    local_only: bool
    egress_guard: bool
    model: ModelStatus
    ocr_available: bool
    ocr_engine: str | None = None
    phone_enabled: bool
    scanner_backends: list[str]
    data_dir: str
    auto_approve_types: list[DocType]


class GpuStatus(ApiModel):
    name: str
    memory_used_mb: float | None = None
    memory_total_mb: float | None = None
    utilization_pct: float | None = None


class DocTiming(ApiModel):
    doc_id: str
    filename: str
    pages: int
    ms: int
    route: Route | None = None


class Metrics(ApiModel):
    documents_processed: int
    pages_processed: int
    avg_ms_per_page: float | None = None
    pages_per_minute: float | None = None
    validation_failure_rate: float | None = None
    human_review_rate: float | None = None
    auto_approval_rate: float | None = None
    field_correction_rate: float | None = None
    avg_export_ms: float | None = None
    cache_hits: int
    cache_misses: int
    load_avg: list[float] | None = None
    rss_mb: float | None = None
    gpu: GpuStatus | None = None
    recent: list[DocTiming]


# ---------------------------------------------------------------- export


class ExportFilters(ApiModel):
    doc_types: list[DocType] = Field(default_factory=list)
    date_from: date | None = None
    date_to: date | None = None
    include_needs_review: bool = False
    include_evidence: bool = True
    format: ExportFormat = ExportFormat.XLSX


class ExportRow(ApiModel):
    doc_id: str
    filename: str
    doc_type: DocType | None = None
    status: DocStatus
    counterparty: str | None = None
    doc_date: date | None = None
    primary_id: str | None = None
    total_amount: Decimal | None = None
    currency: str | None = None
    approval_kind: ApprovalKind | None = None
    approved_by: str | None = None
    human_edits: int


class ExportPreview(ApiModel):
    record_count: int
    needs_review_count: int
    by_type: list[TypeCount]
    sheets: list[str]
    rows: list[ExportRow]
    warnings: list[str]


class ExportJob(ApiModel):
    id: str
    created_at: datetime
    actor: str
    format: ExportFormat
    filters: ExportFilters
    record_count: int
    filename: str
    sha256: str
    duration_ms: int


# ---------------------------------------------------------------- capture


class ScannerDevice(ApiModel):
    id: str
    name: str
    backend: Literal["sane", "mock"]
    duplex: bool


class CapturePage(ApiModel):
    id: int
    idx: int
    width: int
    height: int


class CaptureSession(ApiModel):
    id: str
    kind: CaptureKind
    status: CaptureStatus
    device_id: str | None = None
    pages: list[CapturePage]
    created_at: datetime
    expires_at: datetime | None = None
    intake_id: int | None = None
    document_id: str | None = None


class ScanRequest(ApiModel):
    duplex: bool = False
    dpi: int = Field(default=200, ge=75, le=600)
    color: Literal["color", "gray"] = "gray"


class PageEdit(ApiModel):
    id: int
    rotation: Literal[0, 90, 180, 270] = 0
    crop: Box | None = None


class AssembleRequest(ApiModel):
    title: str | None = Field(default=None, max_length=120)
    doc_type_hint: DocType | None = None
    pages: list[PageEdit] = Field(min_length=1, max_length=100)


class PhonePairing(ApiModel):
    session: CaptureSession
    url: str
    code: str
    qr_svg: str


class DeleteAllRequest(ApiModel):
    confirm: Literal["DELETE"]


class ApiError(ApiModel):
    code: str
    message: str
