"""Evidence grounding, confidence calibration, and when the model is asked at all."""

from __future__ import annotations

from dataclasses import replace

import pytest

from conftest import make_ctx
from core.conftest import ScriptedModel, contract_pdf, doc, empty_answers, field, invoice_pdf, issue_codes, pdf, receipt_pdf, run
from vaultai.config import Settings
from vaultai.domain import DocType, FieldOrigin, ObligationKind
from vaultai.extraction import Found, evidence
from vaultai.model import confidence, grounding
from vaultai.model.base import AnalyzeTask, DocLine, ExtractTask
from vaultai.parsing.layout import Doc, Line, Page
from vaultai.pipeline import cache
from vaultai.schemas import SCHEMAS

INVOICE = SCHEMAS[DocType.INVOICE]


def _doc(*texts: str, method: str = "pdf_text", conf: float = 1.0) -> Doc:
    lines = tuple(Line(f"p1.l{i}", t, (0.1, 0.05 * i, 0.9, 0.05 * i + 0.01), conf) for i, t in enumerate(texts, start=1))
    return Doc((Page(1, 612, 792, method, lines),))


def _extract(doc: Doc, *answers: dict, keys: tuple[str, ...] = ("invoice_number", "total", "invoice_date"),
             existing: dict | None = None, shown: tuple[str, ...] | None = None):
    lines = tuple(DocLine(ln.id, ln.text) for ln in doc.lines if shown is None or ln.id in shown)
    task = ExtractTask(DocType.INVOICE, tuple(INVOICE.field(k) for k in keys), lines)
    return grounding.ground_extract({"fields": list(answers)}, task, doc, existing or {}, "MDY", "m")


def _answer(key: str, value: str, line_id: str, quote: str, conf: float = 0.9) -> dict:
    return {"key": key, "value": value, "line_id": line_id, "quote": quote, "confidence": conf}


DOC = _doc("Invoice Number: ZT-1001", "Invoice Date: September 28, 2026", "Total Due: $5,328.00", "Subtotal: $5,238.00")


def test_quotes_must_match_the_line_exactly_apart_from_spacing():
    kept, issues = _extract(DOC, _answer("invoice_number", "ZT-1001", "p1.l1", "Invoice Number:    ZT-1001"))
    assert [(f.key, f.value) for f in kept] == [("invoice_number", "ZT-1001")] and issues == []
    kept, issues = _extract(DOC, _answer("invoice_number", "ZT-1001", "p1.l1", "invoice number: zt-1001"))
    assert kept == [] and [i.code.value for i in issues] == ["ungrounded_value"]


def test_text_values_are_stored_as_printed_not_as_the_model_spelled_them():
    kept, _ = _extract(DOC, _answer("invoice_number", "zt-1001", "p1.l1", "Invoice Number: ZT-1001"))
    assert (kept[0].value, kept[0].raw_text) == ("ZT-1001", "ZT-1001")


def test_annotated_line_ids_from_the_prompt_are_accepted():
    kept, _ = _extract(DOC, _answer("invoice_number", "ZT-1001", "[p1.l1 ocr 60%]", "ZT-1001"))
    assert kept[0].evidence.line_id == "p1.l1"


@pytest.mark.parametrize("answer", [
    _answer("total", "5328", "p1.l4", "Subtotal: $5,238.00"),
    _answer("total", "EUR 5,328.00", "p1.l3", "Total Due: $5,328.00"),
    _answer("total", "5,328.00 or 5,238.00", "p1.l3", "Total Due: $5,328.00"),
    _answer("invoice_date", "2026-02-30", "p1.l2", "Invoice Date: September 28, 2026"),
    _answer("invoice_date", "2026-09-29", "p1.l2", "Invoice Date: September 28, 2026"),
    _answer("invoice_number", "ZT-1001", "p1.l2", "ZT-1001"),
    _answer("invoice_number", "ZT-1001", "p9.l1", "ZT-1001"),
    _answer("status", "approved", "p1.l1", "Invoice Number"),
])
def test_values_that_do_not_read_as_their_type_inside_the_quote_are_rejected(answer):
    kept, issues = _extract(DOC, answer)
    assert kept == [] and [i.code.value for i in issues] == ["ungrounded_value"]


def test_money_and_dates_are_reparsed_from_the_printed_text():
    kept, _ = _extract(DOC, _answer("total", "$5,328.00", "p1.l3", "Total Due: $5,328.00"),
                       _answer("invoice_date", "September 28, 2026", "p1.l2", "September 28, 2026"))
    assert {f.key: (f.value, f.raw_text, f.currency) for f in kept} == {
        "total": ("5328.00", "$5,328.00", "USD"), "invoice_date": ("2026-09-28", "September 28, 2026", None)}


def test_a_line_that_exists_but_was_not_shown_cannot_be_cited():
    kept, issues = _extract(DOC, _answer("total", "5328.00", "p1.l3", "$5,328.00"), shown=("p1.l1",))
    assert kept == [] and len(issues) == 1


def test_conflicting_answers_for_one_field_are_both_discarded():
    kept, issues = _extract(DOC, _answer("total", "5328.00", "p1.l3", "$5,328.00"),
                            _answer("total", "5238.00", "p1.l4", "$5,238.00"))
    assert kept == [] and "more than one conflicting answer" in issues[0].message


def test_confidence_comes_from_the_evidence_not_from_the_model():
    labelled, _ = _extract(DOC, _answer("total", "5328.00", "p1.l3", "$5,328.00", conf=1.0))
    unlabelled_doc = _doc("Balance Payable: $5,328.00")
    unlabelled, _ = _extract(unlabelled_doc, _answer("total", "5328.00", "p1.l1", "$5,328.00", conf=1.0))
    unsure, _ = _extract(DOC, _answer("total", "5328.00", "p1.l3", "$5,328.00", conf=0.2))
    scanned_doc = _doc("Total Due: $5,328.00", method="ocr", conf=0.7)
    scanned, _ = _extract(scanned_doc, _answer("total", "5328.00", "p1.l1", "$5,328.00"))
    assert [k[0].confidence for k in (labelled, unlabelled, unsure, scanned)] == [0.85, 0.75, 0.75, 0.595]


def test_a_model_value_that_disagrees_with_the_rules_is_downgraded_and_never_replaces_them():
    rules = Found("total", "5238.00", "$5,238.00", 0.7, evidence(DOC.by_id["p1.l4"]))
    kept, _ = _extract(DOC, _answer("total", "5328.00", "p1.l3", "$5,328.00"), existing={"total": rules})
    assert kept == []
    weak_rules = replace(rules, confidence=0.3)
    kept, _ = _extract(DOC, _answer("total", "5328.00", "p1.l3", "$5,328.00"), existing={"total": weak_rules})
    assert kept[0].confidence == 0.6


def test_weak_evidence_below_the_floor_is_discarded():
    scanned = _doc("Balance Payable: $5,328.00", method="ocr", conf=0.45)
    kept, issues = _extract(scanned, _answer("total", "5328.00", "p1.l1", "$5,328.00"))
    assert kept == [] and "weak evidence" in issues[0].message


def test_cross_field_checks_downgrade_only_model_values():
    def f(key: str, value: str, origin: FieldOrigin) -> Found:
        return Found(key, value, value, 0.85, None, origin=origin)

    fields = {"subtotal": f("subtotal", "100.00", FieldOrigin.RULES), "tax": f("tax", "8.00", FieldOrigin.RULES),
              "total": f("total", "180.00", FieldOrigin.MODEL),
              "invoice_date": f("invoice_date", "2026-05-01", FieldOrigin.RULES),
              "due_date": f("due_date", "2026-04-01", FieldOrigin.MODEL),
              "invoice_number": f("invoice_number", "PO-7", FieldOrigin.MODEL), "po_number": f("po_number", "PO-7", FieldOrigin.RULES)}
    assert confidence.problems(DocType.INVOICE, fields) == {
        "total": "amounts do not add up", "due_date": "dates are out of order", "invoice_number": "same identifier as another field"}
    checked = confidence.cross_check(DocType.INVOICE, fields)
    assert {k: v.confidence for k, v in checked.items()} == {
        "subtotal": 0.85, "tax": 0.85, "total": 0.5, "invoice_date": 0.85, "due_date": 0.5, "invoice_number": 0.5, "po_number": 0.85}


CONTRACT = _doc("Client shall pay each invoice within thirty (30) days of receipt.",
                "Provider shall deliver the report by November 20, 2026.")


def _analyze(raw: dict, facts: dict | None = None):
    task = AnalyzeTask(DocType.CONTRACT, tuple(DocLine(ln.id, ln.text) for ln in CONTRACT.lines), facts or {})
    return grounding.ground_analyze(raw, task, CONTRACT, "MDY")


def test_obligation_descriptions_keep_the_documents_wording():
    raw = {"claims": [], "obligations": [
        {"kind": "payment", "party": "Client", "description": "Client must pay invoices within 30 days of receipt.",
         "due_date": None, "line_id": "p1.l1", "quote": "Client shall pay each invoice within thirty (30) days of receipt."},
        {"kind": "deliverable", "party": "Vendor Corp", "description": "Something entirely different happens.",
         "due_date": "2026-11-20", "line_id": "p1.l2", "quote": "deliver the report by November 20, 2026"}]}
    _, obligations, issues = _analyze(raw)
    assert issues == []
    assert [(o.kind, o.party, o.description, str(o.due_date)) for o in obligations] == [
        (ObligationKind.PAYMENT, "Client", "Client must pay invoices within 30 days of receipt.", "None"),
        (ObligationKind.DELIVERABLE, None, "deliver the report by November 20, 2026", "2026-11-20")]


def test_obligation_deadlines_must_be_printed_in_the_quote():
    raw = {"claims": [], "obligations": [
        {"kind": "payment", "party": "Client", "description": "Pay within 30 days", "due_date": "2026-12-01",
         "line_id": "p1.l1", "quote": "Client shall pay each invoice within thirty (30) days of receipt."}]}
    _, obligations, issues = _analyze(raw)
    assert obligations == [] and [i.code.value for i in issues] == ["ungrounded_value"]


def test_claims_with_advice_or_unsupported_numbers_are_dropped():
    raw = {"claims": [
        {"text": "The report is due November 20, 2026.", "line_ids": ["p1.l2"]},
        {"text": "This payment clause is unenforceable.", "line_ids": ["p1.l1"]},
        {"text": "The client owes $9,999.00.", "line_ids": ["p1.l1"]},
        {"text": "The fee is USD 12,500.00.", "line_ids": ["p1.l1"]}], "obligations": []}
    claims, _, issues = _analyze(raw, {"Recurring fee [p1.l1]": "USD 12,500.00"})
    assert [c.text for c in claims] == ["The report is due November 20, 2026.", "The fee is USD 12,500.00."]
    assert len(issues) == 2


def _counting(settings: Settings, answer=empty_answers) -> tuple[object, ScriptedModel]:
    model = ScriptedModel(answer)
    return make_ctx(settings, model), model


def test_clean_financial_documents_make_no_model_calls_and_legal_documents_one(settings):
    ctx, model = _counting(settings)
    receipt = run(ctx, "receipt.pdf", receipt_pdf())
    invoice = run(ctx, "invoice.pdf", invoice_pdf(number="ZT-9"))
    assert model.tasks == []
    assert [(doc(ctx, d)["status"], doc(ctx, d)["route"]) for d in (receipt, invoice)] == [
        ("approved", "fast"), ("ready", "fast")]
    run(ctx, "contract.pdf", contract_pdf())
    assert [type(t).__name__ for t in model.tasks] == ["AnalyzeTask"]
    assert all(isinstance(ln, DocLine) for ln in model.tasks[0].lines)


def test_signature_status_is_never_asked_of_a_text_only_model(settings):
    ctx, model = _counting(settings)
    run(ctx, "notes.pdf", pdf(["SERVICES AGREEMENT", "This Agreement is governed by the laws of the State of Delaware."]),
        hint=DocType.CONTRACT)
    asked = {f.key for t in model.tasks if isinstance(t, ExtractTask) for f in t.fields}
    assert asked and "signature_status" not in asked


def test_currency_is_derived_once_the_model_supplies_an_amount(settings):
    def answer(task):
        if isinstance(task, ExtractTask):
            line = next(ln for ln in task.lines if "Balance Payable" in ln.text)
            return {"fields": [{"key": "total", "value": "$20.00", "line_id": line.id, "quote": "$20.00", "confidence": 0.9}]}
        return empty_answers(task)

    ctx, _ = _counting(settings, answer)
    doc_id = run(ctx, "invoice.pdf", invoice_pdf(total="", extra=("Balance Payable: $20.00",)))
    assert (field(ctx, doc_id, "total")["ai_value"], field(ctx, doc_id, "currency")["ai_value"]) == ("20.00", "USD")
    assert "missing_required" not in issue_codes(ctx, doc_id)


def test_model_cache_keys_cover_document_prompt_model_and_configuration(settings):
    args = ("ExtractTask", "sha-a", [{"role": "user", "content": "x"}], "v1", "nemotron", settings)
    base = cache.model_key(*args)
    assert base != cache.model_key("ExtractTask", "sha-b", *args[2:])
    assert base != cache.model_key(*args[:3], "v2", *args[4:])
    assert base != cache.model_key(*args[:4], "other-model", settings)
    assert base != cache.model_key(*args[:5], replace(settings, model_temperature=0.7))
    assert base == cache.model_key(*args)
