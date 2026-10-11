"""Adapter for a local OpenAI-compatible chat server (vLLM, NIM, TensorRT-LLM, llama.cpp, Ollama /v1).

The endpoint must be loopback or private; that is checked again before every
request. Proxy environment variables are ignored and redirects are not followed,
so a request can never be routed off the machine. Failures raise
ModelUnavailable; there is no fallback to any other model.

Output is constrained as tightly as the server allows: a JSON Schema per task
(response_format json_schema), or JSON object mode when the server refuses
schemas. Every request carries a top-level chat_template_kwargs with
enable_thinking set from VAULTAI_MODEL_ENABLE_THINKING (false by default), so a
reasoning model such as Nemotron puts its answer in message.content instead of
a reasoning field. Only message.content is ever read: reasoning text is never
taken as an answer, and a reply with empty content counts as unusable. A reply
that is empty, not JSON, or does not fit the schema gets one repair attempt
(two attempts in all); nothing else is retried. Replies are read with a size
cap, and no reply text is ever logged.
"""

from __future__ import annotations

import json
import re
import threading
import time
from urllib.parse import urlparse

import httpx

from vaultai.config import Settings, is_local_host
from vaultai.domain import ModelKind, ModelStatus
from vaultai.logging_utils import get_logger
from vaultai.model import output, prompts
from vaultai.model.base import ModelUnavailable, Reply, Task, TranscribeTask
from vaultai.model.diagnostics import FailureReason, failure_reason

STATUS_TTL_S = 10.0
STATUS_TIMEOUT_S = 2.0
CONNECT_TIMEOUT_S = 5.0
MAX_ATTEMPTS = 2
MAX_RESPONSE_BYTES = 1_000_000
_SCHEMA_REFUSAL = re.compile(rb"json_schema|response_format|guided|structured|grammar", re.I)
_FINISH = re.compile(r"[a-z_]{1,24}")

log = get_logger("vaultai.model")


class _TooLarge(Exception):
    pass


class _SchemaRefused(Exception):
    pass


class OpenAICompatModel:
    kind = ModelKind.OPENAI_COMPAT

    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None) -> None:
        self.settings = settings
        self.name = settings.model_name
        self.vision = settings.model_vision
        self.endpoint = settings.model_endpoint.rstrip("/")
        headers = {"Authorization": f"Bearer {settings.model_api_key}"} if settings.model_api_key else {}
        self._client = httpx.Client(transport=transport, trust_env=False, headers=headers, follow_redirects=False)
        self._timeout = httpx.Timeout(settings.model_timeout_s, connect=min(CONNECT_TIMEOUT_S, settings.model_timeout_s))
        self._format = settings.model_response_format if settings.model_json_mode else "none"
        self._slots = threading.BoundedSemaphore(max(1, settings.model_concurrency))
        self._status: tuple[float, ModelStatus] | None = None
        self._lock = threading.Lock()

    @property
    def response_format(self) -> str:
        return self._format

    def _ensure_local(self) -> None:
        host = urlparse(self.endpoint).hostname or ""
        if not is_local_host(host):
            raise ModelUnavailable("model endpoint is not a local address")

    def status(self) -> ModelStatus:
        with self._lock:
            if self._status and time.monotonic() - self._status[0] < STATUS_TTL_S:
                return self._status[1]
        reachable, detail = self._probe()
        status = ModelStatus(kind=self.kind, name=self.name, endpoint=self.endpoint, reachable=reachable,
                             vision=self.vision, detail=detail)
        with self._lock:
            self._status = (time.monotonic(), status)
        return status

    def _probe(self) -> tuple[bool, str]:
        try:
            self._ensure_local()
            r = self._client.get(f"{self.endpoint}/models", timeout=STATUS_TIMEOUT_S)
        except ModelUnavailable as exc:
            return False, str(exc)
        except httpx.HTTPError as exc:
            return False, f"Cannot reach the local model server at {self.endpoint} ({type(exc).__name__})."
        if r.status_code != 200:
            return False, f"The local model server at {self.endpoint} answered HTTP {r.status_code}."
        try:
            served = [m.get("id") for m in r.json().get("data", []) if isinstance(m, dict)]
        except (ValueError, AttributeError):
            served = []
        if served and self.name not in served:
            return True, f"Reachable, but it serves {', '.join(map(str, served[:3]))} rather than {self.name}."
        return True, f"Reachable; serving {self.name}."

    def _payload(self, task: Task, messages: list[dict]) -> dict:
        payload: dict = {
            "model": self.name,
            "messages": messages,
            "temperature": self.settings.model_temperature,
            "max_tokens": self.settings.model_max_tokens,
            "chat_template_kwargs": {"enable_thinking": self.settings.model_enable_thinking},
        }
        if self._format == "json_schema":
            name = f"vaultai_{type(task).__name__.removesuffix('Task').lower()}"
            payload["response_format"] = {"type": "json_schema",
                                          "json_schema": {"name": name, "schema": output.json_schema(task), "strict": True}}
        elif self._format == "json_object":
            payload["response_format"] = {"type": "json_object"}
        return payload

    def _post(self, payload: dict) -> tuple[object, dict]:
        """(message content, usage). Reads at most MAX_RESPONSE_BYTES of the reply, and only message.content of it."""
        with self._slots:
            try:
                with self._client.stream("POST", f"{self.endpoint}/chat/completions", json=payload,
                                         timeout=self._timeout) as r:
                    body = bytearray()
                    for chunk in r.iter_bytes():
                        body += chunk
                        if len(body) > MAX_RESPONSE_BYTES:
                            raise _TooLarge
                    status = r.status_code
            except httpx.HTTPError as exc:
                raise ModelUnavailable(f"model request failed ({type(exc).__name__})") from exc
        if status in (400, 422) and "json_schema" in json.dumps(payload.get("response_format") or {}) \
                and _SCHEMA_REFUSAL.search(bytes(body[:4096])):
            raise _SchemaRefused
        if status != 200:
            raise ModelUnavailable(f"model server answered HTTP {status}")
        try:
            data = json.loads(body)
            choice = data["choices"][0]
            content = choice["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ModelUnavailable("model server returned an unexpected response shape") from exc
        if content is None or (isinstance(content, str) and not content.strip()):
            finish = choice.get("finish_reason") if isinstance(choice, dict) else None
            log.warning("model reply had no content; reasoning is never used as an answer model=%s finish=%s",
                        self.name, finish if isinstance(finish, str) and _FINISH.fullmatch(finish) else "other")
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        return content, {k: int(v) for k, v in usage.items() if k in ("prompt_tokens", "completion_tokens")
                         and isinstance(v, int) and v >= 0}

    def _send(self, task: Task, messages: list[dict]) -> tuple[object, dict]:
        try:
            return self._post(self._payload(task, messages))
        except _SchemaRefused:
            with self._lock:
                self._format = "json_object"
            log.warning("local model server refused json_schema output; using json_object model=%s", self.name)
            return self._post(self._payload(task, messages))

    def _log_failure(self, task: Task, attempt: int, reason: FailureReason) -> None:
        """The stable code only; the reply itself is never logged."""
        log.info("model reply rejected model=%s task=%s attempt=%d reason=%s", self.name, type(task).__name__, attempt,
                 reason.value)

    def run(self, task: Task) -> Reply:
        self._ensure_local()
        if isinstance(task, TranscribeTask) and not self.vision:
            raise ModelUnavailable("the configured model does not accept images")
        base = prompts.messages(task, self.settings.model_system_prefix)
        chars = sum(len(m["content"]) for m in base if isinstance(m["content"], str))
        messages = base
        failures: list[FailureReason] = []
        usage: dict[str, int] = {}
        raw: object = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                content, used = self._send(task, messages)
            except _TooLarge:
                failures.append(FailureReason.RESPONSE_TOO_LARGE)
                log.warning("model reply rejected model=%s task=%s attempt=%d reason=%s",
                            self.name, type(task).__name__, attempt, FailureReason.RESPONSE_TOO_LARGE.value)
                return Reply(None, attempt, tuple(failures), usage, chars)
            for k, v in used.items():
                usage[k] = usage.get(k, 0) + v
            if content is None or (isinstance(content, str) and not content.strip()):
                raw = None
                failures.append(FailureReason.EMPTY_CONTENT)
                self._log_failure(task, attempt, failures[-1])
                messages = prompts.repair(base, "", "it was empty")
                continue
            raw = prompts.parse_reply(content) if isinstance(content, str) else content
            parsed, problem = output.check(task, raw)
            if parsed is not None:
                return Reply(raw, attempt, tuple(failures), usage, chars)
            failures.append(failure_reason(raw))
            self._log_failure(task, attempt, failures[-1])
            previous = content if isinstance(content, str) else json.dumps(content, default=str)
            messages = prompts.repair(base, previous, problem or "it was not usable")
        return Reply(raw, MAX_ATTEMPTS, tuple(failures), usage, chars)
