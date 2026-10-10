"""End-to-end through the real core pipeline."""

from __future__ import annotations

import io
import re
import time
from collections.abc import Iterator
from pathlib import Path

import openpyxl
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from tests.surfaces.conftest import BASE_URL, CSRF
from vaultai.app import create_app
from vaultai.config import REPO_ROOT, Settings
from vaultai.phone_app import create_phone_app

DEMO = REPO_ROOT / "samples" / "demo"
SETTLED = {"ready", "needs_review", "approved", "failed"}


@pytest.fixture
def live(tmp_path: Path) -> Iterator[TestClient]:
    settings = Settings(data_dir=tmp_path / "data", ocr_engine="none", scanner_backend="mock", egress_guard=False,
                        phone_enabled=False, max_file_mb=2)
    with TestClient(create_app(settings), base_url=BASE_URL, headers=CSRF) as client:
        yield client


def wait_settled(client: TestClient, doc_id: str, timeout: float = 90) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        doc = client.get(f"/api/documents/{doc_id}").json()
        if doc["status"] in SETTLED:
            return doc
        time.sleep(0.2)
    raise AssertionError(f"document {doc_id} did not finish processing")


def upload(client: TestClient, path: Path) -> dict:
    response = client.post("/api/intake/upload", data={"source": "upload"},
                           files=[("files", (path.name, path.read_bytes(), "application/pdf"))])
    assert response.status_code == 200, response.text
    return response.json()[0]


def test_upload_process_edit_and_reprocess_keeps_human_edits(live: TestClient) -> None:
    item = upload(live, DEMO / "invoice_acme_INV-2026-0142.pdf")
    assert (item["preflight"], item["doc_status"]) == ("accepted", "imported")
    assert live.post("/api/intake/process", json={"intake_ids": [item["id"]]}).json() == {"queued": [item["document_id"]]}
    doc = wait_settled(live, item["document_id"])
    assert doc["doc_type"] == "invoice"
    assert any(f["evidence"] for f in doc["fields"])

    vendor = next(f for f in doc["fields"] if f["key"] == "vendor")
    edited = live.patch(f"/api/documents/{doc['id']}/fields/vendor", json={"value": "Acme Industrial Supply"}).json()
    assert next(f for f in edited["fields"] if f["key"] == "vendor")["origin"] == "human"

    summary = live.post(f"/api/documents/{doc['id']}/reprocess")
    assert (summary.status_code, summary.json()["status"]) == (200, "processing")
    after = wait_settled(live, doc["id"])
    kept = next(f for f in after["fields"] if f["key"] == "vendor")
    assert (kept["value"], kept["origin"], kept["ai_value"]) == ("Acme Industrial Supply", "human", vendor["ai_value"])


def test_oversize_upload_is_rejected_by_preflight(live: TestClient, tmp_path: Path) -> None:
    big = tmp_path / "big.pdf"
    big.write_bytes(b"%PDF-1.7\n" + b"0" * (3 * 1024 * 1024))
    item = upload(live, big)
    assert (item["preflight"], item["document_id"]) == ("rejected", None)


def test_samples_review_and_export_round_trip(live: TestClient) -> None:
    items = live.post("/api/intake/samples").json()
    assert len(items) == len([p for p in DEMO.iterdir() if p.is_file() and not p.name.startswith(".")])
    queued = live.post("/api/intake/process", json={}).json()["queued"]
    docs = [wait_settled(live, doc_id) for doc_id in queued]
    reviewable = [d for d in docs if d["status"] in {"ready", "needs_review"}]
    target = reviewable[0]
    live.post(f"/api/documents/{target['id']}/review", json={"action": "approve", "note": "Checked in integration"})

    assert live.get("/api/actions").status_code == 200
    assert live.get("/api/dashboard").json()["total_documents"] == len(items)
    assert live.get("/api/system/status").json()["model"]["kind"] == "mock"
    assert live.get("/api/system/metrics").status_code == 200

    job = live.post("/api/exports", json={}).json()
    workbook = openpyxl.load_workbook(io.BytesIO(live.get(f"/api/exports/{job['id']}/download").content))
    ids = [row[0].value for row in workbook["Approved Records"].iter_rows(min_row=2)]
    assert target["id"] in ids
    assert all(live.get(f"/api/documents/{i}").json()["status"] == "exported" for i in ids)


def test_scanner_assembly_enters_intake_as_scanner(live: TestClient) -> None:
    session_id = live.post("/api/scanner/sessions", json={"device_id": "mock:feeder"}).json()["id"]
    pages = live.post(f"/api/scanner/sessions/{session_id}/scan", json={"dpi": 100, "duplex": True}).json()["pages"]
    item = live.post(f"/api/scanner/sessions/{session_id}/assemble",
                     json={"title": "Scanned receipt", "pages": [{"id": p["id"]} for p in pages]}).json()
    assert (item["source"], item["preflight"], item["page_count"]) == ("scanner", "accepted", 2)
    assert live.get(f"/api/scanner/sessions/{session_id}").json()["status"] == "completed"


def test_phone_submission_enters_intake_as_phone(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data", ocr_engine="none", scanner_backend="none", egress_guard=False,
                        phone_enabled=True, phone_host="192.168.1.10")
    desktop = TestClient(create_app(settings), base_url=BASE_URL, headers=CSRF)
    pairing = desktop.post("/api/phone/sessions").json()
    session_id, token = re.search(r"/m/([\w-]+)#([\w-]+)", pairing["url"]).groups()  # type: ignore[union-attr]
    buf = io.BytesIO()
    Image.new("RGB", (850, 1100), "white").save(buf, "JPEG")
    lan = TestClient(create_phone_app(desktop.app.state.ctx), base_url="http://192.168.1.10:8766",
                     client=("192.168.1.20", 50123))
    response = lan.post(f"/p/{session_id}/submit", headers={"X-Pairing-Token": token},
                        data={"edits": "[{\"rotation\": 0, \"crop\": null}]"},
                        files=[("pages", ("p.jpg", buf.getvalue(), "image/jpeg"))])
    item = response.json()
    assert (response.status_code, item["source"], item["preflight"]) == (200, "phone", "accepted")
    assert desktop.get(f"/api/phone/sessions/{session_id}").json()["document_id"] == item["document_id"]
