"""The evaluator's obligation scoring. Synthetic fixtures only; scoring stays strict on kind, due date, and party."""

from __future__ import annotations

import json

from core.test_obligations import DOC, PARTIES, PAY, TEXT, _ob, _task
from vaultai.devtools.evaluation import FoundObligation, match_obligations, obligation_report, same_party

EXPECTED = [
    {"kind": "payment", "due_date": None, "party": "Harborview Logistics LLC", "description": "Pay each invoice within 30 days"},
    {"kind": "payment", "due_date": None, "party": "Zephyr Tools LLC", "description": "Invoice monthly in advance"},
    {"kind": "deliverable", "due_date": "2026-11-20", "party": "Zephyr Tools LLC", "description": "Deliver the first shipment"},
]
INVOICE = "Supplier will invoice monthly in advance."
DELIVER = TEXT[6]


def _report(*obligations: dict, rule_keys: set[tuple[str, str]] = frozenset()):
    raw = {"claims": [], "obligations": list(obligations)}
    return obligation_report(EXPECTED, [(_task(), raw)], DOC, "MDY", set(rule_keys))


def test_party_names_match_when_the_document_defines_them_as_one_party():
    assert same_party("Buyer", "Harborview Logistics LLC", PARTIES)
    assert same_party("harborview logistics, llc", "Harborview Logistics LLC.")
    assert same_party("The Supplier", "Supplier")
    assert same_party(None, None)
    assert not same_party("Harborview", "Harborview Logistics LLC", PARTIES)
    assert not same_party("Supplier", "Harborview Logistics LLC", PARTIES)
    assert not same_party("Buyer", None, PARTIES)


def test_kind_due_date_and_party_must_all_match_one_expected_obligation():
    found = [FoundObligation("payment", None, "Buyer"), FoundObligation("obligation", None, "Buyer"),
             FoundObligation("deliverable", None, "Supplier"), FoundObligation("deliverable", "2026-11-21", "Supplier"),
             FoundObligation("deliverable", "2026-11-20", "Supplier")]
    assert match_obligations(EXPECTED, found, PARTIES) == [True, False, False, False, True]


def test_each_expected_obligation_is_matched_at_most_once():
    found = [FoundObligation("payment", None, "Buyer"), FoundObligation("payment", None, "Harborview Logistics LLC")]
    assert match_obligations(EXPECTED, found, PARTIES) == [True, False]


def test_a_paraphrased_description_supported_by_the_same_quote_is_still_correct():
    report = _report(_ob("p1.l5", PAY, kind="payment", party="Buyer",
                         description="Buyer pays every invoice it receives within thirty (30) days"))
    assert (report.grounded, report.correct) == (1, 1)


def test_a_relative_deadline_is_correct_without_a_due_date_and_wrong_with_a_computed_one():
    good = _report(_ob("p1.l5", PAY, kind="payment", party="Buyer", deadline_text="within thirty (30) days of receipt"))
    computed = _report(_ob("p1.l5", PAY, kind="payment", party="Buyer", deadline_text="within thirty (30) days of receipt",
                           due_date="2026-12-01"))
    assert (good.correct, computed.correct, dict(computed.rejected)) == (1, 0, {"deadline_not_grounded": 1})


def test_an_explicit_calendar_deadline_must_match_the_expected_due_date():
    dated = _report(_ob("p1.l7", DELIVER, kind="deliverable", party="Supplier",
                        deadline_text="no later than November 20, 2026", due_date="2026-11-20"))
    undated = _report(_ob("p1.l7", DELIVER, kind="deliverable", party="Supplier"))
    assert (dated.correct, undated.correct) == (1, 0)


def test_a_multiline_citation_is_scored_like_any_other():
    report = _report(_ob("p1.l5", INVOICE, kind="payment", party="Supplier", end_line_id="p1.l6"))
    assert (report.grounded, report.correct, report.rejected_lines) == (1, 1, [])


def test_obligations_the_rules_already_found_still_count_as_correct_model_answers():
    report = _report(_ob("p1.l5", PAY, kind="payment", party="Buyer"),
                     _ob("p1.l7", DELIVER, kind="deliverable", party="Supplier", due_date="2026-11-20",
                         deadline_text="no later than November 20, 2026"),
                     rule_keys={("payment", "p1.l5"), ("deliverable", "p1.l7")})
    assert (report.grounded, report.duplicates_of_rules, report.correct) == (2, 2, 2)


def test_several_obligations_in_one_document_are_counted_separately():
    report = _report(_ob("p1.l5", PAY, kind="payment", party="Buyer"),
                     _ob("p1.l5", INVOICE, kind="payment", party="Supplier", end_line_id="p1.l6"),
                     _ob("p1.l7", DELIVER, kind="deliverable", party="Supplier", due_date="2026-11-20",
                         deadline_text="no later than November 20, 2026"),
                     _ob("p1.l5", PAY, kind="payment", party="Buyer"),
                     _ob("p1.l8", "Supplier may use subcontractors.", party="Supplier"),
                     _ob("p1.l5", INVOICE, kind="payment", party="Supplier"))
    assert (report.expected, report.proposed, report.grounded, report.repeats, report.correct) == (3, 6, 3, 1, 3)
    assert dict(report.rejected) == {"not_an_obligation": 1, "quote_needs_end_line": 1}


def test_a_wrong_party_or_kind_is_not_correct_even_when_grounded():
    report = _report(_ob("p1.l5", PAY, kind="obligation", party="Buyer"),
                     _ob("p1.l11", TEXT[10], kind="payment", party="Acme Corp"))
    assert (report.grounded, report.correct) == (2, 0)


def test_the_report_holds_codes_counts_and_line_ids_but_no_text():
    report = _report(_ob("p1.l5", "Buyer must pay all invoices promptly.", kind="payment", party="Buyer",
                         description="Quokka private note"),
                     _ob("p9.l99", PAY, kind="payment"))
    text = json.dumps(report.to_json())
    assert report.to_json()["rejected_lines"] == [["p1.l5", "quote_mismatch"], ["p9.l99", "citation_line_not_shown"]]
    for leaked in ("Quokka", "Buyer", "invoice", "Harborview", "Zephyr", "pay"):
        assert leaked not in text


def test_a_reply_outside_the_schema_proposes_nothing():
    report = obligation_report(EXPECTED, [(_task(), {"obligations": "Buyer pays"})], DOC, "MDY", set())
    assert (report.proposed, report.grounded, report.correct) == (0, 0, 0)
