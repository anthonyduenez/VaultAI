from __future__ import annotations

from fastapi.testclient import TestClient

from tests.surfaces.conftest import field_row, insert_document
from vaultai.context import AppContext
from vaultai.search import fts_query


def ids(response) -> list[str]:
    assert response.status_code == 200, response.text
    return [item["id"] for item in response.json()["items"]]


def test_finds_a_document_by_a_word_only_in_its_text(client: TestClient, app_ctx: AppContext) -> None:
    hit = insert_document(app_ctx, filename="a.pdf", text="Forklift recertification is due before the audit.")
    insert_document(app_ctx, filename="b.pdf", text="Quarterly toner order.")
    assert ids(client.get("/api/documents", params={"q": "recertif"})) == [hit]


def test_finds_a_document_by_a_field_value(client: TestClient, app_ctx: AppContext) -> None:
    hit = insert_document(app_ctx, fields=[field_row("invoice_number", "INV-2026-0142", value_type="identifier")])
    insert_document(app_ctx, fields=[field_row("invoice_number", "INV-2026-0999", value_type="identifier")])
    assert ids(client.get("/api/documents", params={"q": "INV-2026-0142"})) == [hit]


def test_fts_syntax_in_the_query_is_inert(client: TestClient, app_ctx: AppContext) -> None:
    insert_document(app_ctx, text="nothing to see")
    response = client.get("/api/documents", params={"q": '") OR 1=1 --'})
    assert response.status_code == 200
    assert response.json()["total"] == 0
    assert fts_query('") OR 1=1 --') == '"OR"* "1"* "1"*'
    assert fts_query('NEAR(a b) "x"^') == '"NEAR"* "a"* "b"* "x"*'


def test_filters_sorting_and_facets(client: TestClient, app_ctx: AppContext) -> None:
    small = insert_document(app_ctx, doc_type="invoice", status="approved", total="90.00", doc_date="2026-02-01",
                            counterparty="Acme Supply", confidence=0.95)
    big = insert_document(app_ctx, doc_type="invoice", status="needs_review", total="1200.00", doc_date="2026-03-15",
                          counterparty="Pinecrest Foods", confidence=0.6)
    receipt = insert_document(app_ctx, doc_type="receipt", status="approved", total="15.50", doc_date="2026-03-20",
                              counterparty="Cornerstone Office Mart", confidence=0.99)

    by_total = client.get("/api/documents", params={"sort": "total_amount", "order": "asc"})
    assert ids(by_total) == [receipt, small, big]
    assert ids(client.get("/api/documents", params={"doc_type": "invoice", "sort": "doc_date", "order": "asc"})) == [
        small, big,
    ]
    assert ids(client.get("/api/documents", params={"date_from": "2026-03-01", "date_to": "2026-03-16"})) == [big]
    assert ids(client.get("/api/documents", params={"party": "pinecrest"})) == [big]
    assert sorted(ids(client.get("/api/documents", params={"min_confidence": 0.9}))) == sorted([small, receipt])

    body = client.get("/api/documents", params={"status": "approved"}).json()
    assert body["total"] == 2
    assert body["by_type"] == [{"doc_type": "invoice", "count": 1}, {"doc_type": "receipt", "count": 1}]
    assert body["by_status"] == [{"status": "approved", "count": 2}]


def test_sort_outside_the_whitelist_is_rejected(client: TestClient) -> None:
    response = client.get("/api/documents", params={"sort": "id; DROP TABLE documents"})
    assert response.status_code == 422
    assert client.get("/api/documents", params={"limit": 500}).status_code == 422
