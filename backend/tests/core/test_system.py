"""Egress guard, job queue, and metrics: the process-level pieces around the pipeline."""

from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path

from core.conftest import FakeOcr, ctx_with, ingest, invoice_pdf, png_bytes, receipt_pdf, run
from vaultai.metrics import compute_metrics
from vaultai.parsing import ocr
from vaultai.pipeline import queue as queue_module
from vaultai.pipeline.process import process_document, request_processing
from vaultai.pipeline.queue import JobQueue

BACKEND = Path(__file__).resolve().parents[2]

# Run in a child interpreter: the guard patches sockets process-wide and must not leak into other tests.
EGRESS_PROBE = f"""
import socket, sys
sys.path.insert(0, {str(BACKEND)!r})
from vaultai.security.egress import EgressBlocked, install_egress_guard
install_egress_guard()
install_egress_guard()
server = socket.socket()
server.bind(("127.0.0.1", 0))
server.listen(1)
socket.create_connection(server.getsockname(), timeout=2).close()
print("local ok")
for target in [("203.0.113.5", 80), ("8.8.8.8", 53)]:
    s = socket.socket()
    try:
        s.connect(target)
        print("connected", target[0])
    except EgressBlocked as exc:
        print("blocked", target[0])
    finally:
        s.close()
try:
    socket.create_connection(("203.0.113.5", 443), timeout=2)
except EgressBlocked:
    print("blocked create_connection")
"""


def test_egress_guard_blocks_public_addresses_and_allows_loopback():
    out = subprocess.run([sys.executable, "-c", EGRESS_PROBE], capture_output=True, text=True, timeout=30, check=True)
    assert out.stdout.splitlines() == ["local ok", "blocked 203.0.113.5", "blocked 8.8.8.8", "blocked create_connection"]


def test_queue_processes_new_work_and_recovers_documents_left_processing(mock_ctx):
    stranded = ingest(mock_ctx, "a.pdf", invoice_pdf(number="Q-1")).document_id
    request_processing(mock_ctx, [stranded], actor="test")
    later = ingest(mock_ctx, "b.pdf", invoice_pdf(number="Q-2")).document_id
    from_thread = ingest(mock_ctx, "c.pdf", invoice_pdf(number="Q-3")).document_id
    jobs = JobQueue(mock_ctx)
    mock_ctx.queue = jobs

    async def main() -> None:
        await jobs.start()
        request_processing(mock_ctx, [later], actor="test")
        jobs.enqueue(later)
        await asyncio.to_thread(request_processing, mock_ctx, [from_thread], actor="test")
        await jobs.join()
        await jobs.stop()

    asyncio.run(main())
    with mock_ctx.db() as conn:
        statuses = [r["status"] for r in conn.execute("SELECT status FROM documents ORDER BY created_at")]
        processed = conn.execute("SELECT COUNT(*) FROM audit_log WHERE actor = 'pipeline'").fetchone()[0]
    assert (statuses, processed) == (["ready", "ready", "ready"], 3)


def test_a_worker_survives_an_exception(mock_ctx, monkeypatch):
    bad = ingest(mock_ctx, "bad.pdf", invoice_pdf(number="W-1")).document_id
    good = ingest(mock_ctx, "good.pdf", invoice_pdf(number="W-2")).document_id
    request_processing(mock_ctx, [bad, good], actor="test")

    def flaky(ctx, doc_id):
        if doc_id == bad:
            raise RuntimeError("boom")
        process_document(ctx, doc_id)

    monkeypatch.setattr(queue_module, "process_document", flaky)
    jobs = JobQueue(mock_ctx.__class__(mock_ctx.settings, mock_ctx.storage, mock_ctx.model))

    async def main() -> None:
        await jobs.start()
        await jobs.join()
        await jobs.stop()

    asyncio.run(main())
    with mock_ctx.db() as conn:
        statuses = {r["id"]: r["status"] for r in conn.execute("SELECT id, status FROM documents")}
    assert statuses == {bad: "processing", good: "ready"}


def test_metrics_count_work_review_outcomes_and_ocr_cache(settings, monkeypatch):
    monkeypatch.setitem(ocr.ENGINES, "fake", lambda _s: FakeOcr(["MAPLE CORNER HARDWARE", "Receipt #: MC-1"], conf=0.9))
    ctx = ctx_with(settings, ocr_engine="fake")
    run(ctx, "receipt.pdf", receipt_pdf())
    scanned = run(ctx, "scan.png", png_bytes())
    request_processing(ctx, [scanned], actor="test", reprocess=True)
    process_document(ctx, scanned)
    m = compute_metrics(ctx)
    assert (m.documents_processed, m.pages_processed, m.auto_approval_rate, m.cache_hits, m.cache_misses) == (2, 2, 0.5, 1, 1)
    assert [t.filename for t in m.recent] == ["scan.png", "receipt.pdf"]
    assert m.rss_mb > 0 and m.avg_ms_per_page > 0
