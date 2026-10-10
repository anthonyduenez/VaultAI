from __future__ import annotations

import io
import subprocess
from datetime import UTC, datetime
from types import SimpleNamespace

import pypdfium2 as pdfium
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from tests.conftest import make_ctx
from tests.surfaces.conftest import FakeCore
from vaultai import db
from vaultai.capture import scanner as scanner_module
from vaultai.capture.assemble import apply_edit, capture_filename, images_to_pdf
from vaultai.capture.scanner import SaneScanner
from vaultai.config import Settings
from vaultai.domain import ScanRequest
from vaultai.repo import Problem


def pdf_page_sizes(data: bytes) -> list[tuple[int, int]]:
    pdf = pdfium.PdfDocument(data)
    try:
        return [tuple(round(v) for v in pdf[i].get_size()) for i in range(len(pdf))]
    finally:
        pdf.close()


def new_session(client: TestClient) -> str:
    response = client.post("/api/scanner/sessions", json={"device_id": "mock:feeder"})
    assert response.status_code == 200
    return response.json()["id"]


def test_mock_device_is_listed(client: TestClient) -> None:
    assert client.get("/api/scanner/devices").json() == [
        {"id": "mock:feeder", "name": "VaultAI demo scanner (mock)", "backend": "mock", "duplex": True}
    ]


def test_unknown_device_is_404(client: TestClient) -> None:
    assert client.post("/api/scanner/sessions", json={"device_id": "epson:usb:1"}).status_code == 404


def test_scan_duplex_retake_delete_and_cancel(client: TestClient) -> None:
    session_id = new_session(client)
    simplex = client.post(f"/api/scanner/sessions/{session_id}/scan", json={"dpi": 75}).json()
    assert [p["idx"] for p in simplex["pages"]] == [0]
    duplex = client.post(f"/api/scanner/sessions/{session_id}/scan", json={"dpi": 75, "duplex": True}).json()
    pages = duplex["pages"]
    assert [p["idx"] for p in pages] == [0, 1, 2]

    image = client.get(f"/api/scanner/sessions/{session_id}/pages/{pages[1]['id']}/image")
    assert (image.headers["content-type"], Image.open(io.BytesIO(image.content)).mode) == ("image/png", "L")

    retaken = client.post(
        f"/api/scanner/sessions/{session_id}/pages/{pages[1]['id']}/retake", json={"dpi": 100, "color": "color"}
    ).json()
    assert [p["id"] for p in retaken["pages"]] == [p["id"] for p in pages]
    image = client.get(f"/api/scanner/sessions/{session_id}/pages/{pages[1]['id']}/image")
    assert Image.open(io.BytesIO(image.content)).mode == "RGB"

    remaining = client.delete(f"/api/scanner/sessions/{session_id}/pages/{pages[0]['id']}").json()["pages"]
    assert [(p["id"], p["idx"]) for p in remaining] == [(pages[1]["id"], 0), (pages[2]["id"], 1)]

    assert client.delete(f"/api/scanner/sessions/{session_id}").status_code == 204
    assert client.get(f"/api/scanner/sessions/{session_id}").json()["status"] == "cancelled"
    assert client.get(f"/api/scanner/sessions/{session_id}/pages/{pages[1]['id']}/image").status_code == 404
    assert client.post(f"/api/scanner/sessions/{session_id}/scan", json={}).json()["code"] == "session_closed"


def test_assemble_applies_edits_and_ingests_as_scanner(
    client: TestClient, fake_core: FakeCore, settings: Settings
) -> None:
    session_id = new_session(client)
    client.post(f"/api/scanner/sessions/{session_id}/scan", json={"dpi": 75, "duplex": True})
    first, second = client.get(f"/api/scanner/sessions/{session_id}").json()["pages"]

    response = client.post(
        f"/api/scanner/sessions/{session_id}/assemble",
        json={"title": "Harbor / fuel", "doc_type_hint": "receipt",
              "pages": [{"id": second["id"], "rotation": 90}, {"id": first["id"], "crop": [0, 0, 1, 0.5]}]},
    )
    item = response.json()
    assert (item["source"], item["preflight"], item["doc_status"]) == ("scanner", "accepted", "imported")
    sent = fake_core.ingested[0]
    assert sent["filename"].startswith("Harbor fuel 20") and sent["filename"].endswith(".pdf")
    assert sent["doc_type_hint"] == "receipt"
    (w0, h0), (w1, h1) = pdf_page_sizes(sent["data"])
    assert (w0 > h0, w1 > h1) == (True, True)
    session = client.get(f"/api/scanner/sessions/{session_id}").json()
    assert (session["status"], session["document_id"], session["pages"]) == ("completed", item["document_id"], [])
    assert not (settings.captures_dir / session_id).exists()


def test_assemble_rejects_pages_from_elsewhere(client: TestClient, fake_core: FakeCore) -> None:
    session_id = new_session(client)
    client.post(f"/api/scanner/sessions/{session_id}/scan", json={"dpi": 75})
    page = client.get(f"/api/scanner/sessions/{session_id}").json()["pages"][0]
    duplicate = client.post(f"/api/scanner/sessions/{session_id}/assemble",
                            json={"pages": [{"id": page["id"]}, {"id": page["id"]}]})
    foreign = client.post(f"/api/scanner/sessions/{session_id}/assemble", json={"pages": [{"id": 999}]})
    assert (duplicate.json()["code"], foreign.json()["code"]) == ("invalid_pages", "invalid_pages")
    assert fake_core.ingested == []


def test_apply_edit_rotates_clockwise_then_crops() -> None:
    image = Image.new("L", (200, 100), 255)
    image.putpixel((0, 0), 0)
    rotated = apply_edit(image, 90, None)
    assert (rotated.size, rotated.getpixel((99, 0))) == ((100, 200), 0)
    assert apply_edit(image, 0, (0.5, 0.0, 1.0, 0.5)).size == (100, 50)
    with pytest.raises(Problem):
        apply_edit(image, 0, (0.5, 0.0, 0.52, 1.0))


def test_images_to_pdf_keeps_page_count_at_letter_size() -> None:
    pages = [Image.new("RGB", (850, 1100), "white"), Image.new("L", (1100, 850), 255)]
    assert pdf_page_sizes(images_to_pdf(pages)) == [(612, 792), (792, 612)]


def test_capture_filename_strips_unsafe_characters() -> None:
    now = datetime(2026, 10, 10, 18, 5, tzinfo=UTC)
    assert capture_filename("../../etc/passwd", "Scan", now).startswith("....etcpasswd 2026-10-")
    assert capture_filename(None, "Scan", now).startswith("Scan 2026-10-")


class FakeScanimage:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        self.calls.append(argv)
        if argv[1:] == ["-L"]:
            out = b"device `epson2:libusb:001:004' is a Epson DS-530 sheetfed scanner\n"
            return subprocess.CompletedProcess(argv, 0, out, b"")
        if "-A" in argv:
            return subprocess.CompletedProcess(argv, 0, b"--source Flatbed|ADF|ADF Duplex", b"")
        buf = io.BytesIO()
        Image.new("L", (850, 1100), 255).save(buf, "PNG")
        return subprocess.CompletedProcess(argv, 0, buf.getvalue(), b"")


@pytest.fixture
def scanimage(monkeypatch: pytest.MonkeyPatch) -> FakeScanimage:
    fake = FakeScanimage()
    monkeypatch.setattr(scanner_module.shutil, "which", lambda name: "/usr/bin/scanimage")
    monkeypatch.setattr(scanner_module.subprocess, "run", fake)
    return fake


def test_sane_devices_are_parsed_from_scanimage(scanimage: FakeScanimage, settings: Settings) -> None:
    devices = SaneScanner(settings).devices()
    assert [(d.id, d.name, d.backend, d.duplex) for d in devices] == [
        ("epson2:libusb:001:004", "Epson DS-530 sheetfed scanner", "sane", True)
    ]


def test_sane_scan_of_unlisted_device_never_runs_a_scan(scanimage: FakeScanimage, settings: Settings) -> None:
    with pytest.raises(Problem) as err:
        SaneScanner(settings).scan("net:10.0.0.9:evil --batch=/etc/x", ScanRequest())
    assert err.value.status == 404
    assert scanimage.calls == [["scanimage", "-L"]]


def test_sane_listing_is_reused_for_thirty_seconds(
    scanimage: FakeScanimage, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = [1000.0]
    monkeypatch.setattr(scanner_module, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    sane = SaneScanner(settings)
    with make_ctx(settings).db() as conn:
        scanner_module.create_session(conn, sane, "epson2:libusb:001:004", now=db.now())
    sane.scan("epson2:libusb:001:004", ScanRequest())
    clock[0] += 31
    sane.scan("epson2:libusb:001:004", ScanRequest())
    assert [argv[1] for argv in scanimage.calls] == ["-L", "-d", "-L", "-d"]


def test_sane_scan_passes_an_argv_list(scanimage: FakeScanimage, settings: Settings) -> None:
    images = SaneScanner(settings).scan("epson2:libusb:001:004", ScanRequest(dpi=300, color="gray"))
    assert [img.size for img in images] == [(850, 1100)]
    assert scanimage.calls[-1] == [
        "scanimage", "-d", "epson2:libusb:001:004", "--format=png", "--resolution", "300", "--mode", "Gray",
    ]
