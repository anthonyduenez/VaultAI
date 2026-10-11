#!/usr/bin/env python3
"""Run every sample through ingest + processing (temp data dir) and score it against samples/expected.json.

Usage: backend/.venv/bin/python scripts/eval_samples.py [--verbose] [--backend mock|oracle|openai_compat]
                                                        [--variant text|ocr|stress] [--json PATH]

  --backend mock           deterministic rules only (default); the gates below apply to this run
  --backend oracle         a perfect reader limited to the lines each prompt shows; measures context
                           recall and model-call counts, not a language model
  --backend openai_compat  the local model configured in .env (for example Nemotron on the GX10); the
                           endpoint must be local and the egress guard is installed first
  --variant text           the sample PDFs as committed (default)
  --variant ocr            every page rasterized, so tesseract reads it
  --variant stress         field labels renamed so the rules miss required fields (held-out model check)

Diagnostics in the output are counts by stable code, never text: model_failure_reasons (every refused attempt:
empty_content, invalid_json, schema_invalid, response_too_large), model_output_rejections_by_reason (replies still
unusable after the repair attempt), and grounding_rejections_by_reason (well-formed answers the evidence check
discarded: invalid_citation, unsupported_by_document, conflicting_evidence, weak_evidence, obligation_not_grounded,
deadline_not_grounded, party_not_grounded, not_an_obligation, unknown_field, other). model_obligations_correct counts
model obligations whose kind, due date, and party match an expected obligation.

Exit status is non-zero when a gate fails. Gates apply only to the default run (mock backend, text variant):
they are the bar the rules must clear on the demo set and are not tuned to the current score. Other runs
report metrics and expected statuses but do not gate, because a missing field correctly changes the status.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from collections import Counter
from dataclasses import replace
from decimal import Decimal, InvalidOperation
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))

from vaultai.app import build_context  # noqa: E402
from vaultai.config import REPO_ROOT, load_settings  # noqa: E402
from vaultai.devtools.evaluation import STRESS_LABELS, OracleModel, Recorder, rasterize_pdf, relabel_pdf  # noqa: E402
from vaultai.domain import ModelKind, Severity, Source, ValueType  # noqa: E402
from vaultai.intake.ingest import ingest_bytes  # noqa: E402
from vaultai.model import diagnostics  # noqa: E402
from vaultai.model.factory import build_model  # noqa: E402
from vaultai.model.mock import MockModel  # noqa: E402
from vaultai.pipeline.process import process_document, request_processing  # noqa: E402
from vaultai.schemas import SCHEMAS  # noqa: E402

SAMPLES = REPO_ROOT / "samples"
GATES = {
    "doc_type": 1.0,
    "status": 1.0,
    "issues": 1.0,
    "line_items": 1.0,
    "preflight": 1.0,
    "fields": 0.95,
    "obligations": 0.9,
}
MODEL_ISSUES = ("ungrounded_value", "model_output_rejected", "model_unavailable")


def _same(value_type: ValueType, got: str | None, want: str) -> bool:
    if got is None:
        return False
    if value_type is ValueType.MONEY:
        try:
            return Decimal(got) == Decimal(want)
        except InvalidOperation:
            return False
    return " ".join(got.split()).casefold() == " ".join(want.split()).casefold()


def _dec(v: str | None) -> Decimal | None:
    return Decimal(v) if v not in (None, "") else None


def _input(rel: str, variant: str) -> tuple[str, bytes]:
    data = (SAMPLES / rel).read_bytes()
    name = Path(rel).name
    if variant == "ocr":
        return name.removesuffix(".pdf") + ".tiff", rasterize_pdf(data)
    if variant == "stress" and rel in STRESS_LABELS:
        return name, relabel_pdf(data, STRESS_LABELS[rel])
    return name, data


def _model(backend: str, settings, expected):
    if backend == "oracle":
        return OracleModel(expected, settings.date_order)
    if backend == "openai_compat":
        return build_model(settings)
    return MockModel()


def _settings(backend: str, data_dir: Path):
    if backend == "openai_compat":
        from vaultai.security.egress import install_egress_guard

        settings = replace(load_settings(), data_dir=data_dir)
        if settings.model_backend is not ModelKind.OPENAI_COMPAT:
            sys.exit("--backend openai_compat needs VAULTAI_MODEL_BACKEND=openai_compat in .env")
        if settings.egress_guard:
            install_egress_guard()
        return settings
    return replace(load_settings({}), data_dir=data_dir, egress_guard=False)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--backend", choices=("mock", "oracle", "openai_compat"), default="mock")
    parser.add_argument("--variant", choices=("text", "ocr", "stress"), default="text")
    parser.add_argument("--json", type=Path, help="write the metrics to this file")
    args = parser.parse_args()
    verbose = args.verbose
    gated = args.backend == "mock" and args.variant == "text"
    expected: dict[str, dict] = json.loads((SAMPLES / "expected.json").read_text())
    with tempfile.TemporaryDirectory(prefix="vaultai-eval-") as tmp:
        settings = _settings(args.backend, Path(tmp) / "data")
        inner = _model(args.backend, settings, expected)
        recorder = Recorder(inner)
        ctx = build_context(settings, recorder)

        docs: dict[str, str] = {}
        preflight_rows = []
        for rel in [k for k, v in expected.items() if "doc_type" in v]:
            name, data = _input(rel, args.variant)
            item = ingest_bytes(ctx, filename=name, data=data, source=Source.SAMPLE, batch_id="eval")
            if item.document_id:
                docs[rel] = item.document_id
        if args.variant == "text":
            for rel, want in expected.items():
                if "expected_preflight" in want:
                    item = ingest_bytes(ctx, filename=Path(rel).name, data=(SAMPLES / rel).read_bytes(),
                                        source=Source.UPLOAD, batch_id="eval-adversarial")
                    preflight_rows.append((rel, item.preflight.value, want["expected_preflight"], item.error))

        started = time.monotonic()
        request_processing(ctx, list(docs.values()), actor="eval")
        for rel, doc_id in docs.items():
            recorder.current = rel
            inner.current = rel
            process_document(ctx, doc_id)
        elapsed = time.monotonic() - started

        score = {k: [0, 0] for k in GATES}
        required = [0, 0]
        counts = Counter()
        statuses: Counter[str] = Counter()
        reasons: dict[str, dict[str, int]] = {key: {} for key in diagnostics.MODEL_MAPS}
        rows = []
        with ctx.db() as conn:
            for rel, doc_id in docs.items():
                want = expected[rel]
                d = conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
                fields = {r["key"]: r for r in conn.execute("SELECT * FROM fields WHERE doc_id = ?", (doc_id,))}
                all_issues = [r["code"] for r in conn.execute("SELECT code, severity FROM issues WHERE doc_id = ?", (doc_id,))]
                issues = {r["code"] for r in conn.execute("SELECT code, severity FROM issues WHERE doc_id = ?", (doc_id,))
                          if r["severity"] != Severity.INFO.value}
                items = [r for r in conn.execute("SELECT * FROM line_items WHERE doc_id = ? ORDER BY idx", (doc_id,))]
                obligations = [(r["kind"], r["due_date"], (r["party"] or "").casefold())
                               for r in conn.execute("SELECT * FROM obligations WHERE doc_id = ?", (doc_id,))]
                clauses = [r for r in conn.execute("SELECT kind, risk FROM clauses WHERE doc_id = ?", (doc_id,))]
                pages = conn.execute("SELECT text_method FROM pages WHERE doc_id = ?", (doc_id,)).fetchall()
                statuses[d["status"]] += 1
                counts["docs_failed"] += d["status"] == "failed"
                counts["pages_without_text"] += sum(p["text_method"] == "none" for p in pages)
                counts["processing_ms"] += d["processing_ms"] or 0
                for code in MODEL_ISSUES:
                    counts[code] += all_issues.count(code)
                model_obligations = [(r["kind"], r["due_date"], (r["party"] or "").casefold()) for r in conn.execute(
                    "SELECT kind, due_date, party FROM obligations WHERE doc_id = ? AND origin = 'model'", (doc_id,))]
                counts["model_obligations"] += len(model_obligations)
                expected_keys = {(o["kind"], o["due_date"], (o["party"] or "").casefold())
                                 for o in want.get("obligations", [])}
                counts["model_obligations_correct"] += sum(key in expected_keys for key in model_obligations)
                stored = conn.execute("SELECT metrics_json FROM doc_metrics WHERE doc_id = ?", (doc_id,)).fetchone()
                model_metrics = diagnostics.normalize(json.loads(stored[0]) if stored else None)["model"]
                for key, kind in diagnostics.MODEL_MAPS.items():
                    diagnostics.merge(reasons[key], model_metrics[key], kind)

                type_ok = d["doc_type"] == want["doc_type"]
                score["doc_type"][0] += type_ok
                score["doc_type"][1] += 1
                misses: list[str] = []
                gold_schema = SCHEMAS[next(t for t in SCHEMAS if t.value == want["doc_type"])]
                for key, value in want["fields"].items():
                    spec = gold_schema.field(key)
                    row = fields.get(key) if type_ok else None
                    got = row["ai_value"] if row else None
                    ok = _same(spec.value_type, got, value)
                    score["fields"][0] += ok
                    score["fields"][1] += 1
                    if spec.required:
                        required[0] += ok
                        required[1] += 1
                    if got is None:
                        counts["missing_fields"] += 1
                    elif not ok:
                        counts["incorrect_fields"] += 1
                    if row is not None and row["ai_origin"] == "model":
                        counts["model_values"] += 1
                        counts["model_values_correct"] += ok
                    if not ok:
                        misses.append(f"{key}: want {value!r} got {got!r}")
                status_ok = d["status"] == want["expected_status"]
                score["status"][0] += status_ok
                score["status"][1] += 1
                issues_ok = issues == set(want["expected_issue_codes"])
                score["issues"][0] += issues_ok
                score["issues"][1] += 1
                items_note = ""
                if "line_items" in want:
                    got_items = [(r["description"], _dec(r["quantity"]), _dec(r["unit_price"]), _dec(r["amount"])) for r in items]
                    want_items = [(w[0], *(Decimal(v) for v in w[1:])) for w in want["line_items"]]
                    ok = got_items == want_items
                    score["line_items"][0] += ok
                    score["line_items"][1] += 1
                    items_note = f"{len(got_items)}/{len(want_items)}" + ("" if ok else " ✗")
                obl_note = ""
                notes: list[str] = []
                if "obligations" in want:
                    remaining = list(obligations)
                    found = 0
                    for o in want["obligations"]:
                        key = (o["kind"], o["due_date"], (o["party"] or "").casefold())
                        if key in remaining:
                            remaining.remove(key)
                            found += 1
                        elif verbose:
                            notes.append(f"obligation missing: {key}")
                    score["obligations"][0] += found
                    score["obligations"][1] += len(want["obligations"])
                    obl_note = f"{found}/{len(want['obligations'])} (+{len(remaining)})"
                risky_note = ""
                if "risky_clause_kinds" in want:
                    risky = {c["kind"] for c in clauses if c["risk"] != "none" and c["kind"] != "renewal"}
                    risky_note = "ok" if risky == set(want["risky_clause_kinds"]) else f"✗ {sorted(risky)}"
                rows.append((rel, d["doc_type"], type_ok, len(want["fields"]) - len(misses), len(want["fields"]),
                             d["status"], want["expected_status"], issues_ok, sorted(issues),
                             sorted(want["expected_issue_codes"]), items_note, obl_note, risky_note, misses + notes))

        for rel, got, want, error in preflight_rows:
            score["preflight"][0] += got == want
            score["preflight"][1] += 1

    calls = recorder.calls
    by_task = Counter(c.task for c in calls)
    w = max(len(r[0]) for r in rows)
    print(f"{'document':{w}}  {'type':18} {'fields':7} {'status':26} {'issues':6} {'items':7} {'obligations':12} "
          f"{'risky':6} calls")
    per_doc_calls = Counter(c.doc for c in calls)
    for (rel, dtype, type_ok, ok_fields, n_fields, status, want_status, issues_ok, issues, want_issues,
         items_note, obl_note, risky_note, misses) in rows:
        status_cell = status if status == want_status else f"{status}≠{want_status}"
        print(f"{rel:{w}}  {(dtype or '-') + ('' if type_ok else ' ✗'):18} {ok_fields:>2}/{n_fields:<4} {status_cell:26} "
              f"{'ok' if issues_ok else '✗':6} {items_note:7} {obl_note:12} {risky_note:6} {per_doc_calls[rel]}")
        if not issues_ok and (verbose or gated):
            print(f"{'':{w}}    issues got {issues} want {want_issues}")
        if verbose or gated:
            for m in misses:
                print(f"{'':{w}}    {m}")
    print()
    for rel, got, want, error in preflight_rows:
        print(f"{rel:{w}}  preflight {got:9} {'ok' if got == want else '✗ want ' + want}  {error or ''}")
    if preflight_rows:
        print()
    failed = []
    for name, (ok, total) in score.items():
        if not total:
            continue
        rate = ok / total
        gate = GATES[name]
        mark = ("ok" if rate >= gate else "FAIL") if gated else "(not gated)"
        if gated and rate < gate:
            failed.append(name)
        print(f"{name:12} {ok:>3}/{total:<3} {rate:6.1%}  gate {gate:.0%}  {mark}")

    n_docs = len(docs)
    accepted = counts["model_values"] + counts["model_obligations"]
    proposed = accepted + counts["ungrounded_value"]
    prompt_chars = [c.prompt_chars for c in calls]
    tokens = [c.prompt_tokens + (c.completion_tokens or 0) for c in calls if c.prompt_tokens is not None]
    metrics = {
        "backend": args.backend,
        "variant": args.variant,
        "model": ctx.model.name,
        "documents": n_docs,
        "doc_type_accuracy": round(score["doc_type"][0] / n_docs, 4),
        "required_field_accuracy": round(required[0] / required[1], 4) if required[1] else None,
        "field_accuracy": round(score["fields"][0] / score["fields"][1], 4),
        "missing_fields": counts["missing_fields"],
        "incorrect_fields": counts["incorrect_fields"],
        "model_values_accepted": counts["model_values"],
        "model_values_correct": counts["model_values_correct"],
        "model_obligations_accepted": counts["model_obligations"],
        "model_obligations_correct": counts["model_obligations_correct"],
        "ungrounded_values_rejected": counts["ungrounded_value"],
        "grounded_value_rate": round(accepted / proposed, 4) if proposed else None,
        "model_outputs_rejected": counts["model_output_rejected"],
        "model_output_rejections_by_reason": reasons["rejections_by_reason"],
        "model_failure_reasons": reasons["failure_reasons"],
        "grounding_rejections_by_reason": reasons["grounding_rejections_by_reason"],
        "model_unavailable": counts["model_unavailable"],
        "model_calls": len(calls),
        "model_calls_per_document": round(len(calls) / n_docs, 2),
        "model_calls_by_task": dict(sorted(by_task.items())),
        "model_attempts": sum(c.attempts for c in calls),
        "prompt_chars_mean": round(sum(prompt_chars) / len(prompt_chars)) if prompt_chars else 0,
        "prompt_chars_max": max(prompt_chars, default=0),
        "prompt_chars_mean_by_task": {
            task: round(sum(c.prompt_chars for c in calls if c.task == task) / n) for task, n in sorted(by_task.items())},
        "tokens_reported": sum(tokens) if tokens else None,
        "model_ms_total": round(sum(c.ms for c in calls)),
        "avg_processing_ms": round(counts["processing_ms"] / n_docs),
        "wall_s": round(elapsed, 2),
        "docs_failed": counts["docs_failed"],
        "pages_without_text": counts["pages_without_text"],
        "status_distribution": dict(sorted(statuses.items())),
        "status_matches_expected": score["status"][0],
        "issue_sets_match_expected": score["issues"][0],
    }
    print()
    for key, value in metrics.items():
        print(f"{key:28} {value}")
    if args.json:
        args.json.write_text(json.dumps(metrics, indent=2) + "\n")
    print(f"\nprocessed {n_docs} documents in {elapsed:.1f}s with {ctx.model.name}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
