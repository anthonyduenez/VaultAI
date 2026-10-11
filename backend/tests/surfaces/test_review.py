from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from tests.surfaces.conftest import FakeCore, audit_rows, field_row, insert_document
from vaultai.context import AppContext
from vaultai.domain import ValueType
from vaultai.repo import Problem
from vaultai.review import normalize_value

NOW = datetime(2026, 10, 10, 9, 30, tzinfo=UTC)


def review(client: TestClient, doc_id: str, action: str, note: str | None = None):
    return client.post(f"/api/documents/{doc_id}/review", json={"action": action, "note": note})


def test_approve_records_human_approval_and_history(client: TestClient, app_ctx: AppContext) -> None:
    doc_id = insert_document(app_ctx, status="needs_review")
    response = review(client, doc_id, "approve", "Checked against the PO")
    body = response.json()
    assert response.status_code == 200
    assert (body["status"], body["approval_kind"], body["approved_by"]) == ("approved", "human", "local-user")
    assert [(h["action"], h["before"], h["after"], h["note"]) for h in body["history"]] == [
        ("approve", "needs_review", "approved", "Checked against the PO")
    ]


@pytest.mark.parametrize(
    ("status", "action"),
    [("approved", "approve"), ("approved", "reject"), ("rejected", "approve"), ("approved", "defer"),
     ("imported", "reopen"), ("ready", "reopen"), ("processing", "approve")],
)
def test_illegal_transitions_are_409(client: TestClient, app_ctx: AppContext, status: str, action: str) -> None:
    doc_id = insert_document(app_ctx, status=status)
    response = review(client, doc_id, action)
    assert response.status_code == 409
    assert response.json() == {
        "code": "invalid_transition", "message": f"Not allowed while the document is {status.replace('_', ' ')}.",
    }
    assert client.get(f"/api/documents/{doc_id}").json()["status"] == status


def test_reprocess_is_409_while_processing(client: TestClient, app_ctx: AppContext) -> None:
    doc_id = insert_document(app_ctx, status="processing")
    response = client.post(f"/api/documents/{doc_id}/reprocess")
    assert (response.status_code, response.json()) == (
        409, {"code": "invalid_transition", "message": "Not allowed while the document is processing."},
    )


def test_defer_from_ready_moves_to_needs_review_and_marks_deferred(client: TestClient, app_ctx: AppContext) -> None:
    doc_id = insert_document(app_ctx, status="ready")
    body = review(client, doc_id, "defer", "waiting on vendor").json()
    assert (body["status"], body["deferred"]) == ("needs_review", True)
    body = review(client, doc_id, "approve").json()
    assert (body["status"], body["deferred"]) == ("approved", False)


def test_reopen_clears_approval(client: TestClient, app_ctx: AppContext) -> None:
    doc_id = insert_document(app_ctx, status="approved", approved_at=NOW, approved_by="sam", approval_kind="policy")
    body = review(client, doc_id, "reopen").json()
    assert (body["status"], body["approval_kind"], body["approved_by"], body["approved_at"]) == (
        "needs_review", None, None, None,
    )


def test_approving_with_unresolved_errors_requires_a_note(client: TestClient, app_ctx: AppContext) -> None:
    doc_id = insert_document(app_ctx, issues=[("amount_mismatch", "error", "Line items do not add up to the total.")])
    for note in (None, "   "):
        response = review(client, doc_id, "approve", note)
        assert response.status_code == 422
        assert response.json()["code"] == "note_required"
    assert review(client, doc_id, "approve", "Vendor confirmed by phone").json()["status"] == "approved"


def test_reject_clears_deferral_and_reopen_returns_it_to_review(client: TestClient, app_ctx: AppContext) -> None:
    doc_id = insert_document(app_ctx, status="ready")
    review(client, doc_id, "defer")
    body = review(client, doc_id, "reject", "Not ours").json()
    assert (body["status"], body["deferred"]) == ("rejected", False)
    body = review(client, doc_id, "reopen").json()
    assert [(h["action"], h["before"], h["after"]) for h in reversed(body["history"])] == [
        ("defer", "ready", "needs_review"), ("reject", "needs_review", "rejected"), ("reopen", "rejected", "needs_review"),
    ]


def test_human_edit_keeps_ai_value_and_audits_before_after(
    client: TestClient, app_ctx: AppContext, fake_core: FakeCore
) -> None:
    doc_id = insert_document(app_ctx, fields=[field_row("total", "5328.00", value_type="money")])
    response = client.patch(f"/api/documents/{doc_id}/fields/total", json={"value": "$5,382.00", "note": "typo"})
    field = response.json()["fields"][0]
    assert (field["value"], field["ai_value"], field["origin"], field["ai_origin"], field["edited_by"]) == (
        "5382.00", "5328.00", "human", "rules", "local-user",
    )
    assert audit_rows(app_ctx, doc_id) == [("edit_field", "total", "5328.00", "5382.00")]
    assert fake_core.revalidated == [doc_id]


def test_editing_an_approved_record_reopens_it(client: TestClient, app_ctx: AppContext, fake_core: FakeCore) -> None:
    doc_id = insert_document(
        app_ctx, status="exported", approved_at=NOW, approved_by="sam", approval_kind="human",
        fields=[field_row("vendor", "Acme Supply")],
    )
    body = client.patch(f"/api/documents/{doc_id}/fields/vendor", json={"value": "Acme Supply Co."}).json()
    assert (body["status"], body["approved_by"]) == ("needs_review", None)
    assert [a[0] for a in audit_rows(app_ctx, doc_id)] == ["edit_field", "reopen"]


@pytest.mark.parametrize(
    ("value_type", "value", "message"),
    [("money", "five dollars", "Enter an amount such as 1234.56."),
     ("date", "13/45/2026", "Enter a date as YYYY-MM-DD."),
     ("bool", "maybe", "Enter true or false.")],
)
def test_invalid_edits_are_422_with_fixed_message(
    client: TestClient, app_ctx: AppContext, fake_core: FakeCore, value_type: str, value: str, message: str
) -> None:
    doc_id = insert_document(app_ctx, fields=[field_row("x", "1", value_type=value_type)])
    response = client.patch(f"/api/documents/{doc_id}/fields/x", json={"value": value})
    assert response.status_code == 422
    assert response.json() == {"code": "invalid_value", "message": message}
    assert audit_rows(app_ctx, doc_id) == []


def test_enum_edit_must_be_an_allowed_option(client: TestClient, app_ctx: AppContext, fake_core: FakeCore) -> None:
    doc_id = insert_document(
        app_ctx, doc_type="contract", fields=[field_row("signature_status", "unknown", value_type="enum")]
    )
    bad = client.patch(f"/api/documents/{doc_id}/fields/signature_status", json={"value": "mostly"})
    assert bad.json() == {"code": "invalid_value", "message": "Choose one of the allowed values."}
    good = client.patch(f"/api/documents/{doc_id}/fields/signature_status", json={"value": "SIGNED"})
    assert good.json()["fields"][0]["value"] == "signed"


def test_unknown_field_is_404(client: TestClient, app_ctx: AppContext, fake_core: FakeCore) -> None:
    doc_id = insert_document(app_ctx)
    assert client.patch(f"/api/documents/{doc_id}/fields/nope", json={"value": "1"}).status_code == 404


def test_normalize_value_dates_money_and_clearing() -> None:
    assert normalize_value(ValueType.DATE, " 2026-03-04 ") == "2026-03-04"
    assert normalize_value(ValueType.MONEY, "US$ 1,200") == "1200.00"
    assert normalize_value(ValueType.MONEY, "-£7.5") == "-7.50"
    assert normalize_value(ValueType.IDENTIFIER, "  000123456789 ") == "000123456789"
    assert normalize_value(ValueType.MONEY, "") is None
    with pytest.raises(Problem):
        normalize_value(ValueType.MONEY, "12.3.4")


def test_batch_review_reports_each_document(client: TestClient, app_ctx: AppContext) -> None:
    ok = insert_document(app_ctx, status="needs_review")
    done = insert_document(app_ctx, status="approved", approved_at=NOW, approval_kind="human", approved_by="x")
    errors = insert_document(app_ctx, status="ready", issues=[("missing_required", "error", "Total is missing.")])
    response = client.post("/api/review/batch", json={"doc_ids": [ok, done, errors, "missing"], "action": "approve"})
    assert response.json()["results"] == [
        {"doc_id": ok, "ok": True, "status": "approved", "error": None},
        {"doc_id": done, "ok": False, "status": None, "error": "Not allowed while the document is approved."},
        {"doc_id": errors, "ok": False, "status": None,
         "error": "Add a note explaining why you are approving a document with unresolved errors."},
        {"doc_id": "missing", "ok": False, "status": None, "error": "Not found."},
    ]


def test_review_queue_orders_and_groups(client: TestClient, app_ctx: AppContext) -> None:
    from datetime import timedelta

    later = NOW + timedelta(hours=1)
    ready = insert_document(app_ctx, status="ready", counterparty="Acme Supply", created_at=NOW)
    nr_old = insert_document(app_ctx, status="needs_review", counterparty="acme supply ", created_at=NOW)
    nr_new = insert_document(app_ctx, status="needs_review", counterparty="Other", created_at=later)
    deferred = insert_document(app_ctx, status="ready", counterparty="Other", created_at=NOW)
    insert_document(app_ctx, status="approved")
    review(client, deferred, "defer")
    body = client.get("/api/review/queue").json()
    assert [i["id"] for i in body["items"]] == [nr_old, nr_new, ready, deferred]
    assert sorted((g["key"], sorted(g["doc_ids"])) for g in body["groups"]) == sorted([
        ("invoice:acme supply", sorted([nr_old, ready])),
        ("invoice:other", sorted([nr_new, deferred])),
    ])


def test_obligation_status_update_is_audited(client: TestClient, app_ctx: AppContext) -> None:
    doc_id = insert_document(app_ctx, obligations=[{"kind": "payment", "description": "Pay net 30", "due_date": "2026-11-01"}])
    ob_id = client.get(f"/api/documents/{doc_id}").json()["obligations"][0]["id"]
    body = client.patch(f"/api/obligations/{ob_id}", json={"status": "done"}).json()
    assert (body["status"], body["doc_filename"]) == ("done", "doc.pdf")
    assert audit_rows(app_ctx, doc_id) == [("obligation_update", f"obligation:{ob_id}", "open", "done")]
