from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from tests.surfaces.conftest import insert_document
from vaultai.actions import load_action_center
from vaultai.context import AppContext
from vaultai.dashboard import load_dashboard

TODAY = date(2026, 10, 10)
NOW = datetime(2026, 10, 10, 15, 0, tzinfo=UTC)


def test_action_center_maps_records_to_categories(ctx: AppContext) -> None:
    insert_document(
        ctx, filename="msa.pdf", doc_type="contract", status="approved", approval_kind="human", approved_by="sam",
        approved_at=datetime(2026, 9, 1, tzinfo=UTC),
        obligations=[
            {"kind": "notice", "description": "Send non-renewal notice to BrightPath", "due_date": "2026-11-01"},
            {"kind": "payment", "description": "Quarterly fee", "due_date": "2026-10-07"},
            {"kind": "deliverable", "description": "Annual report", "due_date": "2027-03-01"},
            {"kind": "renewal", "description": "Already handled", "due_date": "2026-10-20", "status": "done"},
        ],
        issues=[("auto_renewal", "warning", "Renews automatically.")],
    )
    insert_document(
        ctx, filename="evil.pdf", status="needs_review",
        issues=[("prompt_injection", "error", "Text tries to instruct the assistant."),
                ("missing_required", "warning", "Invoice number is missing."),
                ("missing_required", "warning", "Total is missing.")],
    )
    insert_document(ctx, filename="receipt.pdf", doc_type="receipt", status="approved", approval_kind="policy",
                    approved_at=datetime(2026, 10, 9, tzinfo=UTC))
    insert_document(ctx, filename="broken.pdf", status="failed", doc_type=None, error="The PDF could not be read.")
    insert_document(ctx, filename="fine.pdf", status="ready")
    insert_document(ctx, filename="no.pdf", status="rejected", issues=[("duplicate_document", "warning", "Dup.")])

    with ctx.db() as conn:
        center = load_action_center(conn, today=TODAY, now=NOW)

    assert [(i.category.value, i.severity.value, i.title, i.detail) for i in center.items] == [
        ("payment_due", "error", "Payment overdue by 3 days", "Quarterly fee"),
        ("injection", "error", "Suspicious instructions found: evil.pdf", "Text tries to instruct the assistant."),
        ("processing_failure", "error", "Processing failed: broken.pdf", "The PDF could not be read."),
        ("renewal", "warning", "Notice due in 22 days", "Send non-renewal notice to BrightPath"),
        ("missing_info", "warning", "Missing information: evil.pdf", "Invoice number is missing. Total is missing."),
        ("needs_review", "warning", "Needs review: evil.pdf", None),
        ("ready", "info", "Ready to approve: fine.pdf", None),
        ("recently_approved", "info", "Approved: receipt.pdf", "Auto-approved by policy"),
    ]
    assert {c.category.value: c.count for c in center.counts} == {
        "needs_review": 1, "ready": 1, "missing_info": 1, "renewal": 1, "payment_due": 1, "injection": 1,
        "processing_failure": 1, "recently_approved": 1,
    }


def test_dashboard_totals_per_currency_and_deadlines(ctx: AppContext) -> None:
    approved = {"status": "approved", "approval_kind": "human", "approved_by": "sam", "approved_at": NOW}
    insert_document(ctx, doc_type="invoice", total="1000.10", currency="USD", **approved)
    insert_document(ctx, doc_type="purchase_order", total="0.20", currency="USD", status="exported")
    insert_document(ctx, doc_type="receipt", total="15.50", currency="USD", **approved)
    insert_document(ctx, doc_type="check", total="250.00", currency="EUR", **approved)
    insert_document(ctx, doc_type="invoice", total="99.99", currency="USD", status="needs_review")
    insert_document(ctx, doc_type="invoice", total="5000.00", currency="USD", status="rejected")
    insert_document(ctx, doc_type="contract", total="12000.00", currency="USD", status="ready",
                    obligations=[{"kind": "renewal", "description": "Renews", "due_date": "2026-12-01"},
                                 {"kind": "payment", "description": "Fee", "due_date": "2026-10-20"}])

    with ctx.db() as conn:
        board = load_dashboard(conn, today=TODAY)

    assert [(t.currency, t.approved_payables, t.approved_receipts, t.approved_checks, t.pending_review)
            for t in board.financial_totals] == [
        ("EUR", Decimal("0"), Decimal("0"), Decimal("250.00"), Decimal("0")),
        ("USD", Decimal("1000.30"), Decimal("15.50"), Decimal("0"), Decimal("99.99")),
    ]
    assert (board.total_documents, board.approved, board.needs_review, board.ready, board.rejected) == (7, 4, 1, 1, 1)
    assert [o.description for o in board.upcoming_deadlines] == ["Fee"]
    assert [o.description for o in board.renewals] == ["Renews"]
