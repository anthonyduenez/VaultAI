# AI tuning: results

This page records what changed in VaultAI's document-understanding pipeline, what the evaluation shows, what it cannot show, and why model weights were not fine-tuned. The numbers before any change are in [AI_TUNING_BASELINE.md](AI_TUNING_BASELINE.md).

## Read this first

**No Nemotron numbers were measured on the development Mac.** All work was done there, and no Nemotron server was reachable from it. The only Nemotron numbers on this page are the GX10 baseline the operator reported, under [Obligation extraction](#obligation-extraction). The improvements below were measured with two stand-ins:

- the **mock** backend (rules only, no model);
- an **oracle** that answers a field only when the expected value is printed on a line VaultAI sent. The oracle measures what VaultAI gives the model (context recall, call count, prompt size) and what VaultAI does with an answer (grounding, confidence, routing). It is a perfect reader, so it says nothing about how often Nemotron reads correctly.

Whether Nemotron's accuracy changed is unknown until the commands under [Reproduce](#reproduce) are run on the GX10. The bundled samples are also small (16 documents, all fictional, one layout family per type), so every percentage below rests on very few cases.

### Update: reasoning output on the GX10

On the GX10, Nemotron 3.5 Lightning served by vLLM answered every VaultAI request with `content: null` and its text in a `reasoning` field, so every reply was rejected as unusable. VaultAI now sends a top-level `"chat_template_kwargs": {"enable_thinking": false}` on every request (`VAULTAI_MODEL_ENABLE_THINKING`, false by default), which makes the model answer in `content`. VaultAI still reads only `content`: reasoning is never taken as an answer, and an empty reply is retried once and then rejected.

This was checked on the development Mac against a fake loopback server that withholds `content` unless thinking is disabled. It was not checked against Nemotron. Before the change, all 33 model calls across the text, stress, and OCR runs were rejected after two attempts. After it, none were. The GX10 evaluations below still need to be run.

## Results

The baseline was taken at commit `f7bd04d`, the final run at the tip of `vaultai-mvp`. Both used the same 16 documents and the same scorer.

### Accuracy

| Run | Required fields | All fields (153) | Missing | Incorrect | Model values kept (all correct) | Statuses as expected | Issue sets as expected |
| --- | --- | --- | --- | --- | --- | --- | --- |
| mock, text | 100% → 100% | 100% → 100% | 0 → 0 | 0 → 0 | 0 → 0 | 16 → 16 | 16 → 16 |
| mock, stress | 40.0% → 40.0% | 77.1% → 77.1% | 35 → 35 | 0 → 0 | 0 → 0 | 5 → 5 | 2 → 2 |
| mock, OCR | 90.9% → 90.9% | 93.5% → 93.5% | 7 → 7 | 3 → 3 | 0 → 0 | 12 → 12 | 11 → 11 |
| oracle, text | 100% → 100% | 100% → 100% | 0 → 0 | 0 → 0 | 0 → 0 | 16 → 16 | 16 → 16 |
| oracle, stress | 100% → 100% | 98.7% → 99.3% | 2 → 1 | 0 → 0 | 33 → 34 | 14 → 14 | 16 → 15 |
| oracle, OCR | 96.4% → 96.4% | 95.4% → 95.4% | 4 → 4 | 3 → 3 | 4 → 3 | 14 → 12 | 13 → 11 |

How to read the table:

1. **The rules did not change.** The mock rows are identical, so the deterministic path is unchanged. The gated default evaluation still passes every gate.
2. **Measurable accuracy gain: one field.** On stress, a receipt's `currency` is now derived after the model supplies its total. It was never revisited before.
3. **The other stress receipt still misses currency.** That is an oracle artifact. The oracle cites the first line holding 61.40, which is `Subtotal 61.40` with no `$`. A model citing `AMOUNT CHARGED $61.40` would get USD.
4. **The changed issue set on stress is deliberate.** On the Acme invoice, the model-filled total ($5,328.00) fails subtotal-plus-tax arithmetic. That is the sample's planted error. Cross-field checking now lowers the value's confidence and adds `low_extraction_confidence` beside `amount_mismatch`. The status stays `needs_review`.
5. **The OCR status changes are deliberate.** On both scanned checks, the payee came from the model on a line tesseract read with 63–82% confidence. A model value's confidence is now multiplied by its line's OCR confidence, as the rules already did for their own values. Both payees land below the 0.75 review threshold, so both checks go to `needs_review` instead of `ready`.
6. **"Model values kept" on OCR fell from 4 to 3 with no lost value.** On the Northwind check, rules and model read the same amount. The rules value now wins the tie instead of being relabeled as the model's.

### Model calls and prompt size

| Run | Calls | Calls per document | By task | Total prompt characters |
| --- | --- | --- | --- | --- |
| oracle, text | 16 → 5 (−69%) | 1.00 → 0.31 | analyze 16 → 5 | 43,488 → 23,630 (−46%) |
| oracle, stress | 30 → 19 (−37%) | 1.88 → 1.19 | analyze 16 → 5, extract 14 → 14 | 76,136 → 72,098 (−5%) |
| oracle, OCR | 20 → 9 (−55%) | 1.25 → 0.56 | analyze 16 → 5, extract 4 → 4 | 55,296 → 37,649 (−32%) |

- Calls fell because financial documents (checks, receipts, invoices, purchase orders, quotes) and forms no longer get a model analysis call. Their summary is a template of verified fields. Legal documents, correspondence, and "other" get one analysis call each. The 3-page MSA fits one call, where an early version of this work had split it into three.
- **Per-call prompts got longer.** Mean extraction prompts went from 2,332 to 3,462 characters (stress) and analysis prompts from 2,718 to 4,726. The reasons:
  - the per-type templates add field definitions;
  - every task carries an explicit rules block;
  - analysis prompts now include the rules' clause flags as facts;
  - analysis runs only on the longest documents.

  On the stress set, the single-page invoices are already shorter than the 2,400-character extraction budget, so they are sent whole either way, and total prompt size barely moved. The extraction budget pays off on long documents, which this sample set barely has.
- **Latency could not be measured.** Processing time without a model is unchanged (about 100 ms per document on text, 430 ms with OCR). Model latency needs the GX10. Calls per document is the best proxy available: each removed call is a full Nemotron request saved. Analysis chunks, when there are several, now run concurrently up to `VAULTAI_MODEL_CONCURRENCY`.

### Mechanical check of the one-command workflow

`./scripts/start_gx10_demo.sh --load-samples` was run unmodified from a clean worktree against a **fake** OpenAI-compatible server on `127.0.0.1:8000`. The fake advertised `nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4` and returned valid, empty answers.

- **What ran:** the launcher passed its checks, built the frontend, started the backend, and processed 13 samples to 1 approved, 4 needs_review, and 8 ready.
- **What the server received:** 5 requests, all `response_format: json_schema`, all naming the exact model.
- **What was recorded:** `/api/system/metrics` reported the 5 calls and their token counts. The backend log contained no document text. The database was `0600`.

This proves the workflow and the request format, not Nemotron's behavior.

## What changed

| Area | Change | Code |
| --- | --- | --- |
| Text preparation | The model's view keeps each line's id and exact text and adds notes around lines: pages, tables with columns, clauses and sections, signature blocks, low OCR confidence. Two-column blocks are read one column at a time under the same ids. Page numbers and repeated headers and footers are dropped. Quarantined lines never appear. | `model/context.py`, `parsing/tables.py` |
| Context selection | Extraction sends the best-scored lines within `VAULTAI_MODEL_EXTRACT_CONTEXT_CHARS` (2,400). Lines score for labels near a missing field, values of the field's type, the document's opening lines, organization names for party fields, and signature lines for signer fields. Analysis sends paragraphs that state duties, dates, amounts, renewal, termination, or signatures. Classification sends the opening of page 1 and the top of later pages, plus the rules' best candidates. | `model/context.py`, `pipeline/process.py` |
| Prompts | One template per family: check, receipt, invoice, purchase order, quote, contract, agreement letter, statement of work, policy, correspondence, general. Each states the document type, the allowed keys, required and optional fields, value types, what each field is and is easily confused with, the null rule, and exact line-id evidence. Legal templates allow facts and issue spotting only, keep the document's wording, and leave risk flags to the rules. | `model/templates.py`, `model/prompts.py` |
| Structured output | A per-task JSON Schema in `response_format` (`json_schema`, strict), dropping to `json_object` only when the server refuses schemas. Non-JSON or off-schema replies get one repair turn (two attempts in all). Unsupported values are never retried. Replies are read with a 1 MB cap. `NaN` and `Infinity` are refused. Token usage is recorded. | `model/output.py`, `model/openai_compat.py` |
| Grounding | A value needs a line the model was shown and a quote that matches that line exactly apart from spacing and Unicode form (case kept). Text is stored as printed, not as the model spelled it. Amounts, dates (1900–2100), and currencies are re-parsed from the quote. Two different answers for one field discard both. Obligations keep the document's wording unless the model's description is faithful to the line. Claims must cite lines, state only printed amounts and dates, and give no advice. | `model/grounding.py` |
| Confidence | The model's own number can lower a score but never raise it. See [Confidence policy](#confidence-policy). | `model/confidence.py` |
| Routing | Rules run first. The model is asked only for required fields the rules missed or read below 0.75, never for signature fields (a text-only model cannot see a signature), and for analysis only on documents that need reading. Any accepted model analysis makes the document model-assisted, so it can never be auto-approved. Currency is derived again after the model fills an amount. | `pipeline/process.py` |
| Caching | Model output is cached only when it validates. The key covers the document hash, the exact prompt (so page content), the prompt and template version, the model id, and a configuration fingerprint. The cache is a table in the owner-only database. | `pipeline/cache.py` |
| Metrics | Per-document stage timings, model calls by task, attempts, retries, rejections, cache hits, prompt characters, tokens, and prompt versions go to `doc_metrics` (no document text). Totals appear in `GET /api/system/metrics` and on the dashboard. | `pipeline/process.py`, `metrics.py` |
| Evaluation | `scripts/eval_samples.py --backend mock\|oracle\|openai_compat --variant text\|ocr\|stress --json FILE`. | `scripts/eval_samples.py`, `devtools/evaluation.py` |
| Feedback | `python -m vaultai export-feedback` writes reviewed corrections as JSONL. | `feedback.py` |

## Confidence policy

A model value that survives grounding is scored by `model/confidence.py`:

| Signal | Effect |
| --- | --- |
| Starting point for an exact, typed, grounded quote | 0.80 |
| One of the field's labels on the evidence line or the line above | +0.05 |
| The field has known labels and none is near | −0.05 |
| Quote longer than 160 characters | −0.05 |
| Page was OCR'd | × the line's OCR confidence |
| Rules read the same value | +0.05 |
| Rules read a different value | −0.25 |
| Model reports less than 0.5 | −0.10 |
| Ceiling | 0.85 |
| Below 0.40 | discarded as weak evidence (`ungrounded_value`) |

After model values are merged with the rules, a model value is lowered to 0.50 when:

- the subtotal, discount, tax, and total do not add up;
- the total is below the subtotal;
- a date pair is out of order;
- an identifier repeats another field or has no digits;
- a routing number fails its checksum;
- a check's written and numeric amounts disagree.

A required field below 0.75 raises `low_extraction_confidence` and sends the document to `needs_review`. Whatever the score, a document the model touched is `ready` or `needs_review`, never auto-approved.

## Diagnosing rejections

Every rejection is recorded as a stable code with a count, per document in `doc_metrics` and in total in `GET /api/system/metrics` and the evaluation JSON. The rejected reply, the prompt, values, and quoted lines are never stored or logged. Codes live in `backend/vaultai/model/diagnostics.py`.

| Code | Counted in | Meaning | Usual fix |
| --- | --- | --- | --- |
| `empty_content` | `model_failure_reasons`, `model_output_rejections_by_reason` | `message.content` was null or blank. Reasoning fields are never read. | Check that `chat_template_kwargs.enable_thinking` is false and `VAULTAI_MODEL_MAX_TOKENS` is large enough |
| `invalid_json` | same | The content was not a JSON object | Structured output support on the server |
| `schema_invalid` | same | A JSON object that is not the requested structure, or a type outside the allowed list | Prompt or schema |
| `response_too_large` | same | The reply exceeded 1 MB and was cut off. It is not retried. | Server limits |
| `invalid_citation` | `grounding_rejections_by_reason` | Line id missing, malformed, not shown to the model, or not in the document; for an obligation, also an `end_line_id` that is not one or two lines after `line_id` on the same page, or a span that crosses a quarantined line | Prompt: cite shown ids only |
| `unsupported_by_document` | same | The quote is not on the cited line, or the value does not read as its type inside the quote | Prompt: copy exactly; possibly tuning |
| `conflicting_evidence` | same | Two different answers for one field | Prompt or context |
| `weak_evidence` | same | Calibrated confidence below 0.40, usually poor OCR | OCR |
| `obligation_not_grounded` | same | An obligation's quote is not copied exactly from its cited line or lines | Prompt: copy exactly, use `end_line_id` for a wrapped sentence |
| `deadline_not_grounded` | same | An obligation's `deadline_text` is not in its sentence, or its `due_date` is not printed in the deadline text (or the quote), often a date computed from "within N days" | Prompt: relative deadlines go in `deadline_text` only |
| `party_not_grounded` | same | The sentence names a different party before its binding verb than the one the model gave | Prompt: party as printed |
| `not_an_obligation` | same | The quoted sentence has no binding language for its kind: a permission ("may"), a description, or background | Prompt: duties only |
| `unknown_field` | same | A key that was not requested | Schema enforcement on the server |
| `other` | same | A discarded value without a recognized code | Investigate |

`model_failure_reasons` counts every refused attempt, including ones the repair turn recovered. `model_output_rejections_by_reason` counts only replies that stayed unusable. Its total equals `model_outputs_rejected`.

## Obligation extraction

### GX10 baseline

The operator reported these numbers from the GX10 run on 2026-10-11, at commit `e564e2b` with Nemotron 3.5 Lightning. They were not reproduced on the development Mac.

| Variant | Required fields | All fields | Grounded-value rate | `obligation_not_grounded` | `deadline_not_grounded` |
| --- | --- | --- | --- | --- | --- |
| text | 100% | 100% | 45% | 11 | not reported |
| stress | 98.18% | 99.35% | 84.09% | 3 | 4 |
| OCR | 94.55% | 94.77% | 50% | 10 | not reported |

Field extraction holds up. The low grounded-value rates come from obligations, which only the analysis task produces. On the text variant, no field needs the model, so every rejected value there is an obligation or a deadline.

### Why prompt, context, and grounding come before tuning

Before this change, a correct obligation could be refused for reasons that have nothing to do with what the model knows:

1. **Wrapped sentences.** A contract duty usually wraps onto the next line, and the old schema allowed a quote from one line only. A complete, exact quote of the sentence was refused as `obligation_not_grounded`.
2. **Relative deadlines.** "Within thirty (30) days of receipt" had nowhere to go but `due_date`, so a model that filled it in was refused as `deadline_not_grounded`.
3. **Typography.** A curly apostrophe in the document against a straight one in the quote failed the exact match.
4. **No duty definition.** The old prompt asked for "obligations and deadlines" in one line inside a summary task. It did not separate duties from permissions, descriptions, or background.

Each of these is a contract between VaultAI and the model, not a gap in the model's knowledge, so each is fixed here first. Fine-tuning stays off the table until the GX10 numbers after this change show what is left.

### What changed

- **A dedicated obligation task.** The analysis prompt is now an obligation task with a short summary on the side. It says what counts as binding language and what does not (permissions, descriptions, background, definitions, recommendations, summaries). It asks for one entry per duty and lists the document's parties and defined terms, such as `Client = Harborview Logistics LLC`, taken from the preamble and the extracted fields.
- **Explicit citations.** The prompt has rules for citing: the line where the quote starts; ids shown only; never a page, a layout note, or a line that only mentions the topic; and leaving out anything that cannot be quoted. `end_line_id` lets a quote continue onto one or two following lines.
- **Deadlines.**
  - `deadline_text` holds the deadline words as printed, such as "within thirty (30) days of receipt". It is appended to the stored description.
  - `due_date` must be printed inside the deadline text, or inside the quote when there is no deadline text. VaultAI never derives a calendar date from a relative deadline.
- **Validation.** VaultAI refuses an obligation when:
  - the citation or span is not valid;
  - the quote is not exact (curly quotes and dashes match their plain forms; nothing else is relaxed);
  - the sentence holding the quote has no binding language for its kind (`not_an_obligation`);
  - the sentence's subject names a different party (`party_not_grounded`);
  - the deadline text or due date is not printed (`deadline_not_grounded`).

  A party the document never mentions is dropped rather than kept. A description that adds a word, amount, or date not in the sentence is replaced by the quote. Repeated duties are kept once.
- **Context.** The analysis context was already the paragraphs with duties, dates, or amounts, plus clause headings, signature blocks, and table notes. Only the parties line was added. Prompts grew by about 1,700 characters per analysis call.

The binding-language check is a necessary condition, not a classifier. It refuses a "may" sentence or a plain description, but it cannot tell whether a "shall" sentence was read correctly. That part is still the reviewer's.

### Local evidence

The oracle proposes every expected obligation whose date is printed, quoting the whole sentence that holds the date, without using VaultAI's duty check to choose it.

| Variant | Expected dated obligations | Proposed | Rejected | Kept as new | Duplicates of rule obligations |
| --- | --- | --- | --- | --- | --- |
| text | 9 | 8 | 0 | 3 | 5 |
| stress | 9 | 8 | 0 | 3 | 5 |
| OCR | 9 | 8 | 0 | 3 | 5 |

- **The validator refuses no correct obligation.** That includes the letter's three-line deliverable sentence.
- **The ninth is not quotable.** The MSA's non-renewal notice date (2026-11-01) is computed from "sixty (60) days before the end", so no exact quote exists.
- **The adversarial cases are unit tests.** Wrong parties, permissions, computed dates, invented ids, and malformed objects are covered in `backend/tests/core/test_obligations.py`.
- **Nothing else moved.** Field accuracy, statuses, issue sets, and model calls on the mock and oracle runs are unchanged.

### What to look for on the GX10

Rerun `./scripts/run_nemotron_evals.sh`, then compare `grounding_rejections_by_reason`, `model_obligations_accepted`, `model_obligations_correct`, and the grounded-value rate with the baseline above.

- `obligation_not_grounded` and `deadline_not_grounded` should fall if the causes above were the main ones.
- `not_an_obligation` and `party_not_grounded` are new. A count there is an unsupported obligation refused, not a regression.

Because those new refusals also count against the grounded-value rate, the rate alone is not the measure. The aim is more correct obligations accepted and none unsupported.

Until those numbers exist, this change is not an improvement in Nemotron's scores.

## Remaining failure modes

- **Signatures on scans and photos.** Signature status is `unknown` and signer names are missed on OCR'd pages, because there are no vector marks to detect. That is 3 incorrect and 4 missing fields on the OCR variant with any backend. A text-only model cannot fix it, so VaultAI never asks one. A local vision model or a mark detector on page images could.
- **Hidden text in images.** White-on-white text does not survive rasterization, so a scanned copy of the QuickShip invoice raises `prompt_injection` but not `hidden_text`. Nothing is lost, because the text cannot be read either.
- **Small OCR text.** Tesseract reads the 7-point "PAY TO THE ORDER OF" line on the sample checks with 63–82% confidence. Rules then miss the payee and a model value is downgraded to review. The fix is OCR-side, for example rendering at a higher resolution for small text.
- **Multi-line values.** A value must be quoted from one line, so parties or addresses split across lines cannot be extracted by the model. They stay missing rather than being stitched together.
- **Unknown model behavior.** Nemotron's real hit rate, quote fidelity, and reaction to the repair turn are unmeasured. The adapter was exercised against scripted and fake servers only.
- **Tiny evaluation set.** 16 documents from one generator. The stress synonyms are a fixed list of 33 label renamings across 14 documents. Treat percentages as smoke tests, not estimates.

## Human feedback loop

`python -m vaultai export-feedback` writes one JSON line per corrected field to `data/exports/` (owner-only, exclusive create) and records an audit row.

- **Ground truth only.** Documents count only if a person approved them. Rejected, unreviewed, policy-approved, and injection- or hidden-text-flagged documents are skipped. By default, only fields a reviewer edited are written. `--include-confirmed` adds model values a reviewer approved without editing, labeled `confirmed`.
- **Fields.** Each record carries:
  - the document hash, document type, and field;
  - the original value, its origin and confidence, and the corrected value;
  - the evidence line (page, id, box, text; `--no-evidence-text` omits the text);
  - the review decision and approval time;
  - the model id, prompt versions, and configuration;
  - OCR engine, text method, page and line confidence;
  - schema version and export time.
- **Not included.** No reviewer name and no raw document. `--include-document-text` writes a second file with each exported document's visible lines, never quarantined ones.
- **Leakage controls.** Each record's `split` (train 80%, validation 10%, test 10%) is a hash of its template group: document type plus normalized counterparty. Every document from one vendor or layout lands in the same split, near-duplicates stay together, and a split never mixes lines from one document. Identical corrections within a group are written once.
- **Nothing trains automatically.** The export is a file for a person to inspect and decide on.

Keep the bundled samples, the stress variants, and any feedback from documents that share their templates out of tuning data. They are the evaluation set.

## Fine-tuning decision

**Weight fine-tuning is not justified now.** The reasons:

1. **The GX10 baseline measures VaultAI's obligation contract as much as the model.** Field extraction is at 94.5–100% of required fields. The low grounded-value rates come from obligation and deadline citations ([Obligation extraction](#obligation-extraction)). Wrapped sentences, relative deadlines, and typography were format limits on VaultAI's side and are fixed first. Fine-tuning needs evidence that a well-prompted Nemotron still fails on the right context. That evidence does not exist yet.
2. **The failures that were measured are not model failures.**
   - Signatures on scans are a vision and OCR problem.
   - Small text is an OCR problem.
   - Multi-line values are a quoting-contract limit.
   - The currency gap was a pipeline bug, now fixed.
3. **The oracle shows the context is sufficient.** It recovered 100% of required fields on the stress set from the lines VaultAI now sends. If Nemotron falls short there, prompt or format compliance is the first suspect, not knowledge.
4. **There is no training data.** There are no reviewed corrections yet, and the samples must stay held out.

Revisit after the GX10 runs. Consider a LoRA adapter only if all of these hold:

- On a held-out set of at least a few hundred reviewed fields, the base model's required-field accuracy on the stress and real-document variants stays clearly below the oracle's, after prompt iteration.
- The misses are systematic: the same field confused the same way, in the right context.
- Grounding rejections (wrong line ids or paraphrased quotes) are a large share of misses.

If those hold:

- Train on the GX10 or another approved local machine only, with NeMo or PEFT LoRA or QLoRA against the base checkpoint, which stays unchanged.
- Use only reviewed corrections from `export-feedback` (split by template group) plus synthetic or redacted documents. Never upload a document.
- Serve the adapter as an optional vLLM LoRA module selected by model name. Removing it from `.env` restores the base model.
- Compare base against adapter with `scripts/eval_samples.py --backend openai_compat` and the feedback test split on:
  - required-field accuracy;
  - grounded-value rate;
  - hallucination rate (accepted values that are wrong);
  - rejected outputs;
  - latency and GPU memory;
  - review-status distribution.
- Keep the demo independent of training.

## Security tests added

| Test file | Coverage |
| --- | --- |
| `backend/tests/core/test_model_security.py` | <ul><li>Injection and hidden text absent from classification, extraction, and analysis prompts.</li><li>Model output cannot approve, reject, delete, or write files (5 hostile replies).</li><li>Model analysis cannot enable auto-approval.</li><li>Model modules cannot import storage, review, database, or subprocess code.</li><li>Public endpoints refused (6 forms) at startup and before each request.</li><li>Proxy variables ignored, proven against a real local server with a dead proxy configured.</li><li>No cloud fallback and no public AI hostname in the code.</li><li>No document or reply text in logs across a schema refusal, retries, and an outage.</li><li>Owner-only database, cache, and metrics.</li><li>Oversized replies routed to review.</li><li>Demo profile pinned to local Nemotron with phone pairing off.</li></ul> |
| `backend/tests/core/test_model_output.py` | <ul><li>Per-task JSON Schema.</li><li>Exactly one repair attempt, never for evidence problems.</li><li>Schema refusal drops to `json_object` once; other HTTP errors are outages.</li><li>1 MB reply cap; token usage summed.</li><li>A timeout routes to review.</li><li>Nine malformed replies never crash the worker.</li><li>Metrics recorded without content.</li></ul> |
| `backend/tests/core/test_grounding.py` | <ul><li>Exact quotes; values stored as printed; amounts, dates, and currencies re-parsed.</li><li>Unshown lines refused; conflicting answers discarded.</li><li>Confidence signals and cross-field downgrades.</li><li>Faithful obligations; advice and unsupported claims dropped.</li><li>Zero calls for clean financial documents.</li><li>Signature fields never asked of the model.</li><li>Cache keys.</li></ul> |
| `backend/tests/core/test_context.py` | <ul><li>Column reading order with stable ids.</li><li>Layout notes outside line text.</li><li>Furniture removal.</li><li>Quarantine.</li><li>Budgeted selection.</li><li>Forged notes and tags neutralized.</li></ul> |
| `backend/tests/core/test_feedback.py` | <ul><li>Only human-approved corrections exported.</li><li>Opt-in confirmations and document text.</li><li>Injection-flagged documents skipped.</li><li>Template-group splits.</li><li>CLI and audit.</li><li>Owner-only output with no reviewer name.</li></ul> |

## Reproduce

On any machine, with the mock and oracle backends:

```sh
backend/.venv/bin/python scripts/eval_samples.py                                   # gated rules check
backend/.venv/bin/python scripts/eval_samples.py --backend oracle --variant stress --json oracle-stress.json
backend/.venv/bin/python scripts/eval_samples.py --backend oracle --variant ocr    --json oracle-ocr.json
```

On the GX10, with vLLM serving the model from `.env`:

```sh
backend/.venv/bin/python scripts/eval_samples.py --backend openai_compat --variant text   --json nemotron-text.json
backend/.venv/bin/python scripts/eval_samples.py --backend openai_compat --variant stress --json nemotron-stress.json
backend/.venv/bin/python scripts/eval_samples.py --backend openai_compat --variant ocr    --json nemotron-ocr.json
```

Run them at the tip of the branch. A run at an earlier commit, such as `f7bd04d`, is not a usable Nemotron baseline on a server that returns reasoning by default: those commits send no `chat_template_kwargs`, so every reply comes back with empty `content` and is rejected. Compare the GX10 numbers with the mock and oracle baselines above instead. `model_values_accepted` minus `model_values_correct` is the number of accepted but wrong model values.
