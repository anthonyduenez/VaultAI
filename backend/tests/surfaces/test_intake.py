from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.surfaces.conftest import FakeCore, insert_document, make_client
from vaultai.config import Settings
from vaultai.context import AppContext


def test_upload_keeps_basenames_and_truncates_oversize_files(tmp_path: Path, fake_core: FakeCore) -> None:
    settings = Settings(data_dir=tmp_path / "data", egress_guard=False, scanner_backend="none", max_file_mb=1)
    client = make_client(settings)
    limit = settings.max_file_bytes
    response = client.post(
        "/api/intake/upload",
        data={"source": "folder"},
        files=[("files", ("Q3/receipts/a.pdf", b"%PDF a", "application/pdf")),
               ("files", ("..\\..\\b.pdf", b"x" * (limit + 5000), "application/pdf"))],
    )
    assert response.status_code == 200
    assert [i["filename"] for i in response.json()] == ["a.pdf", "b.pdf"]
    assert [(c["filename"], len(c["data"]), c["source"].value) for c in fake_core.ingested] == [
        ("a.pdf", 6, "folder"), ("b.pdf", limit + 1, "folder"),
    ]


def test_upload_limits_and_validation(tmp_path: Path, fake_core: FakeCore) -> None:
    client = make_client(Settings(data_dir=tmp_path / "data", egress_guard=False, scanner_backend="none",
                                  max_batch_files=2))
    files = [("files", (f"{i}.pdf", b"%PDF", "application/pdf")) for i in range(3)]
    too_many = client.post("/api/intake/upload", files=files)
    bad_source = client.post("/api/intake/upload", data={"source": "scanner"}, files=files[:1])
    none = client.post("/api/intake/upload", data={"source": "upload"})
    assert [(r.status_code, r.json()["code"]) for r in (too_many, bad_source, none)] == [
        (422, "too_many_files"), (422, "invalid_request"), (422, "no_files"),
    ]
    assert fake_core.ingested == []


def test_samples_reads_only_regular_files_in_demo(tmp_path: Path, fake_core: FakeCore) -> None:
    demo = tmp_path / "samples" / "demo"
    (demo / "nested").mkdir(parents=True)
    (demo / "b.pdf").write_bytes(b"%PDF b")
    (demo / "a.pdf").write_bytes(b"%PDF a")
    (demo / ".DS_Store").write_bytes(b"junk")
    (demo / "nested" / "c.pdf").write_bytes(b"%PDF c")
    (tmp_path / "secret.pdf").write_bytes(b"%PDF secret")
    (demo / "link.pdf").symlink_to(tmp_path / "secret.pdf")
    client = make_client(Settings(data_dir=tmp_path / "data", samples_dir=tmp_path / "samples", egress_guard=False,
                                  scanner_backend="none"))

    items = client.post("/api/intake/samples").json()
    assert [(i["filename"], i["source"]) for i in items] == [("a.pdf", "sample"), ("b.pdf", "sample")]
    assert [c["data"] for c in fake_core.ingested] == [b"%PDF a", b"%PDF b"]
    assert [i["filename"] for i in client.get("/api/intake").json()] == ["b.pdf", "a.pdf"]


def test_process_selects_imported_documents(
    client: TestClient, app_ctx: AppContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    requested: list[list[str]] = []

    def request_processing(ctx: AppContext, doc_ids: list[str], *, actor: str, reprocess: bool = False) -> list[str]:
        requested.append(doc_ids)
        return doc_ids

    module = types.ModuleType("vaultai.pipeline.process")
    module.request_processing = request_processing  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "vaultai.pipeline.process", module)
    imported = insert_document(app_ctx, status="imported")
    insert_document(app_ctx, status="needs_review")

    assert client.post("/api/intake/process", json={}).json() == {"queued": [imported]}
    assert client.post("/api/intake/process", json={"intake_ids": []}).json() == {"queued": []}
    assert requested == [[imported], []]
