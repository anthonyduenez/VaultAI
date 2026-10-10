#!/usr/bin/env python3
"""Run every sample through ingest + processing (MockModel, temp data dir) and score it against samples/expected.json.

Usage: backend/.venv/bin/python scripts/eval_samples.py [--verbose]

Exit status is non-zero when any gate below fails. The gates are the bar the
rules must clear on the demo set; they are not tuned to the current score.
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
from dataclasses import replace
from decimal import Decimal, InvalidOperation
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))

from vaultai.app import build_context  # noqa: E402
from vaultai.config import REPO_ROOT, load_settings  # noqa: E402
from vaultai.domain import Severity, Source, ValueType  # noqa: E402
from vaultai.intake.ingest import ingest_bytes  # noqa: E402
from vaultai.model.mock import MockModel  # noqa: E402
from vaultai.pipeline.process import process_document, request_processing  # noqa: E402
from vaultai.schemas import SCHEMAS  # noqa: E402

SAMPLES = REPO_ROOT / "samples"
GATES = {
    "doc_type": 1.0,           # every document classified correctly
    "status": 1.0,             # every document routed to the expected review status
    "issues": 1.0,             # warning/error issue codes match exactly
    "line_items": 1.0,         # every line-item table read exactly
    "preflight": 1.0,          # every adversarial file handled as expected
    "fields": 0.95,            # share of expected field values extracted exactly
    "obligations": 0.9,        # share of expected obligations found (kind, due date, party)
}


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


def main() -> int:
    verbose = "--verbose" in sys.argv
    expected: dict[str, dict] = json.loads((SAMPLES / "expected.json").read_text())
    with tempfile.TemporaryDirectory(prefix="vaultai-eval-") as tmp:
        settings = replace(load_settings({}), data_dir=Path(tmp) / "data", egress_guard=False)
        ctx = build_context(settings, MockModel())

        docs: dict[str, str] = {}
        preflight_rows = []
        for rel in [k for k, v in expected.items() if "doc_type" in v]:
            item = ingest_bytes(ctx, filename=Path(rel).name, data=(SAMPLES / rel).read_bytes(), source=Source.SAMPLE,
                                batch_id="eval")
            if item.document_id:
                docs[rel] = item.document_id
        for rel, want in expected.items():
            if "expected_preflight" in want:
                item = ingest_bytes(ctx, filename=Path(rel).name, data=(SAMPLES / rel).read_bytes(),
                                    source=Source.UPLOAD, batch_id="eval-adversarial")
                preflight_rows.append((rel, item.preflight.value, want["expected_preflight"], item.error))

        started = time.monotonic()
        request_processing(ctx, list(docs.values()), actor="eval")
        for doc_id in docs.values():
            process_document(ctx, doc_id)
        elapsed = time.monotonic() - started

        score = {k: [0, 0] for k in GATES}
        rows = []
        with ctx.db() as conn:
            for rel, doc_id in docs.items():
                want = expected[rel]
                d = conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
                fields = {r["key"]: r for r in conn.execute("SELECT * FROM fields WHERE doc_id = ?", (doc_id,))}
                issues = {r["code"] for r in conn.execute("SELECT code, severity FROM issues WHERE doc_id = ?", (doc_id,))
                          if r["severity"] != Severity.INFO.value}
                items = [r for r in conn.execute("SELECT * FROM line_items WHERE doc_id = ? ORDER BY idx", (doc_id,))]
                obligations = [(r["kind"], r["due_date"], (r["party"] or "").casefold())
                               for r in conn.execute("SELECT * FROM obligations WHERE doc_id = ?", (doc_id,))]
                clauses = [r for r in conn.execute("SELECT kind, risk FROM clauses WHERE doc_id = ?", (doc_id,))]

                type_ok = d["doc_type"] == want["doc_type"]
                score["doc_type"][0] += type_ok
                score["doc_type"][1] += 1
                misses: list[str] = []
                schema = SCHEMAS.get(d["doc_type"]) if type_ok else None
                for key, value in want["fields"].items():
                    vt = schema.field(key).value_type if schema else ValueType.TEXT
                    got = fields[key]["ai_value"] if key in fields else None
                    ok = _same(vt, got, value)
                    score["fields"][0] += ok
                    score["fields"][1] += 1
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
                    # Auto-renewal has its own issue code; "risky" means the other flagged clause kinds.
                    risky = {c["kind"] for c in clauses if c["risk"] != "none" and c["kind"] != "renewal"}
                    risky_note = "ok" if risky == set(want["risky_clause_kinds"]) else f"✗ {sorted(risky)}"
                rows.append((rel, d["doc_type"], type_ok, len(want["fields"]) - len(misses), len(want["fields"]),
                             d["status"], want["expected_status"], issues_ok, sorted(issues),
                             sorted(want["expected_issue_codes"]), items_note, obl_note, risky_note, misses + notes))

        for rel, got, want, error in preflight_rows:
            score["preflight"][0] += got == want
            score["preflight"][1] += 1

    w = max(len(r[0]) for r in rows)
    print(f"{'document':{w}}  {'type':18} {'fields':7} {'status':26} {'issues':6} {'items':7} {'obligations':12} risky")
    for (rel, dtype, type_ok, ok_fields, n_fields, status, want_status, issues_ok, issues, want_issues,
         items_note, obl_note, risky_note, misses) in rows:
        status_cell = status if status == want_status else f"{status}≠{want_status}"
        print(f"{rel:{w}}  {(dtype or '-') + ('' if type_ok else ' ✗'):18} {ok_fields:>2}/{n_fields:<4} {status_cell:26} "
              f"{'ok' if issues_ok else '✗':6} {items_note:7} {obl_note:12} {risky_note}")
        if not issues_ok:
            print(f"{'':{w}}    issues got {issues} want {want_issues}")
        for m in misses:
            print(f"{'':{w}}    {m}")
    print()
    for rel, got, want, error in preflight_rows:
        print(f"{rel:{w}}  preflight {got:9} {'ok' if got == want else '✗ want ' + want}  {error or ''}")
    print()
    failed = []
    for name, (ok, total) in score.items():
        rate = ok / total if total else 1.0
        gate = GATES[name]
        mark = "ok" if rate >= gate else "FAIL"
        if rate < gate:
            failed.append(name)
        print(f"{name:12} {ok:>3}/{total:<3} {rate:6.1%}  gate {gate:.0%}  {mark}")
    print(f"\nprocessed {len(docs)} documents in {elapsed:.1f}s with {ctx.model.name}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
