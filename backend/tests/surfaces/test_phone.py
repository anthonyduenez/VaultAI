from __future__ import annotations

import hashlib
import io
import ipaddress
import json
import re
from datetime import datetime, timedelta
from pathlib import Path

import pypdfium2 as pdfium
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from tests.surfaces.conftest import FakeCore, make_client
from vaultai import db
from vaultai.capture import phone
from vaultai.config import Settings
from vaultai.context import AppContext
from vaultai.phone_app import create_phone_app


@pytest.fixture
def phone_settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path / "data", ocr_engine="none", scanner_backend="none", egress_guard=False,
                    phone_enabled=True, phone_host="192.168.1.10", phone_max_pages=2)


@pytest.fixture
def desktop(phone_settings: Settings) -> TestClient:
    return make_client(phone_settings)


@pytest.fixture
def phone_ctx(desktop: TestClient) -> AppContext:
    return desktop.app.state.ctx


def handset(ctx: AppContext, ip: str = "192.168.1.20") -> TestClient:
    return TestClient(create_phone_app(ctx), base_url="http://192.168.1.10:8766", client=(ip, 50123))


def pair(desktop: TestClient) -> tuple[str, str, dict]:
    body = desktop.post("/api/phone/sessions").json()
    session_id, token = re.fullmatch(r"http://192\.168\.1\.10:8766/m/([\w-]+)#([\w-]+)", body["url"]).groups()
    return session_id, token, body


def jpeg(size: tuple[int, int], orientation: int | None = None) -> bytes:
    exif = Image.Exif()
    if orientation:
        exif[0x0112] = orientation
    exif[0x010F] = "PhoneMaker"
    buf = io.BytesIO()
    Image.new("RGB", size, "white").save(buf, "JPEG", exif=exif.tobytes())
    return buf.getvalue()


def submit(client: TestClient, session_id: str, token: str, pages: list[bytes], edits: object) -> dict:
    files = [("pages", (f"p{i}.jpg", data, "image/jpeg")) for i, data in enumerate(pages)]
    response = client.post(f"/p/{session_id}/submit", headers={"X-Pairing-Token": token},
                           data={"edits": json.dumps(edits)}, files=files)
    return {"status": response.status_code, **response.json()}


def test_pairing_is_refused_when_phone_capture_is_off(client: TestClient) -> None:
    response = client.post("/api/phone/sessions")
    assert response.status_code == 409
    assert response.json()["code"] == "phone_disabled"


def test_pairing_returns_lan_url_code_and_inline_qr(desktop: TestClient, phone_ctx: AppContext) -> None:
    session_id, token, body = pair(desktop)
    assert re.fullmatch(r"\d{6}", body["code"])
    assert len(token) >= 43
    assert (body["session"]["kind"], body["session"]["status"]) == ("phone", "active")
    assert body["qr_svg"].startswith("<svg")
    for forbidden in ("<image", "href", "<script", "url(", "<?xml"):
        assert forbidden not in body["qr_svg"]
    with phone_ctx.db() as conn:
        stored = conn.execute("SELECT token_hash FROM capture_sessions WHERE id = ?", (session_id,)).fetchone()[0]
    assert stored == hashlib.sha256(token.encode()).hexdigest()


def test_status_needs_the_right_token_from_the_local_network(desktop: TestClient, phone_ctx: AppContext) -> None:
    session_id, token, body = pair(desktop)
    lan = handset(phone_ctx)
    ok = lan.get(f"/p/{session_id}/status", headers={"X-Pairing-Token": token})
    payload = ok.json()
    assert datetime.fromisoformat(payload.pop("expires_at")) == datetime.fromisoformat(body["session"]["expires_at"])
    assert payload == {"status": "active", "code": body["code"], "max_pages": 2}
    assert lan.get(f"/p/{session_id}/status", headers={"X-Pairing-Token": token[:-1] + "x"}).status_code == 404
    assert lan.get(f"/p/{session_id}/status").status_code == 404
    assert lan.get("/p/unknown/status", headers={"X-Pairing-Token": token}).status_code == 404

    public = handset(phone_ctx, ip="8.8.8.8").get(f"/p/{session_id}/status", headers={"X-Pairing-Token": token})
    assert public.status_code == 403
    assert public.json()["code"] == "forbidden"


def test_expired_and_cancelled_sessions_are_404(desktop: TestClient, phone_ctx: AppContext) -> None:
    with phone_ctx.db() as conn:
        stale = phone.create_pairing(phone_ctx, conn, now=db.now() - timedelta(seconds=601))
    expired_id, expired_token = re.search(r"/m/([\w-]+)#([\w-]+)", stale.url).groups()  # type: ignore[union-attr]
    lan = handset(phone_ctx)
    assert lan.get(f"/p/{expired_id}/status", headers={"X-Pairing-Token": expired_token}).status_code == 404
    assert lan.get(f"/m/{expired_id}").status_code == 404
    assert submit(lan, expired_id, expired_token, [jpeg((10, 10))], [{}])["status"] == 404
    assert desktop.get(f"/api/phone/sessions/{expired_id}").json()["status"] == "expired"

    session_id, token, _ = pair(desktop)
    assert desktop.delete(f"/api/phone/sessions/{session_id}").status_code == 204
    assert lan.get(f"/p/{session_id}/status", headers={"X-Pairing-Token": token}).status_code == 404
    assert desktop.get(f"/api/phone/sessions/{session_id}").json()["status"] == "cancelled"


def test_submit_rejects_bad_payloads(desktop: TestClient, phone_ctx: AppContext, fake_core: FakeCore) -> None:
    session_id, token, _ = pair(desktop)
    lan = handset(phone_ctx)
    page = jpeg((40, 60))
    too_many = submit(lan, session_id, token, [page, page, page], [{}, {}, {}])
    not_image = submit(lan, session_id, token, [b"%PDF-1.7 pretending"], [{}])
    mismatch = submit(lan, session_id, token, [page, page], [{}])
    bad_crop = submit(lan, session_id, token, [page], [{"crop": [0.9, 0, 0.1, 1]}])
    assert [(r["status"], r["code"]) for r in (too_many, not_image, mismatch, bad_crop)] == [
        (422, "too_many_pages"), (422, "invalid_image"), (422, "invalid_edits"), (422, "invalid_crop"),
    ]
    assert fake_core.ingested == []
    assert desktop.get(f"/api/phone/sessions/{session_id}").json()["status"] == "active"


def test_submit_assembles_once_and_strips_metadata(desktop: TestClient, phone_ctx: AppContext,
                                                   fake_core: FakeCore) -> None:
    session_id, token, _ = pair(desktop)
    lan = handset(phone_ctx)
    sideways = jpeg((300, 100), orientation=6)
    landscape = jpeg((300, 100))

    result = submit(lan, session_id, token, [sideways, landscape], [{"rotation": 0, "crop": None}, {"rotation": 90}])

    assert (result["status"], result["source"], result["preflight"]) == (200, "phone", "accepted")
    data = fake_core.ingested[0]["data"]
    pdf = pdfium.PdfDocument(data)
    sizes = [pdf[i].get_size() for i in range(len(pdf))]
    pdf.close()
    assert [w < h for w, h in sizes] == [True, True]
    assert b"PhoneMaker" not in data and b"Exif" not in data
    session = desktop.get(f"/api/phone/sessions/{session_id}").json()
    assert (session["status"], session["document_id"]) == ("completed", result["document_id"])
    assert submit(lan, session_id, token, [landscape], [{}])["status"] == 404
    assert len(fake_core.ingested) == 1


def test_mobile_page_is_self_contained_with_a_per_response_nonce(desktop: TestClient, phone_ctx: AppContext) -> None:
    session_id, _, _ = pair(desktop)
    response = handset(phone_ctx).get(f"/m/{session_id}")
    nonce = re.search(r"script-src 'nonce-([\w-]+)'", response.headers["content-security-policy"]).group(1)  # type: ignore[union-attr]
    html = response.text
    assert response.status_code == 200
    assert html.count(f'nonce="{nonce}"') == 2
    assert "{{NONCE}}" not in html
    assert not re.search(r"""(src|href)\s*=\s*["']?(https?:)?//""", html)
    assert "<link" not in html
    for text in ("Stays on your local network", 'capture="environment"', "X-Pairing-Token", "history.replaceState"):
        assert text in html
    assert handset(phone_ctx).get(f"/m/{session_id}").headers["content-security-policy"] != response.headers[
        "content-security-policy"
    ]


def test_lan_ip_prefers_configured_host_and_otherwise_returns_an_address(phone_settings: Settings) -> None:
    assert phone.lan_ip(phone_settings) == "192.168.1.10"
    detected = phone.lan_ip(Settings(data_dir=phone_settings.data_dir))
    assert ipaddress.ip_address(detected).version == 4
