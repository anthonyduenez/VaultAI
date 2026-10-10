from __future__ import annotations

import threading
from dataclasses import replace

import pytest

from conftest import make_ctx
from core.conftest import doc, ingest, invoice_pdf
from vaultai.domain import DocStatus, PreflightStatus
from vaultai.intake.filenames import display_name
from vaultai.parsing import isolate
from vaultai.pipeline.process import process_document, request_processing
from vaultai.storage import Storage, UnsafePath


@pytest.mark.parametrize(
    ("raw", "shown"),
    [
        ("../../etc/passwd", "passwd"),
        ("..\\..\\win.ini", "win.ini"),
        ("folder/sub/Invoice 42.pdf", "Invoice 42.pdf"),
        ("in\x00voice\x1f.pdf", "invoice.pdf"),
        ("invoice‮gpj.pdf", "invoicegpj.pdf"),
        ("", "untitled"),
        ("..", "untitled"),
        ("/", "untitled"),
        ("Café.pdf", "Café.pdf"),
        ("a" * 200 + ".pdf", "a" * 116 + ".pdf"),
    ],
)
def test_display_names_are_safe_basenames(raw, shown):
    assert display_name(raw) == shown


def test_storage_refuses_paths_that_escape_and_never_overwrites(mock_ctx):
    storage = mock_ctx.storage
    with pytest.raises(UnsafePath):
        storage.resolve(storage.raw, "../vaultai.db")
    name = Storage.new_name("pdf")
    storage.write_new(storage.raw, name, b"first")
    with pytest.raises(FileExistsError):
        storage.write_new(storage.raw, name, b"second")
    assert (storage.raw / name).read_bytes() == b"first"


def test_duplicate_upload_points_at_the_original(mock_ctx):
    first = ingest(mock_ctx, "a.pdf", invoice_pdf())
    second = ingest(mock_ctx, "copy of a.pdf", invoice_pdf())
    assert (second.preflight, second.duplicate_of, second.document_id) == (PreflightStatus.DUPLICATE, first.document_id, None)
    assert len(list(mock_ctx.storage.raw.iterdir())) == 1


def test_concurrent_duplicate_uploads_store_one_document(mock_ctx):
    data = invoice_pdf(number="RACE-1")
    results = []
    barrier = threading.Barrier(4)

    def upload(i: int) -> None:
        barrier.wait()
        results.append(ingest(mock_ctx, f"race{i}.pdf", data).preflight.value)

    threads = [threading.Thread(target=upload, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    with mock_ctx.db() as conn:
        count = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    assert (sorted(results), count, len(list(mock_ctx.storage.raw.iterdir()))) == (
        ["accepted", "duplicate", "duplicate", "duplicate"], 1, 1)


def test_parser_timeout_rejects_instead_of_hanging(settings, monkeypatch, write_worker):
    ctx = make_ctx(replace(settings, parse_timeout_s=1))
    monkeypatch.setattr(isolate, "WORKER", write_worker("import sys, time\nsys.stdin.read()\ntime.sleep(30)\n"))
    item = ingest(ctx, "slow.pdf", invoice_pdf())
    assert (item.preflight, item.error) == (PreflightStatus.REJECTED, "The file could not be parsed safely.")


# SIGKILL, not os.abort(): macOS pops a crash-reporter dialog for every SIGABRT.
CRASHING_WORKER = "import os, signal\nos.kill(os.getpid(), signal.SIGKILL)\n"


def test_parser_crash_rejects(mock_ctx, monkeypatch, write_worker):
    monkeypatch.setattr(isolate, "WORKER", write_worker(CRASHING_WORKER))
    item = ingest(mock_ctx, "crash.pdf", invoice_pdf())
    assert (item.preflight, item.error) == (PreflightStatus.REJECTED, "The file could not be parsed safely.")


def test_parser_never_sees_vaultai_environment(mock_ctx, monkeypatch, write_worker):
    monkeypatch.setenv("VAULTAI_MODEL_API_KEY", "secret-token")
    monkeypatch.setattr(isolate, "WORKER", write_worker(
        "import json, os, sys\nsys.stdin.read()\n"
        "leaked = [k for k in os.environ if k.startswith('VAULTAI')]\n"
        "print(json.dumps({'ok': not leaked, 'pages': 1, 'error': 'leak'}))\n"))
    assert ingest(mock_ctx, "env.pdf", invoice_pdf()).preflight is PreflightStatus.ACCEPTED


def test_crash_during_processing_fails_the_document_with_a_redacted_error(mock_ctx, monkeypatch, write_worker):
    item = ingest(mock_ctx, "ok.pdf", invoice_pdf(vendor="Quokka Secret Supplies"))
    monkeypatch.setattr(isolate, "WORKER", write_worker(CRASHING_WORKER))
    request_processing(mock_ctx, [item.document_id], actor="test")
    process_document(mock_ctx, item.document_id)
    row = doc(mock_ctx, item.document_id)
    assert (row["status"], row["error"]) == (DocStatus.FAILED.value, "ParseFailed (crashed): the document could not be processed.")
