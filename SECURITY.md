# VaultAI security model

This page describes what VaultAI defends against, how each defense is built, what it stores and for how long, and where the gaps are. It is written for the person who decides whether to put real paperwork into it. VaultAI is a hackathon MVP. It is not a production-grade system and has not had an outside security review.

Every claim below points at the file that implements it. Where a control was exercised, the check was run on the development Mac, not on a GX10.

## Assets and trust boundaries

Assets, most sensitive first:

1. The documents themselves. Bank details, signatures, contract terms, employee names.
2. Extracted records, review decisions, and exports.
3. The audit log, which must stay truthful.
4. The machine VaultAI runs on.

Trust boundaries:

| Boundary | Trusted side | Untrusted side |
| --- | --- | --- |
| Upload | the FastAPI app | file bytes, filenames, client MIME types |
| Parser subprocess | the app | the parser's interpreter and the libraries that decode PDFs and images |
| Document text | the validation rules | every line of text, visible or hidden |
| Model adapter | the pipeline and the grounding gate | the model's output |
| Desktop API | the operator's browser on this machine | any other origin, tab, or host on the network |
| Phone listener | the desktop | any device on the LAN |
| Spreadsheet | the operator | whatever Excel does with cell contents |

There is one operator. VaultAI has no accounts, sessions, or roles. `VAULTAI_REVIEWER_NAME` is a label written on approvals, not an identity.

## Threat model

| Threat | Control | Code | Residual risk |
| --- | --- | --- | --- |
| Document text leaves the machine | Egress guard patches `socket.connect` and `socket.connect_ex` process-wide and refuses any non-local address. Model endpoint must be local, checked at startup and before every request. `httpx` client runs with `trust_env=False` and no redirects. No external assets in either web page. Access logs off. | `security/egress.py`, `config.py`, `model/openai_compat.py`, `security/http.py` | In-process only. A compromised native library or another process is not covered. Add a host firewall rule. |
| A malicious file exploits the PDF or image decoder | Preflight reads magic bytes, rejects spoofed extensions, encrypted PDFs, PDFs with `/JavaScript`, `/JS`, `/Launch`, `/EmbeddedFile`, `/RichMedia`, or `/XFA` (including names inside Flate streams), and images whose header dimensions exceed `VAULTAI_MAX_IMAGE_PIXELS`. Parsing runs in `python -I` with an empty environment, the temp dir as cwd, `RLIMIT_CPU`, `RLIMIT_FSIZE`, `RLIMIT_CORE=0`, `RLIMIT_AS` on Linux, and a 60 s timeout. Any failure becomes a fixed token such as `timeout` or `crashed`. | `intake/preflight.py`, `parsing/isolate.py`, `parsing/worker.py` | The subprocess is not a container. It runs as the same user and can read the data directory. macOS ignores `RLIMIT_AS`. |
| Oversized input exhausts disk or memory | `VAULTAI_MAX_FILE_MB` (25), `VAULTAI_MAX_PAGES` (50), `VAULTAI_MAX_BATCH_FILES` (200), text files capped at 2 MB, Pillow decompression-bomb warnings raised as errors, multipart reads capped at the limit plus one byte. | `intake/preflight.py`, `security/http.py`, `parsing/worker.py` | None known for a single operator. |
| A document instructs the AI | Every line is screened against the patterns in `validation/injection.py` (ignore instructions, you are now, system prompt, approve all, without review, role markers, exfiltration verbs with URLs, shell commands, tool calls). Text that is off-page, under 0.4% of the page height, or has no luminance contrast with its background is flagged hidden. Flagged lines are never sent to the model, raise `prompt_injection` or `hidden_text` warnings, and force `needs_review`. The system prompt tells the model the document is data, document tags and chat role markers inside the text are neutralized, and control characters are stripped. | `validation/injection.py`, `parsing/worker.py`, `model/prompts.py`, `pipeline/process.py` | Pattern lists miss novel phrasing. The model still sees the unflagged text and can be misled by it. The grounding gate limits what that can change. |
| The model fabricates or acts | Adapters return JSON only. They have no access to the database, the filesystem, or any tool. Output is parsed into a closed pydantic schema with `extra="forbid"`. Every kept value must cite a line id the model was shown and quote text that is on that line. Money and dates must parse and must appear in the quote. Model confidence is capped at 0.85 and a model value never replaces a more confident rules value. Values containing links, paths, or commands are flagged. Auto-approval requires the rules-only route and required fields that came from the rules or a human, so model or vision output can never auto-approve whatever the thresholds. No code path leads from model output to a status, an approval, a deletion, or a file. | `model/base.py`, `model/grounding.py`, `validation/rules.py`, `pipeline/process.py` | A grounded but wrong value (right quote, wrong field) reaches the reviewer. That is what review is for. |
| Another website drives the local API | `Host` header must be `127.0.0.1:<port>`, `localhost:<port>`, or the configured host, else 421. The Vite dev proxy rewrites `Host` to `127.0.0.1:8765`, so the dev server's own host is not accepted. Every POST, PUT, PATCH, and DELETE must carry `X-VaultAI: 1`, else 403. Browsers do not add custom headers to cross-site form posts, and no CORS is enabled. Responses carry `default-src 'self'`, `frame-ancestors 'none'`, `X-Frame-Options: DENY`, `nosniff`, `no-referrer`, and `no-store` on `/api`. OpenAPI and docs routes are disabled. | `security/http.py`, `app.py` | A malicious program already running as the operator can call the API. That is out of scope for a local app. |
| Someone on the LAN uploads into the phone listener | The listener is off by default. When on, it serves only `/m/{id}`, `/p/{id}/status`, and `/p/{id}/submit`, and rejects any client IP that is not loopback, private, or link-local. Sessions use a 32-byte random token kept in the URL fragment (never sent to a server) and the `X-Pairing-Token` header. The server stores only the SHA-256 of the token and compares in constant time. Sessions are single use, expire after 10 minutes, die on cancel, and every unknown, used, expired, or cancelled session answers 404 on every route. A 6-digit code is shown on both screens so the operator can confirm the pairing. Uploads are limited to 20 pages of 12 MB, must be JPEG or PNG by magic bytes, and EXIF (including GPS) is dropped. Submitted pages go through the full preflight path. | `phone_app.py`, `capture/phone.py`, `security/http.py` | Plain HTTP. Anyone who can read LAN traffic can see the photos and the token in the header. A LAN attacker who gets the token before the phone submits can upload a document into the queue, where the operator will see it. |
| Path traversal or overwrite through filenames | Stored files get server-generated random names and are created with `O_CREAT | O_EXCL | O_NOFOLLOW` and mode 0600. An accepted upload is hard-linked from the probe file into `data/raw/` (a link never replaces an existing file), with a fresh `O_EXCL` write on filesystems without hard links. Directories are 0700 and `umask` is 077. `Storage.resolve` refuses any path that escapes its base. Uploaded filenames are reduced to a sanitized basename for display only. | `storage.py`, `intake/filenames.py` | None known. |
| Formula injection through an export | Every document-derived string is written as a string cell with `data_type="s"` and `quotePrefix` when it starts with a formula character. Identifiers use the `@` text format. CSV prefixes `=`, `+`, `-`, `@`, tab, and CR with `'`. | `export/xlsx.py`, `export/csv_export.py` | None known. The demo export was checked and contains zero formula cells. |
| A reviewer approves without seeing a problem | Documents with any warning or error are never auto-approved. A legal document type in `VAULTAI_AUTO_APPROVE_TYPES` fails at startup. Approving a document with an error-level issue requires a non-empty note. Approve, reject, and defer are allowed only from `ready` or `needs_review`, reopen only from `approved`, `exported`, or `rejected`, and anything else returns 409. Every status change, edit, export, and deletion is an audit row with actor, timestamp, before, after, and note. Field edits keep the AI value beside the human value. Editing an approved or exported record moves it back to review. | `pipeline/process.py`, `config.py`, `domain.py`, `review.py`, `db.py` | The audit log is a table in the same SQLite file as everything else. The operator can edit it. There is no signing or append-only storage. |
| Status drifts into an illegal state | Transitions are a fixed table checked on every change. Illegal ones return 409. Exports select and mark records in one transaction, so a record reopened mid-export is never marked exported. | `domain.py`, `export/service.py` | None known. |
| Errors leak document text | Every error is `ApiError {code, message}` with a fixed message. Unhandled exceptions become `500 internal` and the traceback goes to the local log only. Preflight rejection messages are fixed sentences. Parser errors are fixed tokens. | `security/http.py`, `intake/preflight.py`, `parsing/isolate.py` | The local server log records document ids and pipeline stages. |

## Network summary

| Listener | Bind | Default | Who can reach it |
| --- | --- | --- | --- |
| Desktop API and UI | `VAULTAI_HOST:VAULTAI_PORT` | `127.0.0.1:8765` | this machine |
| Phone listener | the LAN address in the QR code (`VAULTAI_PHONE_HOST` or auto-detected), `VAULTAI_PHONE_PORT` | `<lan-ip>:8766`, off | devices with a private, loopback, or link-local source address |
| Vite dev server | `127.0.0.1:5173` | development only | this machine. Proxies `/api` with the `Host` header rewritten to `127.0.0.1:8765`. |

Outbound connections the app makes: the model endpoint (when `openai_compat`), and a UDP `connect()` to `10.255.255.255` that selects the outbound interface for the QR code without sending a packet. Nothing else. `VAULTAI_EGRESS_GUARD=true` enforces this in-process.

On the GX10, add a host firewall rule that allows outbound traffic only to the model server's port on localhost, and inbound traffic on 8766 only from your LAN subnet and only while phone capture is in use.

## Storage and permissions

All state is under `VAULTAI_DATA_DIR` (default `data/`, ignored by git). The process sets `umask 077`. Directories are created 0700, files 0600.

| Path | Contents |
| --- | --- |
| `data/vaultai.db` | SQLite. Documents, fields, line items, issues, clauses, obligations, summaries, intake rows, capture sessions, exports, the OCR and model cache, the full-text index, and the audit log. |
| `data/raw/` | original uploads under random names |
| `data/derived/<doc_id>/` | rendered page PNGs |
| `data/captures/<session>/` | scanner pages while a session is open |
| `data/exports/` | XLSX and CSV files |
| `data/tmp/` | parser scratch, removed after each run |

The database is not encrypted. Protect it with full-disk encryption and file permissions.

## Data retention and deletion

Nothing is deleted on a schedule. Documents stay until the operator deletes them.

Deleting one document (`DELETE /api/documents/{id}`, or **Delete** on the document page) removes the raw file, the page images, every derived row through foreign-key cascade, the search entry, and every intake row that named the document or was marked a duplicate of it.

The document's audit rows survive as the record that the deletion happened, with the copied content removed. In the same transaction, `before`, `after`, and `note` are set to NULL on every row carrying that document id, which clears edited field values, reviewer notes, the auto-approval rule text, and export and capture notes. What remains on those rows is the action, the actor, the time, the document id (which no longer resolves to a filename), and the field key of each edit. The new `delete` row's note is only `sha256 <first 12 hex characters>`.

`preflight_reject` rows are different. They record a file that was refused before anything was stored, so there is no document to delete, and their note keeps "filename: reason".

`DELETE /api/data` with body `{"confirm":"DELETE"}`, the **Delete all local data** control in **Settings & Privacy**, or `python -m vaultai delete-data --yes` removes every file under the data directory, deletes the database and its WAL and SHM files, and recreates an empty database with one `delete_all` audit row. The OCR and model cache goes with it. Stop the server before running the CLI form so no worker is writing.

Exports are files the operator downloaded. VaultAI cannot recall them. Treat every export as a copy of approved records that now lives outside the data directory.

The cache table stores model request hashes and raw model replies, and OCR words per page image hash, keyed by content hash. It is a copy of document text. It is deleted only by delete all.

## What the model sees

With `VAULTAI_MODEL_BACKEND=mock`, no model runs and no document text leaves the Python process.

With `openai_compat`, the model server receives visible document lines prefixed with line ids, the field schema, and verified facts. It never receives hidden or injection-flagged lines, filenames, file paths, ids, or the operator's name. With `VAULTAI_MODEL_VISION=true` and no OCR engine, it also receives rendered page images. The model server is a separate program. Its own logging, caching, and network behavior are outside VaultAI's control. Run it on the same machine, bind it to localhost, and check its logging settings.

## Known gaps

Stated plainly so nobody has to discover them:

1. The egress guard is a Python socket patch, not a firewall. It does not cover native code, child processes other than the ones VaultAI starts with a clean environment, or a compromised interpreter.
2. The phone listener is plain HTTP. Token and photos cross the LAN unencrypted. TLS with a self-signed certificate embedded in the QR code is future work.
3. The parser memory cap uses `RLIMIT_AS`, which macOS does not enforce. On macOS, only the CPU limit and the timeout bound a hostile PDF. The cap works on Linux.
4. The parser subprocess is not sandboxed beyond resource limits and a clean environment. It runs as the operator and can read the operator's files.
5. There are no user accounts, no login, and no per-user audit identity. Anyone at the keyboard, or any process running as the operator, is the operator.
6. The audit log can be edited by anyone with write access to `vaultai.db`. It is evidence of what the app did, not tamper-proof evidence.
7. The database and the raw files are not encrypted at rest.
8. Prompt-injection screening is pattern based. It catches the demo set and common phrasings and will miss others. The grounding gate and human review are the second and third lines.
9. Signature detection relies on drawn marks in PDFs. A scanned or photographed signature reports `unknown`, so a missing signature on a scan is not flagged.
10. Python dependencies are pinned by version range and the frontend by a lockfile. Neither is pinned by hash, and there is no software bill of materials.
11. The `openai_compat` adapter was tested against a fake local server, not a real Nemotron deployment. Prompt formatting and `/no_think` handling may need adjustment for a specific server.

## Reporting a problem

This is a hackathon repository. Open an issue in the repository with the file, the request, or the document pattern that caused the problem. Do not attach real business documents to an issue.
