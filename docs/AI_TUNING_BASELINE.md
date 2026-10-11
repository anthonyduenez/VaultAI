# AI tuning baseline

Recorded on 2026-10-10 at commit `00d3564` (branch `vaultai-mvp`), before any change to the document-understanding pipeline. The final numbers after the tuning work are in [AI_TUNING.md](AI_TUNING.md).

## What could and could not be measured

The baseline was taken on the development Mac (Apple silicon, macOS, tesseract 5.5.3), not on the GX10. **No Nemotron server was reachable from this machine.** `curl http://127.0.0.1:8000/v1/models` was refused, so the live `openai_compat` path with `nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4` was not run. This document contains no Nemotron accuracy or latency numbers, and none were estimated.

What was measured instead:

| Backend | What it is | What its numbers mean |
| --- | --- | --- |
| `mock` | deterministic rules only, no language model | what VaultAI reads without any model |
| `oracle` | a perfect reader that answers a field only when the expected value is printed on a line it was shown (`vaultai/devtools/evaluation.py`) | an upper bound on what any model could extract from the context VaultAI sends. It also counts model calls and prompt size exactly as a real model would see them. It is not a language model. |

Each backend ran on three variants of the 16 bundled documents (13 in `samples/demo`, 3 in `samples/scanner`):

| Variant | Input |
| --- | --- |
| `text` | the committed PDFs with their text layer |
| `ocr` | every page rasterized to a 200 dpi grayscale TIFF, so tesseract reads it |
| `stress` | the committed PDFs with printed labels renamed to synonyms the rules do not know (for example `Invoice Number:` to `Our Reference:`, `Total Due:` to `Balance Payable:`). Values and layout are unchanged. 15 of 16 documents are changed. The list is `STRESS_LABELS` in `vaultai/devtools/evaluation.py`. |

The stress set is a held-out check of the model path. Its synonyms must not be added to the rules' aliases.

## Test suites

| Suite | Command | Result |
| --- | --- | --- |
| Backend | `cd backend && .venv/bin/python -m pytest -q` | 214 passed |
| Frontend | `cd frontend && npm test` | 46 passed in 12 files |
| Frontend build | `cd frontend && npm run build` | built, no type errors |
| Sample gates | `backend/.venv/bin/python scripts/eval_samples.py` | every gate passed: 16/16 types, 153/153 fields, 16/16 statuses, 16/16 issue sets, 8/8 line-item tables, 21/21 obligations, 5/5 adversarial files |

## Metrics

Commands: `backend/.venv/bin/python scripts/eval_samples.py --backend <mock|oracle> --variant <text|ocr|stress> --json <file>`.

| Metric | mock text | mock stress | mock ocr | oracle text | oracle stress | oracle ocr |
| --- | --- | --- | --- | --- | --- | --- |
| Document-type accuracy | 16/16 | 16/16 | 16/16 | 16/16 | 16/16 | 16/16 |
| Required-field accuracy | 100% | 40.0% | 90.9% | 100% | 100% | 96.4% |
| All-field accuracy (153 fields) | 100% | 77.1% | 93.5% | 100% | 98.7% | 95.4% |
| Missing fields | 0 | 35 | 7 | 0 | 2 | 4 |
| Incorrect fields | 0 | 0 | 3 | 0 | 0 | 3 |
| Model values accepted | 0 | 0 | 0 | 0 | 33 | 4 |
| Grounded-value rate | n/a | n/a | n/a | n/a | 100% | 100% |
| Rejected model outputs | 0 | 0 | 0 | 0 | 0 | 0 |
| Model calls | 16 | 16 | 16 | 16 | 30 | 20 |
| Model calls per document | 1.00 | 1.00 | 1.00 | 1.00 | 1.88 | 1.25 |
| Calls by task | 16 analyze | 16 analyze | 16 analyze | 16 analyze | 16 analyze, 14 extract | 16 analyze, 4 extract |
| Mean prompt, analyze (chars) | 2,718 | 2,678 | 2,710 | 2,718 | 2,718 | 2,710 |
| Mean prompt, extract (chars) | n/a | n/a | n/a | n/a | 2,332 | 2,984 |
| Largest prompt (chars) | 6,491 | 6,487 | 6,441 | 6,491 | 6,489 | 6,441 |
| Mean processing time per document | 99 ms | 100 ms | 429 ms | 101 ms | 102 ms | 432 ms |
| OCR failures (failed documents, pages with no text) | 0, 0 | 0, 0 | 0, 0 | 0, 0 | 0, 0 | 0, 0 |
| Status distribution | 2 approved, 10 ready, 4 needs_review | 1 ready, 15 needs_review | 10 ready, 6 needs_review | 2 approved, 10 ready, 4 needs_review | 12 ready, 4 needs_review | 12 ready, 4 needs_review |

With the mock backend, the "model calls" are calls to the deterministic template summarizer. With `openai_compat`, each one would be a request to Nemotron.

Processing times exclude any model latency. The mock and oracle answer in microseconds.

## What the baseline shows

1. **The rules are saturated on the committed samples.** They were written against these documents, so the text variant scores 100% with no model. These samples cannot show a model improvement. The stress and OCR variants exist for that reason.
2. **Every document costs at least one full-document model call.** The pipeline asks the model to summarize and list obligations for every document, including clean receipts and invoices whose summary only restates verified facts. With Nemotron, that is 16 requests for the 16 samples before any extraction.
3. **Extraction context recall is already high, but the context is the whole document.** On the stress set, the oracle recovered every required field (33 values, 100% grounded) from the lines VaultAI sent. The context budget is about 26,000 characters, so every single-page document is sent whole. An extraction call averages 2,332 characters to recover two or three fields.
4. **One deterministic gap appears once the model fills a field.** On the stress receipts, the oracle supplied the total (printed `$207.75`), but the `currency` field stayed empty. The rules derive currency before the model runs and never revisit it.
5. **OCR losses are layout and signature losses, not model losses.**
   - On scans, signature detection reports `unknown`. That accounts for 3 incorrect and 4 missing fields with any backend. This is the documented scan limitation.
   - Rules-only OCR also misses both check payees and the letter's sender. The oracle recovers those from the context, so a model can help there.
   - White-on-white hidden text does not exist in a rasterized image, so `hidden_text` is not raised on the OCR'd QuickShip invoice. The visible injection line is still caught.
6. **No safety regressions were possible to observe.** Neither backend produces malformed or ungrounded output, so rejection paths are exercised only by the unit tests. The Phase 4 tests add malformed, oversized, and timed-out replies.

## Reproducing on the GX10

On the GX10, with vLLM serving the model and `.env` set as in `.env.example`:

```sh
backend/.venv/bin/python scripts/eval_samples.py --backend openai_compat --variant text   --json nemotron-text.json
backend/.venv/bin/python scripts/eval_samples.py --backend openai_compat --variant stress --json nemotron-stress.json
backend/.venv/bin/python scripts/eval_samples.py --backend openai_compat --variant ocr    --json nemotron-ocr.json
```

The `openai_compat` backend reads `.env`, refuses a non-local endpoint, installs the egress guard before the first request, and uses a temporary data directory. To get the true Nemotron baseline, run the same three commands at the commit that added this file. It adds only the evaluation harness, so its pipeline is the baseline pipeline.
