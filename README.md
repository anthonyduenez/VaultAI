# VaultAI

Local document AI for business. Private documents, verified action.

VaultAI turns a pile of business paperwork (checks, receipts, invoices, contracts, purchase orders, policies, memos, forms) into verified records, a review queue, a deadline list, and an auditable Excel export. Everything runs on one machine. The target device is an ASUS Ascent GX10 (NVIDIA DGX Spark) running a local Nemotron model. No document, page image, or extracted value is ever sent to a cloud API.

The working rule is to automate the routine, surface the risky, and keep the human in control. Clean receipts can be approved by policy. Anything with a warning (a total that does not add up, a missing signature, an auto-renewal clause, hidden text, text that tries to instruct an AI) stops in the review queue until a person decides.

This is a hackathon entry for the ASUS Local AI Track. It is an MVP, not a production system. The numbers below were measured on the development Mac, not on a GX10. See [Known limitations](#known-limitations) and [SECURITY.md](SECURITY.md) before you trust it with real paperwork.

## What it does

| Step | What happens |
| --- | --- |
| Capture | Drop files or a folder, feed pages from a scanner, or photograph pages with a phone over the LAN. Bundled fictional samples cover every document type. |
| Preflight | Byte-level checks before anything is stored. Spoofed files, encrypted or active PDFs, decompression bombs, and duplicates are refused. |
| Parse | A sandboxed subprocess renders pages and reads the text layer. Pages without text go to tesseract OCR, up to four pages at once. |
| Classify and extract | Rules pick the document type and read the fields for that type. A local model is asked only for what the rules could not read, and every model value must cite and quote a line of the document. |
| Validate | Deterministic rules check arithmetic, dates, identifiers, signatures, duplicates, contract clauses, and prompt injection. |
| Review | Each document lands in `approved`, `ready`, or `needs_review`. A reviewer corrects fields, approves, rejects, or defers. The AI value is kept beside every human edit. |
| Track | Obligations with due dates feed the Action Center and the dashboard. |
| Export | Approved records go to an XLSX workbook or CSV on this device. No cell is ever a formula. |

## Architecture

```mermaid
flowchart LR
    A[Capture<br/>upload, folder,<br/>scanner, phone] --> B[Preflight<br/>bytes only]
    B --> C[Parse<br/>sandboxed subprocess]
    C --> D[OCR<br/>tesseract]
    D --> E[Classify]
    E --> F[Extract]
    F --> G[Validate<br/>rules]
    G --> H[Review<br/>human or policy]
    H --> I[Track<br/>Action Center]
    H --> J[Export<br/>XLSX or CSV]
    E -. only when rules are unsure .-> M[(Local model<br/>OpenAI-compatible)]
    F -. only for missing fields .-> M
    F --> S[Summarize and<br/>find obligations] -.-> M
```

Processes on the machine:

| Process | Binds to | Purpose |
| --- | --- | --- |
| Desktop API and built frontend | `127.0.0.1:8765` | FastAPI app. Serves `/api/*` and, when `frontend/dist` exists, the React UI. Refuses requests whose `Host` header is not local and state changes without the `X-VaultAI: 1` header. |
| Phone listener (optional) | `<lan-ip>:8766` | A separate FastAPI app with three routes. Rejects any client that is not on a loopback, private, or link-local address. Off by default. |
| Parser worker | none | `python -I vaultai/parsing/worker.py`, one subprocess per document, with CPU and file-size limits, a 60 s timeout, and an address-space cap on Linux. |
| tesseract | none | Runs as a subprocess on the PNG the parser produced, never on the uploaded bytes. |
| Local model server | your choice, local address only | Any OpenAI-compatible server. VaultAI talks to it over HTTP with proxies disabled. A public hostname is refused at startup. |

Data lives under `data/` (configurable with `VAULTAI_DATA_DIR`), created with owner-only permissions. The SQLite database is `data/vaultai.db`. Raw files, rendered pages, capture pages, and exports are in separate subdirectories with server-generated names.

## Repository layout

```
.env.example            every VAULTAI_* setting with its default
backend/
  vaultai/              the Python package (API, pipeline, model adapters, export, capture)
  tests/                pytest suite (core/ for the pipeline, surfaces/ for the API)
  requirements.txt      runtime dependencies
  requirements-dev.txt  runtime plus pytest
docs/
  API.md                HTTP contract, generated TypeScript types mirror it
  DEMO.md               click-by-click demo script
frontend/
  src/                  React 19 + TypeScript app (Vite)
  dist/                 production build, served by the backend when present
samples/
  demo/                 13 fictional documents loaded by Import & Scan > Demo samples
  scanner/              3 documents the mock scanner feeds page by page
  adversarial/          5 files that preflight must reject or deduplicate
  expected.json         ground truth for every sample
scripts/
  eval_samples.py       scores the pipeline against expected.json
  gen_types.py          regenerates frontend/src/api/types.ts from domain.py
SECURITY.md             security model, threat model, retention, known gaps
```

## Setup

You need Python 3.11 or newer, Node.js (24.14 was used here), and tesseract. SANE is optional and only for a physical scanner.

Install the system tools.

On the GX10 or any Ubuntu machine:

```sh
sudo apt install tesseract-ocr
sudo apt install sane-utils      # only if you will attach a scanner
```

On macOS:

```sh
brew install tesseract
```

Install the Python and Node dependencies and create your configuration:

```sh
python3 -m venv backend/.venv
backend/.venv/bin/pip install -r backend/requirements-dev.txt
cd frontend && npm install && cd ..
cp .env.example .env
```

`.env` is read from the repository root at startup. Every value in it is the built-in default, so the copy works unchanged. Set `VAULTAI_ENV_FILE` to read a different file.

## Run

Start the backend. It creates the data directory and database if they are missing.

```sh
cd backend && .venv/bin/python -m vaultai
```

For development with hot reload, start the Vite server in a second terminal and open http://127.0.0.1:5173. Vite proxies `/api` to the backend on 8765 and rewrites the `Host` header to `127.0.0.1:8765`, which the API requires. The API does not accept the dev server's own host.

```sh
cd frontend && npm run dev
```

For production mode, build the frontend once. The backend then serves the UI itself at http://127.0.0.1:8765.

```sh
cd frontend && npm run build
```

Stop the backend with Ctrl-C. Both ports and the host are settings (`VAULTAI_HOST`, `VAULTAI_PORT`). `VAULTAI_HOST` must be a loopback or private address, or startup fails.

### Initialize the database

Startup does this automatically. To do it without starting the server:

```sh
cd backend && .venv/bin/python -m vaultai init-db
```

The command prints the data directory it prepared.

### Load the sample documents

In the UI, open **Import & Scan**, choose the **Demo samples** tab, and click **Import sample folder**. Then click the **Process** button above the queue.

From the shell, this imports and processes the same 13 files without the UI:

```sh
cd backend && .venv/bin/python -m vaultai load-samples
```

Running it twice is safe. The second run reports `0 new sample documents (13 already present)` because preflight deduplicates by SHA-256.

## Local model configuration

VaultAI ships with two model backends. Set `VAULTAI_MODEL_BACKEND` in `.env`.

| Backend | What it is |
| --- | --- |
| `mock` (default) | No language model at all. Classification and extraction come from the rules. The summary is a template that restates verified facts with citations. Every number in this README was measured in this mode. |
| `openai_compat` | Any local server that speaks the OpenAI chat-completions API. Written for NVIDIA NIM, vLLM, TensorRT-LLM (`trtllm-serve`), llama.cpp server, and Ollama's `/v1` route. Tested so far only against a local fake OpenAI-compatible server. |

Settings for `openai_compat`:

```sh
VAULTAI_MODEL_BACKEND=openai_compat
VAULTAI_MODEL_ENDPOINT=http://127.0.0.1:8000/v1
VAULTAI_MODEL_NAME=nvidia/NVIDIA-Nemotron-Nano-9B-v2
VAULTAI_MODEL_API_KEY=            # only if your local server needs one
VAULTAI_MODEL_CONTEXT_TOKENS=8192
VAULTAI_MODEL_MAX_TOKENS=1024
VAULTAI_MODEL_TIMEOUT_S=60
VAULTAI_MODEL_TEMPERATURE=0
VAULTAI_MODEL_JSON_MODE=true      # sends response_format=json_object
VAULTAI_MODEL_VISION=false        # true if the model accepts page images
VAULTAI_MODEL_SYSTEM_PREFIX=      # see below
```

What the model is asked to do, and what it is not allowed to do:

- The rules classify every document first. The model is asked to classify only when the rules are unsure (confidence below 0.6 or a small margin between the top two types), and only from the first 60 visible lines.
- The model is asked to extract only the required fields the rules missed or read with confidence below `VAULTAI_REVIEW_CONFIDENCE_THRESHOLD` (0.75). It sees the lines nearest to those fields' labels, within the context budget.
- For every document the model is asked to summarize and list obligations, one chunk at a time. Chunks run one after another.
- Every value the model returns must name a line id it was shown and quote text that is on that line. Values that fail this check are discarded and logged as `ungrounded_value`. Output that does not match the closed JSON schema is discarded whole and logged as `model_output_rejected`.
- Model output never auto-approves, whatever the thresholds. Auto-approval requires the rules-only route (no model read a page, chose the type, or was asked for a field) and every required field to come from the rules or a human edit. Model confidence is also capped at 0.85, and a model value never replaces a rules value that is more confident.
- Lines flagged as hidden or as prompt injection are never sent to the model.
- There is no code path from model output to a status change, an approval, a deletion, or a file. The model adapter only returns JSON.

Endpoint and failure rules:

- The endpoint host must be `localhost` or a loopback, RFC 1918, link-local, or IPv6 ULA address. Anything else fails at startup with `ConfigError`. The check runs again before every request.
- Proxy environment variables are ignored (`trust_env=False`), redirects are not followed, and the in-process egress guard blocks any socket connect to a non-local address.
- If the model is unreachable or answers with an error, the document keeps its rules-only results, gets a `model_unavailable` warning, and goes to `needs_review`. There is no fallback to a cloud API and no silent fallback to the mock backend.
- **Settings & Privacy** shows whether the endpoint is reachable and which model it serves.

`VAULTAI_MODEL_SYSTEM_PREFIX` is prepended as the first line of the system prompt on every request. Nemotron reasoning models accept `/no_think` there to skip the reasoning block, which saves tokens and time on short JSON tasks. If a model emits `<think>` blocks anyway, the reply parser strips them before it looks for the JSON object.

`VAULTAI_MODEL_VISION=true` enables one more use. When a page has no text layer and no OCR engine is available, the page image is sent to the model for transcription. A document read this way takes the vision route, so it can never auto-approve. When tesseract is installed, OCR is used and the vision path is not.

## Supported document types

Every type has a fixed field schema in `backend/vaultai/schemas.py`. The table lists the required fields and the main optional ones. Legal types (contract, agreement letter, statement of work, policy) always carry an issue-spotting disclaimer, and putting one in `VAULTAI_AUTO_APPROVE_TYPES` fails at startup.

| Type | Required fields | Also extracted |
| --- | --- | --- |
| Check | payee, amount, check date, check number | payer, bank, written amount, routing and account numbers, memo, signature present |
| Receipt | merchant, date, total | address, receipt number, subtotal, tax, payment method, line items |
| Invoice | vendor, invoice number, invoice date, total | address, due date, bill to, PO number, terms, subtotal, discount, tax, line items |
| Purchase order | PO number, buyer, vendor, order date, total | delivery date, ship to, terms, subtotal, tax, line items |
| Proposal / quote | vendor, date, total | quote number, client, valid until, subtotal, tax, line items |
| Contract | title, parties, effective date, signature status | expiration, auto-renewal, renewal terms, notice period and deadline, termination, payment terms, value, governing law, dispute resolution, signatories, clauses, obligations |
| Agreement letter | sender, recipient, letter date, acceptance status | organizations, subject, agreement statement, effective date, compensation, terms, acceptance deadline, termination, obligations |
| Statement of work | title, client, provider, effective date | end date, total fees, payment terms, signature status, dated deliverables |
| Business policy | title, effective date | policy number, version, owner, next review, numbered rules as obligations |
| Administrative form | form title | form number, submitted by, date, signature present |
| Correspondence | from, date | to, subject, deadlines as obligations |
| Other | none | title, date, organization, amount, reference |

Every document also gets the generic key-value pairs the rules find, full-text search over its visible text, and a currency field.

Validation rules, from `backend/vaultai/validation/rules.py`:

- Required field missing, low extraction confidence, low OCR confidence, no readable text.
- Dates that are not real calendar dates, or a due date before an issue date.
- Line items that do not add to the subtotal, totals that do not equal subtotal minus discount plus tax, tax that does not match a printed rate, more than one currency.
- Checks whose written amount or MICR line disagrees with the printed values, routing numbers that fail the ABA checksum.
- Amounts above `VAULTAI_UNUSUAL_AMOUNT` on checks and receipts, or more than three times the counterparty's usual total on other types.
- Probable duplicates of a document already in VaultAI, by counterparty plus identifier (or merchant, date, and total for receipts).
- Missing or partial signatures.
- Contract clauses that auto-renew, cap no one's liability, indemnify one way, restrict competing or hiring, let only one party terminate for convenience, or rely on undefined standards such as "commercially reasonable efforts".
- Hidden text, prompt injection, and model values that contain links, paths, or commands.

## Review policy

`decide()` in `backend/vaultai/pipeline/process.py` holds the whole policy:

1. Any warning or error issue, including injection and hidden text, sends the document to `needs_review`.
2. A required field that is missing or below `VAULTAI_REVIEW_CONFIDENCE_THRESHOLD` (0.75) sends it to `needs_review`.
3. If the type is in `VAULTAI_AUTO_APPROVE_TYPES` (default `receipt`), the document took the rules-only route, every required field came from the rules or a human, and every required field has confidence of at least `VAULTAI_AUTO_APPROVE_MIN_CONFIDENCE` (0.9), the document is `approved` with approval kind `policy`, and an `auto_approve` audit row records the rule that applied.
4. Otherwise the document is `ready` for one-click human approval.

Set `VAULTAI_AUTO_APPROVE_TYPES=` (empty) to review everything. A legal type in that list is refused at startup with `ConfigError`. The same checks (local model endpoint, local host, no legal auto-approval) run whenever a `Settings` object is built, not only when `.env` is read.

Review actions follow the status table in `backend/vaultai/domain.py`. **Approve**, **Reject**, and **Defer** are allowed from `ready` or `needs_review`. **Reopen** is allowed from `approved`, `exported`, or `rejected`. Anything else returns 409 with "Not allowed while the document is <status>." Field edits are accepted in `ready`, `needs_review`, `approved`, `exported`, and `rejected`. Approving a document that still has error-level issues requires a reviewer note. Editing an approved or exported record moves it back to `needs_review`.

## Scanner setup

VaultAI drives SANE scanners through `scanimage`. Install `sane-utils`, confirm `scanimage -L` lists your device, and leave `VAULTAI_SCANNER_BACKEND=auto`. Open **Import & Scan**, choose the **Scanner** tab, pick the device, set resolution, color, and duplex, and click **Scan page** for each page. Rotate, crop, reorder, or retake pages, give the document a title and optional type, then assemble it. The pages become one PDF and enter the normal intake path.

Without SANE, `auto` falls back to the mock scanner, which feeds the three PDFs in `samples/scanner/` page by page with a slight skew and speckle. Set `VAULTAI_SCANNER_BACKEND=none` to hide the tab. Use your operating system's scan utility and drop the resulting PDF on the **Files** tab if you prefer.

The SANE path was not tested on real hardware during this hackathon. The mock scanner was tested end to end, including OCR of a two-page capture with tesseract 5.5.3.

## Phone capture

Phone capture is off by default. To enable it, set `VAULTAI_PHONE_ENABLED=true` in `.env` and restart. VaultAI then opens a second listener on the LAN address it puts in the QR code, port 8766 (`VAULTAI_PHONE_PORT`). It binds only that address and refuses to start if the address is not private, loopback, or link-local. Set `VAULTAI_PHONE_HOST` to the LAN IP you want in the QR code if auto-detection picks the wrong interface. Set `VAULTAI_PHONE_ENABLED=false` and restart to disable it again.

Pairing workflow:

1. Open **Import & Scan**, choose the **Phone** tab, and click the start button. The desktop shows a QR code, a URL, and a 6-digit code.
2. Scan the QR code with the phone. The phone must be on the same local network. The page shows the same 6-digit code. Confirm they match.
3. Take photos. Rotate or crop each page on the phone. Click **Send pages to VaultAI**.
4. The desktop polls the session, imports the assembled PDF with source `phone`, and the Import queue updates.

The link carries a 32-byte random token in the URL fragment, which the browser never sends to a server. The phone sends the token in the `X-Pairing-Token` header. The server stores only its SHA-256, compares in constant time, and the session is single use with a 10-minute expiry (`VAULTAI_PHONE_SESSION_TTL_S`). An unknown, used, expired, or cancelled session returns 404 on every route. EXIF data, including GPS, is dropped when the page is re-encoded. Photos are limited to 20 pages of 12 MB each. The listener is plain HTTP on the LAN. See [SECURITY.md](SECURITY.md) for what that means.

## Local-only privacy guarantees

What the code guarantees, and how:

- The desktop API binds to `127.0.0.1` by default and refuses any other `VAULTAI_HOST` that is not loopback or private. Requests whose `Host` header is not the local address get 421, which stops DNS rebinding. State-changing requests without the `X-VaultAI: 1` header get 403, which stops cross-site form posts. No CORS is enabled.
- The egress guard (`VAULTAI_EGRESS_GUARD=true`) patches `socket.connect` and `socket.connect_ex` for the whole process so a connection to any address that is not loopback, RFC 1918, link-local, or IPv6 ULA raises `EgressBlocked` before it reaches the network. Verified by connecting to `1.1.1.1:443` from within the process. `lsof` on the running app showed no outbound connections during the end-to-end demo.
- The model endpoint must be a local address, checked at startup and before every request. Proxies are ignored.
- The frontend loads no external assets. The content security policy is `default-src 'self'`, with `blob:` and `data:` images and inline styles allowed. The phone page is self-contained and allows only nonce-tagged inline script and style.
- Access logs are off. The server log records document ids and stages, never document text. API error messages never include document text, OCR text, or file paths.
- No telemetry, no update check, no crash reporter.

What it does not guarantee:

- The egress guard lives inside the Python process. A compromised dependency or a different process is not covered. Add an OS firewall rule on the GX10 too.
- The phone listener accepts any device on the LAN that knows a live session token. Anyone who can read traffic on your LAN can see the photos in transit.
- Your local model server is a separate program with its own logging. VaultAI sends it document text. Check how it is configured.

## Security model summary

Documents are untrusted input at every layer. Preflight inspects bytes, not client MIME types, and refuses spoofed files, encrypted PDFs, PDFs with JavaScript, launch actions, embedded files, or XFA, and images over the pixel limit. Parsing runs in an isolated interpreter with resource limits and a timeout, and every failure becomes a fixed reason token. Every line of text is screened for hidden rendering and for instruction patterns, and quarantined lines never reach the model. Model output is parsed into a closed schema and must cite the document. The reviewer, not the model, changes status. Exports write every document-derived string as a string cell. [SECURITY.md](SECURITY.md) has the full threat model and the known gaps.

## Data retention and deletion

Everything VaultAI stores is under `VAULTAI_DATA_DIR` (default `data/`). Nothing is written anywhere else except `data/tmp` scratch files that are removed after use.

| What | Where | Removed by |
| --- | --- | --- |
| Original file | `data/raw/<random>.<ext>` | document delete, delete all |
| Rendered page PNGs | `data/derived/<doc_id>/` | document delete, delete all |
| Fields, line items, issues, clauses, obligations, summary, search index | `data/vaultai.db` | document delete, delete all |
| OCR and model response cache | `data/vaultai.db` | delete all |
| Scanner and phone session pages | `data/captures/<session>/` | assemble, cancel, delete all |
| Exports | `data/exports/<random>.xlsx` or `.csv` | delete all |
| Audit log | `data/vaultai.db` | delete all |

Delete one document from its page with **Delete**, then **Delete permanently**. This removes the file, the page images, every derived row, the search entry, and the intake rows that named the file (including duplicate rows that pointed at it). The document's audit rows stay as the record of what happened, but their `before`, `after`, and `note` columns are set to NULL, so edited values and reviewer notes do not outlive the document. Each row keeps its action, actor, time, document id, and field key. The `delete` row's note is only `sha256 <first 12 hex characters>`, with no filename. One exception: `preflight_reject` rows for files that were never stored keep "filename: reason", because there is no document to delete.

Delete everything from **Settings & Privacy** by typing `DELETE` in the danger zone and clicking **Delete all local data**, or from the shell:

```sh
cd backend && .venv/bin/python -m vaultai delete-data --yes
```

Without `--yes` the command prints what it would delete and exits with status 1. With it, every file under the data directory is removed and the database is recreated with a single `delete_all` audit row. Stop the server first so no worker is mid-document.

## Exports

The **Export** page previews what a workbook will contain before you write it. XLSX workbooks have sheets `Export Info`, `Approved Records`, `Processing Metadata`, `Validation Warnings`, and `Obligations and Deadlines`, plus `Source Evidence` (per-field page, bounding box, line id, quoted text, origin, AI value, editor) and `Needs Review` when those options are on. Only approved records are in `Approved Records`. Each row labels its approval as `Human` or `Auto-approval policy`. CSV is the approved sheet only.

Every document-derived string is written as a string cell, identifiers use the `@` text format so leading zeros survive, money is numeric with a separate currency column, and dates are real date cells. CSV cells starting with `=`, `+`, `-`, `@`, tab, or CR are prefixed with `'`. The end-to-end check opened a demo export and found zero formula cells. Each export is hashed, listed in **Export history**, and recorded in the audit log, and the included documents move to `exported`.

## Extraction limitations

- Field reading is rules first. The rules were written against the fictional samples and common US layouts. A layout they have not seen can leave required fields blank, which routes the document to review rather than guessing.
- Table extraction on OCR'd pages can miss rows. The two-page scanned Tidewater invoice in the demo set lands in review with an amount mismatch because dense rows were not all read.
- Signature detection looks for drawn curves and small embedded images in PDFs. Scanned pages and photos report signature status `unknown`.
- Pages rotated 90 or 270 degrees are rendered upright, but sideways text can merge into one line.
- Dates like `03/04/2026` follow `VAULTAI_DATE_ORDER` (`MDY` by default). Set `DMY` for European paperwork.
- Contract analysis finds headings, clauses, parties, dates, and a fixed set of risk patterns. It is issue spotting, not a legal reading.

## Legal disclaimer

VaultAI is a document assistant. It is not legal, tax, accounting, or financial advice, and it is not a substitute for a professional. Clause flags are issue spotting for a person to review. Every export carries this notice. Verify extracted values against the original document before you act on them.

## Demo

[docs/DEMO.md](docs/DEMO.md) walks through the 14-step demo with the exact clicks and sample file names. The short version is to load the demo samples, watch the receipt auto-approve, correct and approve the Acme invoice whose total is off by 90 dollars, see the QuickShip invoice's hidden and visible injection text quarantined, read the Brightpath MSA's clauses and its 2026-11-01 non-renewal deadline in the Action Center, scan two pages with the mock scanner, capture a page from a phone, export approved records to XLSX, then delete everything.

## Tests

```sh
cd backend && .venv/bin/python -m pytest          # 199 passed
cd frontend && npm test                            # 46 passed in 12 files
cd frontend && npm run build                       # type check and bundle
backend/.venv/bin/python scripts/eval_samples.py   # scores every sample against expected.json
backend/.venv/bin/python scripts/gen_types.py --check   # exit 1 if types.ts is stale
```

`eval_samples.py` on the development Mac: 16 of 16 document types, 153 of 153 fields, 21 of 21 obligations, every status and issue set as expected, 5 of 5 adversarial files rejected or deduplicated, 16 documents in 1.6 s with the mock backend (about 95 ms per page). Run after any change to the rules. Regenerate the TypeScript types after editing `domain.py` with `scripts/gen_types.py`.

Other timings on the development Mac: OCR of a 6-page scan went from 3.05 s to 1.31 s for `process_document` when OCR started running four pages at once. Reviewers measured on synthetic data that the export preview went from 76 ms to 16 ms at 1,000 approved records, and the dashboard from 1,064 ms to 32 ms at 2,000 documents after `doc_id` indexes were added.

## Known limitations

- Not verified on a GX10 or against a real Nemotron server. The `openai_compat` adapter was tested against a local fake OpenAI-compatible server. A real SANE scanner was not tested.
- The two-page scanned invoice can land in review with an amount mismatch because OCR misses table rows.
- Signature status is `unknown` on scans and photos.
- Sideways text on rotated pages can merge into one line.
- Analysis chunks are sent to the model one at a time, so a long contract takes several model calls in sequence. OCR pages run four at a time, but model calls do not.
- The phone listener is plain HTTP on the LAN.
- The parser memory cap (`VAULTAI_PARSE_MEMORY_MB`) uses `RLIMIT_AS`, which macOS does not enforce. On macOS only the CPU limit and the timeout bound the parser. The cap works on Linux, including the GX10.
- The egress guard is an in-process socket patch, not a firewall.
- There are no user accounts. VaultAI is a single-operator local app, and `VAULTAI_REVIEWER_NAME` is the name written on approvals.

## Future improvements

- TLS for the phone listener with a self-signed certificate pinned in the QR code.
- Run analysis chunks in parallel against a batching model server.
- Signature detection on scans and photos with a small local vision model.
- Rotation detection before OCR for sideways pages.
- Row recovery for dense OCR'd tables using column alignment.
- Package the backend and the built frontend as one systemd unit for the GX10, with a firewall rule that allows only the model port.
- Per-type extraction schemas that an operator can edit without touching code.
