from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.surfaces.conftest import BASE_URL, make_client
from vaultai.config import Settings
from vaultai.security.http import DESKTOP_CSP


def test_foreign_host_header_is_refused_with_421(client: TestClient) -> None:
    response = client.get("/api/dashboard", headers={"Host": "evil.com"})
    assert response.status_code == 421
    assert response.json()["code"] == "misdirected_request"


def test_dev_server_host_is_refused_because_the_proxy_rewrites_host(client: TestClient) -> None:
    assert client.get("/api/dashboard", headers={"Host": "localhost:5173"}).status_code == 421
    assert client.get("/api/dashboard", headers={"Host": "127.0.0.1:8765"}).status_code == 200


def test_state_changing_request_without_header_is_refused(settings: Settings) -> None:
    bare = TestClient(make_client(settings).app, base_url=BASE_URL)
    response = bare.post("/api/exports/preview", json={})
    assert response.status_code == 403
    assert response.json() == {"code": "csrf", "message": "State-changing requests must carry the X-VaultAI: 1 header."}
    assert bare.post("/api/exports/preview", json={}, headers={"X-VaultAI": "1"}).status_code == 200


def test_api_responses_carry_security_headers(client: TestClient) -> None:
    headers = client.get("/api/dashboard").headers
    assert headers["content-security-policy"] == (
        "default-src 'self'; img-src 'self' blob: data:; style-src 'self' 'unsafe-inline'; "
        "frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    )
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["referrer-policy"] == "no-referrer"
    assert headers["cache-control"] == "no-store"
    assert "access-control-allow-origin" not in headers


def test_crash_returns_bare_api_error_without_internals(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def explode(*args: object, **kwargs: object) -> None:
        raise RuntimeError("/Users/someone/vaultai/data/raw/secret.pdf: invoice total 5328.00")

    monkeypatch.setattr("vaultai.api.actions.load_action_center", explode)
    response = client.get("/api/actions")
    assert response.status_code == 500
    assert response.json() == {"code": "internal", "message": "Something went wrong. Details are in the local server log."}
    assert response.headers["content-security-policy"] == DESKTOP_CSP


def test_validation_error_names_the_field_but_never_echoes_input(client: TestClient) -> None:
    response = client.post("/api/review/batch", json={"doc_ids": ["a"], "action": "=cmd|' /C calc'!A0"})
    assert response.status_code == 422
    assert response.json() == {"code": "invalid_request", "message": "Invalid or missing value: action."}


def test_unknown_api_path_is_json_404(client: TestClient) -> None:
    response = client.get("/api/nope")
    assert response.status_code == 404
    assert response.json() == {"code": "not_found", "message": "Not found."}


def test_frontend_fallback_serves_index_and_never_data_files(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>VaultAI UI</title>")
    (dist / "assets" / "app.js").write_text("console.log('ui')")
    settings = Settings(data_dir=tmp_path / "data", frontend_dist=dist, egress_guard=False, scanner_backend="none")
    client = make_client(settings)
    (tmp_path / "data" / "secret.txt").write_text("private")

    assert client.get("/review/abc").text == "<!doctype html><title>VaultAI UI</title>"
    assert client.get("/assets/app.js").text == "console.log('ui')"
    assert client.get("/..%2f..%2fdata%2fsecret.txt").text == "<!doctype html><title>VaultAI UI</title>"
    assert client.get("/api/unknown").json() == {"code": "not_found", "message": "Not found."}
