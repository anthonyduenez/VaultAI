from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.surfaces.conftest import FakeCore, insert_document, make_client
from vaultai.api import intake
from vaultai.config import Settings
from vaultai.context import AppContext


def test_folder_upload_keeps_only_basenames(tmp_path: Path) -> None:
    client = make_client(Settings(data_dir=tmp_path / "data", egress_guard=False, scanner_backend="none"))
    response = client.post(
        "/api/intake/upload", data={"source": "folder"},
        files=[("files", ("Q3/receipts/a.pdf", b"%PDF a", "application/pdf")),
               ("files", ("..\\..\\b.pdf", b"%PDF b", "application/pdf"))],
    )
    assert [(i["filename"], i["source"]) for i in response.json()] == [("a.pdf", "folder"), ("b.pdf", "folder")]


def test_oversize_uploads_are_read_only_one_byte_past_the_limit(tmp_path: Path, fake_core: FakeCore) -> None:
    settings = Settings(data_dir=tmp_path / "data", egress_guard=False, scanner_backend="none", max_file_mb=1)
    limit = settings.max_file_bytes
    response = make_client(settings).post(
        "/api/intake/upload",
        files=[("files", ("a.pdf", b"%PDF a", "application/pdf")),
               ("files", ("b.pdf", b"x" * (limit + 5000), "application/pdf"))],
    )
    assert response.status_code == 200
    assert [(len(c["data"]), c["source"].value) for c in fake_core.ingested] == [(6, "upload"), (limit + 1, "upload")]


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


def test_process_selects_imported_documents(
    client: TestClient, app_ctx: AppContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    requested: list[list[str]] = []

    def request_processing(ctx: AppContext, doc_ids: list[str], *, actor: str, reprocess: bool = False) -> list[str]:
        requested.append(doc_ids)
        return doc_ids

    monkeypatch.setattr(intake, "request_processing", request_processing)
    imported = insert_document(app_ctx, status="imported")
    insert_document(app_ctx, status="needs_review")

    assert client.post("/api/intake/process", json={}).json() == {"queued": [imported]}
    assert client.post("/api/intake/process", json={"intake_ids": []}).json() == {"queued": []}
    assert requested == [[imported], []]
