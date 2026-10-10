from __future__ import annotations

from fastapi.testclient import TestClient

from tests.surfaces.conftest import field_row, insert_document
from vaultai.context import AppContext


def test_detail_maps_every_record_kind(client: TestClient, app_ctx: AppContext) -> None:
    doc_id = insert_document(
        app_ctx, filename="msa.pdf", doc_type="contract", text="Master services agreement",
        fields=[field_row("parties", "BrightPath LLC; Harbor Co", page=2, line_id="p2.l4")],
        clauses=[{"kind": "renewal", "heading": "Renewal", "text": "Renews yearly.", "risk": "attention"}],
        line_items=[{"description": "Onboarding", "quantity": "2", "unit_price": "150.00", "amount": "300.00"}],
        issues=[("auto_renewal", "warning", "Renews automatically unless notice is given.")],
    )
    with app_ctx.db() as conn:
        conn.execute(
            "UPDATE documents SET summary_json = ? WHERE id = ?",
            ('{"claims": [{"text": "Two-party services agreement.", "evidence": [{"page": 1, "line_id": "p1.l1"}]}],'
             ' "generator": "template", "generated_at": "2026-10-01T12:00:00+00:00"}', doc_id),
        )
    body = client.get(f"/api/documents/{doc_id}").json()

    field = body["fields"][0]
    assert (field["value"], field["origin"], field["evidence"]) == (
        "BrightPath LLC; Harbor Co", "rules",
        {"page": 2, "bbox": [0.1, 0.1, 0.4, 0.12], "line_id": "p2.l4", "text": "BrightPath LLC; Harbor Co"},
    )
    assert [(c["kind"], c["risk"]) for c in body["clauses"]] == [("renewal", "attention")]
    assert [(li["quantity"], li["amount"]) for li in body["line_items"]] == [("2", "300.00")]
    assert (body["issue_count"], body["error_count"], body["risk_level"]) == (1, 0, "none")
    assert body["summary"]["claims"][0]["evidence"] == [{"page": 1, "bbox": None, "line_id": "p1.l1", "text": None}]
    assert [(p["page_no"], p["has_image"], p["lines"][0]["id"]) for p in body["pages"]] == [(1, True, "p1.l1")]
    assert (body["provenance"]["original_filename"], body["provenance"]["route"]) == ("msa.pdf", "fast")


def test_page_image_and_original_download(client: TestClient, app_ctx: AppContext) -> None:
    doc_id = insert_document(app_ctx, filename="Invoice 42.pdf", raw=b"%PDF-1.7 original bytes")
    image = client.get(f"/api/documents/{doc_id}/pages/1/image")
    assert (image.status_code, image.headers["content-type"], image.content[:4]) == (200, "image/png", b"\x89PNG")
    assert client.get(f"/api/documents/{doc_id}/pages/2/image").status_code == 404

    original = client.get(f"/api/documents/{doc_id}/original")
    assert original.content == b"%PDF-1.7 original bytes"
    assert original.headers["content-disposition"].startswith("attachment;")
    assert original.headers["x-content-type-options"] == "nosniff"
    assert client.get("/api/documents/nope/original").status_code == 404
