"""Privacy-safe diagnostics: stable codes and counts for refused replies and discarded values, never content."""

from __future__ import annotations

import json
import logging
from dataclasses import replace

import httpx
import pytest

from conftest import make_ctx
from core.conftest import ScriptedModel, empty_answers, invoice_pdf, issue_codes, run
from core.test_grounding import CONTRACT, DOC, _analyze, _answer, _doc, _extract
from vaultai import feedback
from vaultai.domain import DocType, ModelKind
from vaultai.metrics import compute_metrics
from vaultai.model import diagnostics, grounding, openai_compat, output
from vaultai.model.base import ClassifyTask, DocLine, ExtractTask
from vaultai.model.diagnostics import FailureReason, GroundingReason
from vaultai.model.openai_compat import OpenAICompatModel
from vaultai.schemas import SCHEMAS

SECRET = "Quokka Ledger Holdings"
VENDOR = SCHEMAS[DocType.INVOICE].field("vendor")
TASK = ExtractTask(DocType.INVOICE, (VENDOR,), (DocLine("p1.l1", "Zephyr Tools LLC"),))
GOOD = json.dumps({"fields": [{"key": "vendor", "value": "Zephyr Tools LLC", "line_id": "p1.l1",
                               "quote": "Zephyr Tools LLC", "confidence": 0.9}]})
FAILURE_CODES = {"empty_content", "invalid_json", "schema_invalid", "response_too_large"}
GROUNDING_CODES = {"invalid_citation", "unsupported_by_document", "conflicting_evidence", "weak_evidence",
                   "obligation_not_grounded", "deadline_not_grounded", "unknown_field", "other"}


def _local(settings, **changes):
    return replace(settings, model_backend=ModelKind.OPENAI_COMPAT, model_endpoint="http://127.0.0.1:9/v1",
                   model_name="nemotron", **changes)


def _vllm(content: str | None, reasoning: str | None = None) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"index": 0, "finish_reason": "stop", "message": {
        "role": "assistant", "content": content, "reasoning": reasoning, "reasoning_content": reasoning}}]})


class Server:
    def __init__(self, *replies) -> None:
        self.replies = list(replies)
        self.bodies: list[dict] = []
        self.raw: list[bytes] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.raw.append(request.content)
        self.bodies.append(json.loads(request.content))
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        return reply if isinstance(reply, httpx.Response) else _vllm(reply)


def _model(settings, server: Server) -> OpenAICompatModel:
    return OpenAICompatModel(_local(settings), transport=httpx.MockTransport(server))


def test_the_codes_are_stable():
    assert {r.value for r in FailureReason} == FAILURE_CODES
    assert {r.value for r in GroundingReason} == GROUNDING_CODES


@pytest.mark.parametrize("replies, failures, requests, accepted", [
    ((_vllm(None),), ("empty_content", "empty_content"), 2, False),
    ((_vllm("   "),), ("empty_content", "empty_content"), 2, False),
    ((f"Sure, the vendor is {SECRET}.",), ("invalid_json", "invalid_json"), 2, False),
    (('{"fields": "not a list"}',), ("schema_invalid", "schema_invalid"), 2, False),
    (("not json", GOOD), ("invalid_json",), 2, True),
    ((_vllm(None), GOOD), ("empty_content",), 2, True),
    ((GOOD,), (), 1, True),
])
def test_each_refused_attempt_gets_one_stable_code_and_the_retry_policy_is_unchanged(settings, replies, failures,
                                                                                      requests, accepted):
    server = Server(*replies)
    reply = _model(settings, server).run(TASK)
    assert (reply.failures, len(server.bodies), reply.attempts) == (failures, requests, requests)
    assert (output.parse(TASK, reply.output) is not None) is accepted
    assert all(isinstance(code, FailureReason) for code in reply.failures)


def test_an_oversized_reply_is_response_too_large_and_not_retried(settings, monkeypatch):
    monkeypatch.setattr(openai_compat, "MAX_RESPONSE_BYTES", 10_000)
    server = Server(_vllm("x" * 50_000))
    reply = _model(settings, server).run(TASK)
    assert (reply.output, reply.failures, len(server.bodies)) == (None, ("response_too_large",), 1)


def test_reasoning_is_still_ignored_and_thinking_stays_off_on_every_attempt(settings):
    server = Server(_vllm(None, reasoning=GOOD))
    reply = _model(settings, server).run(TASK)
    assert (reply.output, reply.failures) == (None, ("empty_content", "empty_content"))
    for body, raw in zip(server.bodies, server.raw):
        assert body["chat_template_kwargs"] == {"enable_thinking": False}
        assert "extra_body" not in body and b"extra_body" not in raw


def _vendorless() -> bytes:
    return invoice_pdf(vendor="", extra=("Payable to Zephyr Tools LLC",))


def _stored(ctx, doc_id: str) -> tuple[str, dict]:
    with ctx.db() as conn:
        text = conn.execute("SELECT metrics_json FROM doc_metrics WHERE doc_id = ?", (doc_id,)).fetchone()[0]
    return text, json.loads(text)


def _leaves(value) -> list:
    if isinstance(value, dict):
        return [leaf for v in value.values() for leaf in _leaves(v)]
    if isinstance(value, list):
        return [leaf for v in value for leaf in _leaves(v)]
    return [value]


def test_document_metrics_hold_only_codes_and_counts(settings, caplog):
    caplog.set_level(logging.DEBUG, logger="vaultai")
    hostile = json.dumps({"fields": [], SECRET: "Payable to Zephyr Tools LLC"})
    server = Server(f"{SECRET} says the vendor is Zephyr", hostile)
    ctx = make_ctx(_local(settings), _model(settings, server))
    doc_id = run(ctx, "quokka.pdf", _vendorless())
    text, stored = _stored(ctx, doc_id)
    model = stored["model"]
    assert model["failure_reasons"] == {"empty_content": 0, "invalid_json": 1, "schema_invalid": 1, "response_too_large": 0}
    assert model["rejections_by_reason"] == {"empty_content": 0, "invalid_json": 0, "schema_invalid": 1,
                                             "response_too_large": 0}
    assert model["grounding_rejections_by_reason"] == dict.fromkeys(GROUNDING_CODES, 0)
    for key, codes in (("failure_reasons", FAILURE_CODES), ("rejections_by_reason", FAILURE_CODES),
                       ("grounding_rejections_by_reason", GROUNDING_CODES)):
        assert set(model[key]) == codes and all(type(n) is int for n in model[key].values())
    assert issue_codes(ctx, doc_id) == {"model_output_rejected", "missing_required"}
    for leaked in (SECRET, "Quokka", "Zephyr", "Harborview", "Payable", "quokka.pdf", "says the vendor"):
        assert leaked not in text and leaked not in caplog.text
    assert "reason=invalid_json" in caplog.text and "reason=schema_invalid" in caplog.text
    assert all(isinstance(leaf, (int, float, bool, str)) or leaf is None for leaf in _leaves(stored))

    m = compute_metrics(ctx)
    assert (m.model_failure_reasons["invalid_json"], m.model_failure_reasons["schema_invalid"]) == (1, 1)
    assert m.model_rejections_by_reason["schema_invalid"] == 1 and sum(m.grounding_rejections_by_reason.values()) == 0
    assert all(server.bodies[i]["chat_template_kwargs"] == {"enable_thinking": False} for i in range(len(server.bodies)))


def test_an_empty_reply_is_counted_as_empty_content_in_document_metrics(settings):
    ctx = make_ctx(_local(settings), _model(settings, Server(_vllm(None, reasoning=GOOD))))
    doc_id = run(ctx, "invoice.pdf", _vendorless())
    model = _stored(ctx, doc_id)[1]["model"]
    assert (model["failure_reasons"]["empty_content"], model["rejections_by_reason"]["empty_content"]) == (2, 1)


@pytest.mark.parametrize("answer, reason, message", [
    (_answer("status", "approved", "p1.l1", "Invoice Number"), "unknown_field",
     "The model proposed a value that is not supported by the document text; it was discarded."),
    (_answer("total", "5328.00", "p9.l99", "$5,328.00"), "invalid_citation",
     "The model proposed a value that is not supported by the document text; it was discarded."),
    (_answer("total", "5328.00", "not-a-line", "$5,328.00"), "invalid_citation",
     "The model proposed a value that is not supported by the document text; it was discarded."),
    (_answer("total", "5328.00", "p1.l3", "Total payable $5,328.00"), "unsupported_by_document",
     "The model proposed a value that is not supported by the document text; it was discarded."),
    (_answer("total", "9999.00", "p1.l3", "Total Due: $5,328.00"), "unsupported_by_document",
     "The model proposed a total that is not supported by the document text; it was discarded."),
])
def test_each_discarded_value_names_a_stable_reason_and_keeps_its_message(answer, reason, message):
    kept, (issue,) = _extract(DOC, answer)
    assert (kept, issue.code.value, issue.reason, issue.message) == ([], "ungrounded_value", reason, message)


def test_conflicting_and_weak_evidence_have_their_own_codes():
    _, (conflict,) = _extract(DOC, _answer("total", "5328.00", "p1.l3", "$5,328.00"),
                              _answer("total", "5238.00", "p1.l4", "$5,238.00"))
    _, (weak,) = _extract(_doc("Balance Payable: $5,328.00", method="ocr", conf=0.45),
                          _answer("total", "5328.00", "p1.l1", "$5,328.00"))
    assert (conflict.reason, weak.reason) == ("conflicting_evidence", "weak_evidence")
    assert conflict.message == "The model proposed a total that had more than one conflicting answer; it was discarded."


def test_obligation_codes_separate_bad_citations_bad_quotes_and_bad_deadlines():
    raw = {"claims": [], "obligations": [
        {"kind": "payment", "party": None, "description": "x", "due_date": None, "line_id": "p7.l1",
         "quote": "Client shall pay"},
        {"kind": "payment", "party": None, "description": "x", "due_date": None, "line_id": "p1.l1",
         "quote": "Client must pay within ninety days"},
        {"kind": "deliverable", "party": None, "description": "x", "due_date": "2026-12-01", "line_id": "p1.l2",
         "quote": "deliver the report by November 20, 2026"}]}
    _, obligations, issues = _analyze(raw)
    assert obligations == []
    assert [i.reason for i in issues] == ["invalid_citation", "obligation_not_grounded", "deadline_not_grounded"]


def test_grounding_diagnostics_never_carry_values_or_quotes(settings):
    def answer(task):
        if isinstance(task, ExtractTask):
            return {"fields": [
                {"key": "vendor", "value": f"{SECRET} Inc", "line_id": task.lines[0].id, "quote": f"{SECRET} Inc",
                 "confidence": 0.9},
                {"key": "vendor", "value": "Zephyr", "line_id": "p9.l9", "quote": "Payable to Zephyr Tools LLC",
                 "confidence": 0.9}]}
        return empty_answers(task)

    ctx = make_ctx(settings, ScriptedModel(answer))
    doc_id = run(ctx, "invoice.pdf", _vendorless())
    text, stored = _stored(ctx, doc_id)
    counts = stored["model"]["grounding_rejections_by_reason"]
    assert (counts["unsupported_by_document"], counts["invalid_citation"], sum(counts.values())) == (1, 1, 2)
    for leaked in (SECRET, "Zephyr", "Payable", "p9.l9"):
        assert leaked not in text
    m = compute_metrics(ctx)
    assert m.grounding_rejections_by_reason["invalid_citation"] == 1


def test_a_classification_outside_the_allowed_types_is_a_schema_rejection():
    task = ClassifyTask((DocLine("p1.l1", "x"),), (DocType.INVOICE,))
    assert [i.reason for i in grounding.ground_classify({"doc_type": "receipt", "confidence": 0.9}, task)[2]] == [
        "schema_invalid"]
    assert [i.reason for i in grounding.ground_classify("I think invoice", task)[2]] == ["invalid_json"]


def test_older_metric_records_read_as_zero_counts():
    old = {"stages_ms": {"read": 90}, "model": {"name": "nemotron", "calls": {"AnalyzeTask": 1}, "retries": 0}}
    new = diagnostics.normalize(old)
    assert new["stages_ms"] == {"read": 90} and new["model"]["calls"] == {"AnalyzeTask": 1}
    assert new["model"]["failure_reasons"] == dict.fromkeys((r.value for r in FailureReason), 0)
    assert sum(new["model"]["grounding_rejections_by_reason"].values()) == 0
    junk = diagnostics.normalize({"model": {"failure_reasons": {"invalid_json": "3", SECRET: 4, "empty_content": -1}}})
    assert junk["model"]["failure_reasons"] == dict.fromkeys((r.value for r in FailureReason), 0)
    assert diagnostics.normalize(None)["model"]["rejections_by_reason"]["schema_invalid"] == 0


def test_an_old_metrics_row_still_works_with_the_feedback_export_and_the_api(settings):
    ctx = make_ctx(settings, ScriptedModel(empty_answers))
    doc_id = run(ctx, "invoice.pdf", invoice_pdf())
    with ctx.db() as conn:
        conn.execute("UPDATE doc_metrics SET metrics_json = ? WHERE doc_id = ?",
                     (json.dumps({"model": {"prompt_versions": ["old"], "config": {}}}), doc_id))
        records, _, _ = feedback.collect(conn)
    assert records == []
    m = compute_metrics(ctx)
    assert m.model_failure_reasons == dict.fromkeys((r.value for r in FailureReason), 0)
