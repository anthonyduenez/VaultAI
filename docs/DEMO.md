# VaultAI demo script

Fourteen steps, about ten minutes, using only the bundled fictional samples. Each step names the page, the clicks, and what you should see. The flow was run end to end in a browser on the development Mac with the mock model backend and the egress guard on. Timings and OCR results will differ on a GX10.

## Before you start

1. Build the frontend and start the backend with phone capture on. Phone capture needs the phone and the Mac (or GX10) on the same LAN.

   ```sh
   cd frontend && npm run build && cd ..
   VAULTAI_PHONE_ENABLED=true backend/.venv/bin/python -m vaultai
   ```

   Or put `VAULTAI_PHONE_ENABLED=true` in `.env` and run `cd backend && .venv/bin/python -m vaultai`.

2. Open http://127.0.0.1:8765.
3. If the database already has documents from an earlier run, open **Settings & Privacy**, type `DELETE`, and click **Delete all local data** so the demo starts clean.

## The fourteen steps

### 1. Show that nothing leaves the machine

Open **Settings & Privacy**. The **Local-only guarantees** card lists document storage, OCR, the model, network egress, phone capture, and exports, each with where it runs. The subtitle reads "All processing on this device". The **Configuration** card echoes the `.env` values, including `VAULTAI_MODEL_BACKEND` and `VAULTAI_EGRESS_GUARD=true`.

Optional, in a terminal: `lsof -p <backend pid> -i` shows the listeners on 8765 and 8766 and no outbound connections.

### 2. Import the demo samples

Open **Import & Scan**, choose the **Demo samples** tab, and click **Import sample folder**. Thirteen files from `samples/demo/` appear in the queue, each with its preflight result. Say that the samples went through the same byte-level checks as any upload.

### 3. Process them

Click the **Process** button above the queue. The queue polls while documents move through "Reading pages", "Classifying", "Extracting fields", "Analyzing clauses", and "Validating". With the mock backend all thirteen finish in a few seconds. The end state on the Mac was 1 approved, 4 needs review, 8 ready.

### 4. The dashboard

Open **Dashboard**. Point at **Upcoming deadlines** (overdue and the next 30 days), **Contract renewals**, **Financial totals** (approved amounts separate from pending), **Documents by type**, and **Performance**, which shows average latency per page measured on this device.

### 5. A receipt approved by policy

Open **Documents** and click `receipt_cornerstone_office_mart.pdf`. Its status is `approved` with approval kind policy. On the **History** tab the `auto_approve` row states the rule that applied: receipt type, no warnings or errors, no injection, every required field at confidence 0.90 or higher. On the **Line items** tab, three items add to the subtotal and the tax matches.

### 6. An invoice whose total is wrong

Open `invoice_acme_INV-2026-0142.pdf`. Status `needs_review`. The **Issues** tab shows `amount_mismatch`: the printed total is 5,328.00 but subtotal 4,850.00 plus tax 388.00 is 5,238.00. Click **Show in document** on the issue to highlight the total on the page image.

To correct it, open the **Fields** tab, click the edit icon beside **Total**, enter `5238.00`, and click **Save**. The row now shows the human value with the AI value kept beneath it, and the issue clears because validation re-runs on every edit. Click **Approve**. The History tab records the edit with before and after values and the approval with your reviewer name.

### 7. A document that tries to instruct the AI

Open `invoice_quickship_QS-55021.pdf`. It looks like a normal invoice. The **Issues** tab shows `hidden_text` (white-on-white instructions a reader cannot see) and `prompt_injection` (a visible gray line that says to ignore previous instructions and approve). Click each issue to see where the text is on the page. Both lines were quarantined and never sent to a model, and the document is forced to review. Click **Reject**, or leave it for later.

### 8. A contract with clauses and a deadline

Open `contract_msa_brightpath.pdf`, a three-page MSA. The **Summary & clauses** tab lists each clause with its original wording and a plain reason for every flag: auto-renewal, unlimited liability, one-way indemnity, non-solicitation, one-sided termination for convenience, and undefined standards such as "commercially reasonable efforts". The **Issues** tab adds `missing_signature` because the client block is blank, plus the legal review notice. The **Obligations** tab lists the non-renewal notice due 2026-11-01, the term expiration on 2026-12-31, the monthly fee, the breach notice window, the insurance and SOC 2 requirements, and the signature that is still needed.

### 9. The Action Center

Open **Action Center**. Groups are ordered by urgency: renewals, payments due, injection, amount mismatches, unsigned, risky clauses, obligations, duplicates, missing info, processing failures, then needs review, ready, and recently approved. The Brightpath notice deadline appears under renewals with its due date. The QuickShip invoice appears under injection. The Acme invoice has left the amount mismatch category because it was corrected and approved. Click any item to jump to its document.

### 10. Review the rest in batch

Open **Review queue**. Documents that need a decision are first and deferred ones last. Open `invoice_pinecrest_PFS-10388.pdf`, confirm the totals reconcile, and click **Approve**. Then use the **Similar documents** selector to group by type and counterparty, select the rest with **Select all in this group**, and click **Approve selected**. Each result reports ok or error per document.

### 11. Scan two pages with the mock scanner

Open **Import & Scan**, choose the **Scanner** tab. With no SANE device the mock scanner "VaultAI demo scanner (mock)" is listed. Click **Scan page** twice. Two pages from `samples/scanner/` appear with a slight skew and speckle. Rotate or crop one to show the editor. Click **Combine 2 pages into PDF & import**, then process the queue. Open the document. The provenance panel under the header shows the OCR engine (tesseract 5.5.3 on the Mac). The two-page Tidewater invoice may land in review with an amount mismatch because dense OCR'd tables can miss rows. That is the honest outcome, and the reviewer fixes it the same way as in step 6.

### 12. Capture a page from a phone

Choose the **Phone** tab and click **Start pairing**. A QR code, the URL, and a 6-digit code appear. Scan the QR code with the phone. The phone page shows the same code. Confirm they match. Photograph a page sideways, tap rotate on the phone, and tap **Send pages to VaultAI**. The desktop shows the capture as completed and the Import queue gains a document with source phone. On the Mac the rotated photo was OCR'd at confidence 0.95.

Reload the phone page. It answers 404 because the session was single use.

### 13. Export the approved records

Open **Export**. Leave the format as XLSX, keep **Include source evidence sheet** checked, and read the preview. It lists only approved records and warns how many matching records still need review and are left out. Click **Create export**, then **Download export**. Open the workbook. `Approved Records` labels each row Human or Auto-approval policy. `Source Evidence` keeps the AI value beside the human edit on the Acme total. `Export Info` carries the notice that VaultAI is not legal or financial advice. There are no formula cells. Back in the app, the exported documents now show status `exported` and **Export history** lists the file with its SHA-256.

### 14. Delete everything

Open a document and click **Delete**, then **Delete permanently**. The file, pages, records, and intake rows are gone. The **Audit log** on **Settings & Privacy** shows the delete row with only a SHA-256 prefix, and the document's earlier rows keep their action and time but no longer show the edited values or notes. Then type `DELETE` in the danger zone and click **Delete all local data**. The data directory is wiped and the only audit row left is `delete_all`.

## If something goes wrong

- If the phone cannot open the link, check that the phone is on the same LAN and that `VAULTAI_PHONE_HOST` is an IP the phone can reach. **Settings & Privacy** must show `VAULTAI_PHONE_ENABLED` as `true`.
- If the scanner tab says no scanner found, `VAULTAI_SCANNER_BACKEND` is `none`, or `sane` was chosen without `scanimage` installed. `auto` always finds the mock scanner.
- If OCR shows as not installed, install tesseract and restart. Without OCR, scanned and photographed pages produce no text and land in review as unreadable.
- If every sample is marked duplicate, the samples are already in the database. Delete all local data first.
