from __future__ import annotations

from core.conftest import check_pdf, contract_pdf, doc, field, invoice_pdf, issue_codes, run
from vaultai import db
from vaultai.pipeline.process import revalidate


def _issues(ctx, doc_id: str) -> list[tuple[str, str | None, str]]:
    with ctx.db() as conn:
        return [(r["code"], r["field_key"], r["severity"]) for r in conn.execute(
            "SELECT * FROM issues WHERE doc_id = ? AND severity != 'info' ORDER BY id", (doc_id,))]


def test_clean_check_is_ready(mock_ctx):
    doc_id = run(mock_ctx, "check.pdf", check_pdf())
    assert (doc(mock_ctx, doc_id)["status"], _issues(mock_ctx, doc_id)) == ("ready", [])
    assert field(mock_ctx, doc_id, "signature_present")["ai_value"] == "true"


def test_check_written_amount_must_match_the_numeric_amount(mock_ctx):
    doc_id = run(mock_ctx, "check.pdf", check_pdf(amount="$1,520.00"))
    assert _issues(mock_ctx, doc_id) == [("written_amount_mismatch", "amount_written", "error")]


def test_check_number_must_match_the_micr_line(mock_ctx):
    doc_id = run(mock_ctx, "check.pdf", check_pdf(number="1043", micr="A021000021A 000123456789C 001044"))
    assert _issues(mock_ctx, doc_id) == [("conflicting_values", "check_number", "error")]


def test_routing_number_must_pass_the_aba_checksum(mock_ctx):
    doc_id = run(mock_ctx, "check.pdf", check_pdf(micr="A123456789A 000123456789C 001043"))
    assert _issues(mock_ctx, doc_id) == [("invalid_identifier", "routing_number", "error")]


def test_unsigned_check_is_flagged(mock_ctx):
    doc_id = run(mock_ctx, "check.pdf", check_pdf(signed=False))
    assert _issues(mock_ctx, doc_id) == [("missing_signature", "signature_present", "warning")]


def test_impossible_calendar_date_is_reported_not_guessed(mock_ctx):
    doc_id = run(mock_ctx, "inv.pdf", invoice_pdf(invoice_date="02/30/2026"))
    f = field(mock_ctx, doc_id, "invoice_date")
    assert (f["ai_value"], f["raw_text"]) == (None, "02/30/2026")
    assert _issues(mock_ctx, doc_id) == [("invalid_date", "invoice_date", "error")]


def test_due_date_before_invoice_date(mock_ctx):
    doc_id = run(mock_ctx, "inv.pdf", invoice_pdf(due="September 1, 2026"))
    assert _issues(mock_ctx, doc_id) == [("date_order", "due_date", "error")]


def test_line_items_must_add_up_to_the_subtotal(mock_ctx):
    doc_id = run(mock_ctx, "inv.pdf", invoice_pdf(
        items=(("Widget", "2", "10.00", "20.00"), ("Bolt", "1", "5.00", "5.00")), subtotal="$20.00", total="$20.00"))
    assert _issues(mock_ctx, doc_id) == [("amount_mismatch", "subtotal", "error")]


def test_total_must_equal_subtotal_plus_tax(mock_ctx):
    doc_id = run(mock_ctx, "inv.pdf", invoice_pdf(tax="$2.00", total="$25.00"))
    assert _issues(mock_ctx, doc_id) == [("amount_mismatch", "total", "error")]


def test_a_tax_line_that_was_not_read_is_unknown_not_zero(mock_ctx):
    doc_id = run(mock_ctx, "inv.pdf", invoice_pdf(tax="$2.00", tax_label="Levy:", total="$22.00"))
    assert (field(mock_ctx, doc_id, "tax")["ai_value"], _issues(mock_ctx, doc_id)) == (None, [])


def test_missing_required_field(mock_ctx):
    doc_id = run(mock_ctx, "inv.pdf", invoice_pdf(number_label="Reference Note:"))
    assert _issues(mock_ctx, doc_id) == [("missing_required", "invoice_number", "error")]
    assert doc(mock_ctx, doc_id)["status"] == "needs_review"


def test_duplicate_invoice_number_from_the_same_vendor(mock_ctx):
    first = run(mock_ctx, "a.pdf", invoice_pdf(number="ZT-500"))
    second = run(mock_ctx, "b.pdf", invoice_pdf(number="ZT-500", items=(("Widget", "3", "10.00", "30.00"),),
                                                subtotal="$30.00", total="$30.00"))
    other_vendor = run(mock_ctx, "c.pdf", invoice_pdf(vendor="Other Supply Co", number="ZT-500"))
    assert (_issues(mock_ctx, first), _issues(mock_ctx, second), _issues(mock_ctx, other_vendor)) == (
        [], [("duplicate_document", "invoice_number", "warning")], [])


def test_contract_missing_a_signature(mock_ctx):
    doc_id = run(mock_ctx, "msa.pdf", contract_pdf(signed=(True, False)))
    assert field(mock_ctx, doc_id, "signature_status")["ai_value"] == "partially_signed"
    assert ("missing_signature", "signature_status", "warning") in _issues(mock_ctx, doc_id)
    with mock_ctx.db() as conn:
        sig = conn.execute("SELECT party FROM obligations WHERE doc_id = ? AND kind = 'signature'", (doc_id,)).fetchall()
    assert [r["party"] for r in sig] == ["Harborview Logistics LLC"]


def test_fully_signed_contract_has_only_the_legal_disclaimer(mock_ctx):
    doc_id = run(mock_ctx, "msa.pdf", contract_pdf(signed=(True, True)))
    assert (_issues(mock_ctx, doc_id), issue_codes(mock_ctx, doc_id), doc(mock_ctx, doc_id)["status"]) == (
        [], {"legal_review_required"}, "ready")


def test_contract_ending_before_it_starts(mock_ctx):
    doc_id = run(mock_ctx, "msa.pdf", contract_pdf(until="December 31, 2025", signed=(True, True)))
    assert _issues(mock_ctx, doc_id) == [("date_order", "expiration_date", "error")]


def test_ambiguous_clause(mock_ctx):
    doc_id = run(mock_ctx, "msa.pdf", contract_pdf(
        services="Provider shall promptly perform the services listed in Exhibit A.", signed=(True, True)))
    with mock_ctx.db() as conn:
        row = conn.execute("SELECT message, evidence_text FROM issues WHERE doc_id = ? AND code = 'ambiguous_clause'",
                           (doc_id,)).fetchone()
    assert row["evidence_text"] == "Provider shall promptly perform the services listed in Exhibit A."
    assert '"promptly"' in row["message"]


def test_human_correction_clears_the_issue_on_revalidate(mock_ctx):
    doc_id = run(mock_ctx, "inv.pdf", invoice_pdf(invoice_date="02/30/2026"))
    with mock_ctx.db() as conn, db.transaction(conn):
        conn.execute("UPDATE fields SET human_value = '2026-02-28', edited_by = 'alex', edited_at = ? "
                     "WHERE doc_id = ? AND key = 'invoice_date'", (db.iso(), doc_id))
        revalidate(mock_ctx, conn, doc_id)
    row = doc(mock_ctx, doc_id)
    assert (_issues(mock_ctx, doc_id), row["doc_date"], row["status"]) == ([], "2026-02-28", "needs_review")
