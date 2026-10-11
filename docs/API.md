# VaultAI API contract

All shapes are defined in `backend/vaultai/domain.py` and mirrored (generated) in
`frontend/src/api/types.ts`. Names in `code` below are those model names.

## Conventions

- Desktop API base: `http://127.0.0.1:8765/api`. Bound to loopback only.
- Every state-changing request (POST, PATCH, DELETE) must carry header `X-VaultAI: 1`.
  Requests without it get `403 {"code":"csrf","message":...}`. This blocks cross-site
  form posts from other browser tabs (no CORS is ever enabled).
- Requests whose `Host` header is not `127.0.0.1:<port>`, `localhost:<port>`, or the
  Vite dev origin get `421` (DNS-rebinding defense).
- Errors: non-2xx responses carry `ApiError` JSON: `{"code": "...", "message": "..."}`.
  Messages never contain document text, OCR text, or file paths.
- Money is a decimal string ("5328.00"); dates are ISO `YYYY-MM-DD`; datetimes ISO 8601 UTC.
- Responses send `Cache-Control: no-store`, `X-Content-Type-Options: nosniff`,
  `Referrer-Policy: no-referrer`, and a strict CSP (`default-src 'self'`; images allow `blob:` and `data:`).

## System

| Method | Path | Body | Response |
|---|---|---|---|
| GET | `/api/system/status` | | `SystemStatus` |
| GET | `/api/system/metrics` | | `Metrics` |
| GET | `/api/dashboard` | | `Dashboard` |
| GET | `/api/audit?limit=100&doc_id=` | | `AuditEntry[]` (newest first) |
| DELETE | `/api/data` | `DeleteAllRequest` (`{"confirm":"DELETE"}`) | `204`. Deletes every document, derived file, capture, export, and record. Writes one `delete_all` audit row into the fresh database. |

## Intake

| Method | Path | Body | Response |
|---|---|---|---|
| POST | `/api/intake/upload` | multipart: `files` (1..max_batch_files), `source` = `upload`\|`folder` | `IntakeItem[]` (one per file, in order) |
| POST | `/api/intake/samples` | | `IntakeItem[]` for every file under `samples/demo/` |
| GET | `/api/intake` | | `IntakeItem[]` not dismissed, newest first (max 500) |
| DELETE | `/api/intake/{intake_id}` | | `204`. If its document is still `imported`, the document and its file are deleted. Otherwise the row is only dismissed from the queue. |
| POST | `/api/intake/process` | `ProcessRequest` (`intake_ids` null = every `imported` doc) | `ProcessResult` (doc ids queued) |

Preflight never stores a rejected file. A duplicate (same sha256 as an existing document)
returns `preflight: "duplicate"` with `duplicate_of` set and is not stored again.
Folder uploads send each file's relative path as its multipart filename; the server keeps only
a sanitized basename for display.

## Documents

| Method | Path | Body | Response |
|---|---|---|---|
| GET | `/api/documents` | query: `q`, `doc_type` (repeatable), `status` (repeatable), `date_from`, `date_to`, `party`, `deadline_within_days`, `min_confidence`, `sort` (`created_at`\|`doc_date`\|`total_amount`\|`confidence`\|`filename`\|`next_deadline`), `order` (`asc`\|`desc`), `limit` (≤200), `offset` | `DocumentList` |
| GET | `/api/documents/{id}` | | `DocumentDetail` |
| GET | `/api/documents/{id}/pages/{page_no}/image` | | `image/png` (rendered page) |
| GET | `/api/documents/{id}/original` | | original bytes, `Content-Disposition: attachment` |
| PATCH | `/api/documents/{id}/fields/{key}` | `FieldEdit` | `DocumentDetail`. Records a human edit (`origin` becomes `human`; the AI value is kept in `ai_value`), writes an `edit_field` audit row with before/after, re-runs deterministic validation. Editing an `approved`/`exported` record moves it back to `needs_review`. |
| POST | `/api/documents/{id}/review` | `ReviewRequest` | `DocumentDetail`. `approve` → `approved` (approval_kind `human`), `reject` → `rejected`, `defer` → stays `needs_review` with `deferred: true`, `reopen` → `needs_review`. Illegal transitions → `409 {"code":"invalid_transition"}`. Approving a document with unresolved `error`-severity issues requires a non-empty `note`, else `422 {"code":"note_required"}`. |
| POST | `/api/documents/{id}/reprocess` | | `DocumentSummary` (status `processing`). Human edits are preserved and re-applied after extraction. |
| DELETE | `/api/documents/{id}` | | `204`. Deletes raw file, page images, records, FTS entry. Audit rows remain. |
| PATCH | `/api/obligations/{id}` | `ObligationUpdate` | `Obligation` |

`DocumentSummary.stage` is a short human label of the current pipeline step while
`status` is `processing` (e.g. `"Reading pages"`, `"OCR 2/3"`, `"Classifying"`,
`"Extracting fields"`, `"Analyzing clauses"`, `"Validating"`).

## Review

| Method | Path | Body | Response |
|---|---|---|---|
| GET | `/api/review/queue` | | `ReviewQueue`: every `needs_review` and `ready` doc (needs_review first, deferred last), plus `groups` of similar documents (same `doc_type` + `counterparty`, ≥2 docs) for batch review |
| POST | `/api/review/batch` | `BatchReviewRequest` | `BatchReviewResult` (per-doc ok/error; one failure does not abort others) |

## Action Center

| Method | Path | Response |
|---|---|---|
| GET | `/api/actions` | `ActionCenter`. Computed on request from records (nothing is sent anywhere). Items: open obligations with due dates (overdue/≤60 days; `renewal`/`notice`/`expiration` → category `renewal`, `payment` → `payment_due`, `signature` → `unsigned`, others → `obligation`), docs in `needs_review` / `ready`, `missing_required` issues → `missing_info`, `amount_mismatch`/`written_amount_mismatch`/`tax_inconsistent` → `amount_mismatch`, `duplicate_document` → `duplicate`, `missing_signature` → `unsigned`, `risky_clause`/`ambiguous_clause`/`auto_renewal` → `risky_clause`, `prompt_injection`/`hidden_text` → `injection`, `failed` docs → `processing_failure`, approvals in the last 7 days → `recently_approved`. Sorted by severity then due date. |

## Export

| Method | Path | Body | Response |
|---|---|---|---|
| POST | `/api/exports/preview` | `ExportFilters` | `ExportPreview` |
| POST | `/api/exports` | `ExportFilters` | `ExportJob`. Writes the file under `data/exports/`, marks included approved docs `exported`, writes an `export` audit row. |
| GET | `/api/exports` | | `ExportJob[]` newest first |
| GET | `/api/exports/{id}/download` | | file (`.xlsx` or `.csv`), attachment |

Workbook sheets (XLSX): `Export Info`, `Approved Records`, `Needs Review` (only when
`include_needs_review`), `Source Evidence` (only when `include_evidence`), `Processing Metadata`,
`Validation Warnings`, `Obligations and Deadlines`. Every document-derived string is written as
a string cell (never a formula); identifiers use text format `@` so leading zeros survive; money
is numeric with an explicit currency column; dates are real date cells. CSV is the `Approved
Records` view, with cells starting `=`, `+`, `-`, `@`, tab, or CR prefixed by `'`.

## Scanner (desktop)

| Method | Path | Body | Response |
|---|---|---|---|
| GET | `/api/scanner/devices` | | `ScannerDevice[]` (SANE devices via `scanimage -L` when available; the mock scanner when backend is `mock` or `auto` without SANE) |
| POST | `/api/scanner/sessions` | `{"device_id": str}` | `CaptureSession` |
| GET | `/api/scanner/sessions/{id}` | | `CaptureSession` |
| POST | `/api/scanner/sessions/{id}/scan` | `ScanRequest` | `CaptureSession` (1 page appended, 2 when duplex and supported) |
| POST | `/api/scanner/sessions/{id}/pages/{page_id}/retake` | `ScanRequest` | `CaptureSession` (page replaced in place) |
| DELETE | `/api/scanner/sessions/{id}/pages/{page_id}` | | `CaptureSession` |
| GET | `/api/scanner/sessions/{id}/pages/{page_id}/image` | | `image/png` preview |
| POST | `/api/scanner/sessions/{id}/assemble` | `AssembleRequest` (page order = list order; rotation and normalized crop applied server-side) | `IntakeItem` (source `scanner`, document `imported`) |
| DELETE | `/api/scanner/sessions/{id}` | | `204` (cancel; page images deleted) |

## Phone pairing

Desktop side (loopback API):

| Method | Path | Body | Response |
|---|---|---|---|
| POST | `/api/phone/sessions` | | `PhonePairing` (`url` = `http://<lan-ip>:<phone_port>/m/<session_id>#<token>`, `code` = 6 digits shown on both screens, `qr_svg` = inline SVG of `url`). `409 {"code":"phone_disabled"}` when `VAULTAI_PHONE_ENABLED` is false. |
| GET | `/api/phone/sessions/{id}` | | `CaptureSession` (poll for `completed` + `document_id`) |
| DELETE | `/api/phone/sessions/{id}` | | `204` (cancel; token dies immediately) |

Phone side: a **separate** listener on the LAN address in the QR code (`<lan-ip>:<phone_port>`, never `0.0.0.0`; it refuses to start on a non-private address) that serves only these routes and
rejects any client whose IP is not loopback/private/link-local:

| Method | Path | Notes |
|---|---|---|
| GET | `/m/{session_id}` | Self-contained mobile capture page (inline JS/CSS, no external assets). The token travels in the URL fragment, which browsers never send to servers. |
| GET | `/p/{session_id}/status` | header `X-Pairing-Token` → `{"status": ..., "code": ..., "max_pages": n, "expires_at": ...}` |
| POST | `/p/{session_id}/submit` | header `X-Pairing-Token`; multipart `pages` (JPEG/PNG, ≤ phone_max_pages, each ≤ phone_max_image_mb) + `edits` JSON `[{"rotation":0,"crop":[x0,y0,x1,y1]\|null}]` aligned with pages. Server applies edits, assembles one PDF, runs normal preflight + ingest (source `phone`), marks the session `completed`. Single use. |

Token: 32 random bytes, stored only as SHA-256; compared in constant time; valid only while
the session is `active` and before `expires_at` (default 10 minutes).

## Internal seams between backend modules

Lead-owned foundation (edit only additively, and say so in your report):
`domain.py`, `schemas.py`, `config.py`, `db.py`, `storage.py`, `context.py`, `model/base.py`.

Core processing (`ingest`, `parsing`, `classify`, `extraction`, `analysis`, `validation`,
`model/*` adapters, `pipeline`, `metrics`) exposes exactly:

```python
# vaultai/intake/ingest.py
def ingest_bytes(ctx: AppContext, *, filename: str, data: bytes, source: Source, batch_id: str,
                 doc_type_hint: DocType | None = None) -> IntakeItem: ...
    # preflight + dedup + store + documents row (status imported) + intake row + audit.
    # Never raises for bad input: returns preflight rejected/duplicate with a safe error message.

# vaultai/pipeline/queue.py
class JobQueue:                      # satisfies context.Queue
    def __init__(self, ctx: AppContext) -> None: ...
    async def start(self) -> None: ...   # spawns ctx.settings.workers workers; re-queues docs left in `processing`
    async def stop(self) -> None: ...
    def enqueue(self, doc_id: str) -> None: ...  # thread-safe, idempotent for a doc already queued

# vaultai/pipeline/process.py
def request_processing(ctx: AppContext, doc_ids: list[str], *, actor: str, reprocess: bool = False) -> list[str]: ...
    # moves each doc to `processing` (check_transition), audits, enqueues; returns ids queued; skips illegal ones
def process_document(ctx: AppContext, doc_id: str) -> None: ...   # idempotent full pipeline for one doc
def revalidate(ctx: AppContext, conn: sqlite3.Connection, doc_id: str) -> None: ...
    # after a human edit: re-run deterministic validation, recompute document projection columns,
    # risk level, next_deadline, and the FTS row. Does not change status.

# vaultai/model/factory.py
def build_model(settings: Settings) -> ModelAdapter: ...

# vaultai/parsing/ocr.py
def ocr_status(settings: Settings) -> tuple[bool, str | None]: ...   # (available, "tesseract 5.x")

# vaultai/metrics.py
def compute_metrics(ctx: AppContext) -> Metrics: ...   # includes model calls, retries, rejections, tokens

# vaultai/feedback.py
def export_feedback(ctx: AppContext, *, actor: str, now: datetime, include_confirmed: bool = False,
                    include_evidence_text: bool = True, include_document_text: bool = False) -> FeedbackExport: ...
    # reviewed corrections from human-approved documents as owner-only JSONL in data/exports; audited

# vaultai/security/egress.py
def install_egress_guard() -> None: ...   # blocks outbound connections to non-local addresses process-wide
class EgressBlocked(OSError): ...
```

Workflow and surfaces (`app.py`, `security/http.py`, `api/*`, `repo.py`, `review.py`, `search.py`,
`actions.py`, `dashboard.py`, `export/*`, `capture/*`, `phone_app.py`, `deletion.py`) consume
only the seams above plus direct reads of the database tables in `db.py`.
