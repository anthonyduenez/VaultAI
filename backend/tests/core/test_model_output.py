"""The OpenAI-compatible adapter: structured output, the single repair attempt, size caps, and failure routing."""

from __future__ import annotations

import json
from dataclasses import replace

import httpx
import pytest

from conftest import make_ctx
from core.conftest import doc, field, invoice_pdf, issue_codes, run
from vaultai.config import Settings
from vaultai.domain import DocType, ModelKind
from vaultai.metrics import compute_metrics
from vaultai.model import openai_compat
from vaultai.model.base import ClassifyTask, DocLine, ExtractTask, ModelUnavailable
from vaultai.model.openai_compat import OpenAICompatModel
from vaultai.schemas import SCHEMAS

VENDOR = SCHEMAS[DocType.INVOICE].field("vendor")


def _settings(settings, **changes):
    return replace(settings, model_backend=ModelKind.OPENAI_COMPAT, model_endpoint="http://127.0.0.1:9/v1",
                   model_name="nemotron", **changes)


def _reply(content: str, usage: dict | None = None) -> httpx.Response:
    body = {"choices": [{"message": {"role": "assistant", "content": content}}]}
    if usage is not None:
        body["usage"] = usage
    return httpx.Response(200, json=body)


class Server:
    """A scripted local model server: one reply per request, recording every request body."""

    def __init__(self, *replies) -> None:
        self.replies = list(replies)
        self.requests: list[dict] = []
        self.raw: list[bytes] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.raw.append(request.content)
        self.requests.append(json.loads(request.content))
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(reply, Exception):
            raise reply
        return reply if isinstance(reply, httpx.Response) else _reply(reply)


def _model(settings, server: Server, **changes) -> OpenAICompatModel:
    return OpenAICompatModel(_settings(settings, **changes), transport=httpx.MockTransport(server))


TASK = ExtractTask(DocType.INVOICE, (VENDOR,), (DocLine("p1.l1", "Zephyr Tools LLC"),))
GOOD = json.dumps({"fields": [{"key": "vendor", "value": "Zephyr Tools LLC", "line_id": "p1.l1",
                               "quote": "Zephyr Tools LLC", "confidence": 0.9}]})


def test_output_is_constrained_by_a_json_schema_for_the_task(settings):
    server = Server(GOOD)
    reply = _model(settings, server).run(TASK)
    fmt = server.requests[0]["response_format"]
    assert (fmt["type"], fmt["json_schema"]["strict"]) == ("json_schema", True)
    item = fmt["json_schema"]["schema"]["properties"]["fields"]["items"]
    assert item["properties"]["key"]["enum"] == ["vendor"] and item["additionalProperties"] is False
    assert (reply.attempts, reply.failures) == (1, ())


def test_malformed_output_gets_exactly_one_repair_attempt(settings):
    server = Server("Sure! The vendor is Zephyr.", GOOD)
    reply = _model(settings, server).run(TASK)
    assert (reply.output["fields"][0]["value"], reply.attempts, reply.failures) == ("Zephyr Tools LLC", 2, ("invalid_json",))
    repair = server.requests[1]["messages"]
    assert [m["role"] for m in repair] == ["system", "user", "assistant", "user"]
    assert repair[2]["content"] == "Sure! The vendor is Zephyr." and "was not a JSON object" in repair[3]["content"]


def test_two_bad_replies_stop_after_the_second_attempt(settings):
    server = Server('{"fields": [], "status": "approved"}')
    reply = _model(settings, server).run(TASK)
    assert (len(server.requests), reply.attempts, reply.failures) == (2, 2, ("schema_invalid", "schema_invalid"))
    assert "extra_forbidden" in server.requests[1]["messages"][-1]["content"]


def test_a_value_the_document_does_not_support_is_never_retried(settings):
    unsupported = json.dumps({"fields": [{"key": "vendor", "value": "Acme", "line_id": "p1.l1", "quote": "Acme",
                                          "confidence": 0.99}]})
    server = Server(unsupported)
    reply = _model(settings, server).run(TASK)
    assert (len(server.requests), reply.attempts) == (1, 1)


def test_a_server_that_refuses_json_schema_drops_to_json_object_once(settings):
    refused = httpx.Response(400, json={"error": {"message": "response_format json_schema is not supported"}})
    server = Server(refused, GOOD, GOOD)
    model = _model(settings, server)
    model.run(TASK)
    model.run(TASK)
    assert [r.get("response_format", {}).get("type") for r in server.requests] == ["json_schema", "json_object", "json_object"]
    assert model.response_format == "json_object"


def test_other_http_errors_are_unavailable_not_a_format_downgrade(settings):
    model = _model(settings, Server(httpx.Response(400, json={"error": "context length exceeded"})))
    with pytest.raises(ModelUnavailable):
        model.run(TASK)
    assert model.response_format == "json_schema"


def test_json_mode_off_sends_no_response_format(settings):
    server = Server(GOOD)
    _model(settings, server, model_json_mode=False).run(TASK)
    assert "response_format" not in server.requests[0]


def test_an_oversized_reply_is_cut_off_and_rejected_without_a_retry(settings, monkeypatch):
    monkeypatch.setattr(openai_compat, "MAX_RESPONSE_BYTES", 10_000)
    server = Server(_reply("x" * 50_000))
    reply = _model(settings, server).run(TASK)
    assert (reply.output, reply.failures, len(server.requests)) == (None, ("response_too_large",), 1)


def test_token_usage_is_summed_over_attempts(settings):
    server = Server(_reply("nope", {"prompt_tokens": 400, "completion_tokens": 9}),
                    _reply(GOOD, {"prompt_tokens": 420, "completion_tokens": 30}))
    reply = _model(settings, server).run(TASK)
    assert reply.usage == {"prompt_tokens": 820, "completion_tokens": 39}


def _vendorless() -> bytes:
    return invoice_pdf(vendor="", extra=("Payable to Zephyr Tools LLC",))


def test_a_model_timeout_routes_the_document_to_review(settings):
    model = _model(settings, Server(httpx.ReadTimeout("timed out")))
    ctx = make_ctx(_settings(settings), model)
    doc_id = run(ctx, "invoice.pdf", _vendorless())
    row = doc(ctx, doc_id)
    assert (row["status"], row["approved_at"]) == ("needs_review", None)
    assert issue_codes(ctx, doc_id) == {"model_unavailable", "missing_required"}


@pytest.mark.parametrize("content", [
    "", "null", "[]", '{"fields": "Zephyr"}', '{"fields": [{"key": "vendor", "value": 7}]}', "{" * 5000,
    '{"fields": [{"key": "vendor", "value": "Zephyr Tools LLC", "line_id": "p1.l1", "quote": "Zephyr", "confidence": NaN}]}',
    "\x00\x01\x02", '{"fields": [' + '{"key": "vendor", "value": null},' * 80 + '{"key": "vendor", "value": null}]}',
])
def test_malformed_output_never_crashes_the_worker(settings, content):
    ctx = make_ctx(_settings(settings), _model(settings, Server(content)))
    doc_id = run(ctx, "invoice.pdf", _vendorless())
    row = doc(ctx, doc_id)
    assert (row["status"], field(ctx, doc_id, "vendor")["ai_value"]) == ("needs_review", None)
    assert "model_output_rejected" in issue_codes(ctx, doc_id)


def test_model_calls_tokens_and_retries_reach_the_metrics(settings):
    good = json.dumps({"fields": [{"key": "vendor", "value": "Zephyr Tools LLC", "line_id": "p1.l11",
                                   "quote": "Payable to Zephyr Tools LLC", "confidence": 0.9}]})
    server = Server(_reply("not json", {"prompt_tokens": 500, "completion_tokens": 5}),
                    _reply(good, {"prompt_tokens": 520, "completion_tokens": 40}))
    ctx = make_ctx(_settings(settings), _model(settings, server))
    doc_id = run(ctx, "invoice.pdf", _vendorless())
    assert field(ctx, doc_id, "vendor")["ai_value"] == "Zephyr Tools LLC"
    with ctx.db() as conn:
        stored = json.loads(conn.execute("SELECT metrics_json FROM doc_metrics WHERE doc_id = ?", (doc_id,)).fetchone()[0])
    assert stored["model"]["calls"] == {"ExtractTask": 1}
    assert (stored["model"]["attempts"], stored["model"]["retries"], stored["model"]["prompt_tokens"]) == (2, 1, 1020)
    assert stored["model"]["prompt_versions"] == ["2026.10.3/invoice@2026.10.3"]
    assert set(stored["stages_ms"]) == {"read", "classify", "extract", "analyze"}
    m = compute_metrics(ctx)
    assert (m.model_calls, m.model_retries, m.model_prompt_tokens, m.model_completion_tokens) == (1, 1, 1020, 45)


def test_classification_prompt_names_the_rules_candidates(settings):
    server = Server('{"doc_type": "correspondence", "confidence": 0.9}')
    task = ClassifyTask((DocLine("p1.l1", "Notes"),), tuple(DocType), (DocType.CORRESPONDENCE, DocType.OTHER))
    _model(settings, server).run(task)
    assert "closest candidates were: correspondence, other" in server.requests[0]["messages"][1]["content"]


def _vllm(content: str | None, *, reasoning: str | None = None, finish: str = "stop") -> httpx.Response:
    """A reply shaped like vLLM's, including its reasoning fields."""
    return httpx.Response(200, json={
        "id": "chatcmpl-1", "object": "chat.completion", "model": "nemotron",
        "choices": [{"index": 0, "finish_reason": finish, "message": {
            "role": "assistant", "content": content, "reasoning": reasoning, "reasoning_content": reasoning, "tool_calls": []}}],
        "usage": {"prompt_tokens": 300, "completion_tokens": 20, "total_tokens": 320},
    })


def test_every_request_disables_thinking_at_the_top_level(settings):
    refused = httpx.Response(400, json={"error": {"message": "response_format json_schema is not supported"}})
    server = Server(refused, "not json", GOOD)
    reply = _model(settings, server).run(TASK)
    assert reply.attempts == 2
    assert [r.get("response_format", {}).get("type") for r in server.requests] == ["json_schema", "json_object", "json_object"]
    for body, raw in zip(server.requests, server.raw):
        assert body["chat_template_kwargs"] == {"enable_thinking": False}
        assert "extra_body" not in body and b"extra_body" not in raw
        assert body["model"] == "nemotron"


def test_enable_thinking_follows_the_setting(settings):
    server = Server(GOOD)
    _model(settings, server, model_enable_thinking=True).run(TASK)
    assert server.requests[0]["chat_template_kwargs"] == {"enable_thinking": True}


def test_the_demo_default_is_thinking_disabled(settings):
    from vaultai.config import load_settings
    from vaultai.config import REPO_ROOT, read_env_file

    assert Settings().model_enable_thinking is False
    example = read_env_file(REPO_ROOT / ".env.example")
    assert example["VAULTAI_MODEL_ENABLE_THINKING"] == "false"
    assert load_settings({**example, "VAULTAI_DATA_DIR": str(settings.data_dir)}).model_enable_thinking is False


def test_a_valid_json_content_reply_is_accepted(settings):
    good = json.dumps({"fields": [{"key": "vendor", "value": "Zephyr Tools LLC", "line_id": "p1.l11",
                                   "quote": "Payable to Zephyr Tools LLC", "confidence": 0.9}]})
    server = Server(_vllm(good))
    ctx = make_ctx(_settings(settings), _model(settings, server))
    doc_id = run(ctx, "invoice.pdf", _vendorless())
    vendor = field(ctx, doc_id, "vendor")
    assert (vendor["ai_value"], vendor["ai_origin"], vendor["line_id"]) == ("Zephyr Tools LLC", "model", "p1.l11")
    assert issue_codes(ctx, doc_id) == set() and len(server.requests) == 1
    assert (doc(ctx, doc_id)["status"], doc(ctx, doc_id)["route"], doc(ctx, doc_id)["approved_at"]) == ("ready", "model", None)


@pytest.mark.parametrize("content", [None, "", "   \n"])
def test_reasoning_is_never_taken_as_the_answer(settings, caplog, content):
    grounded = json.dumps({"fields": [{"key": "vendor", "value": "Zephyr Tools LLC", "line_id": "p1.l11",
                                       "quote": "Payable to Zephyr Tools LLC", "confidence": 0.9}]})
    server = Server(_vllm(content, reasoning=grounded, finish="length"))
    reply = _model(settings, server).run(TASK)
    assert (reply.output, reply.failures, len(server.requests)) == (None, ("empty_content", "empty_content"), 2)
    retry = server.requests[1]["messages"]
    assert [m["role"] for m in retry] == ["system", "user"] and "Your previous reply was empty" in retry[1]["content"]

    ctx = make_ctx(_settings(settings), _model(settings, Server(_vllm(content, reasoning=grounded))))
    doc_id = run(ctx, "invoice.pdf", _vendorless())
    assert (field(ctx, doc_id, "vendor")["ai_value"], doc(ctx, doc_id)["status"]) == (None, "needs_review")
    assert issue_codes(ctx, doc_id) == {"model_output_rejected", "missing_required"}
    assert "model reply had no content" in caplog.text and "Zephyr" not in caplog.text
