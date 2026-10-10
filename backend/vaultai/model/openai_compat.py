"""Adapter for a local OpenAI-compatible chat server (NIM, vLLM, TensorRT-LLM, llama.cpp, Ollama /v1).

The endpoint must be loopback or private; that is checked again before every
request. Proxy environment variables are ignored so a request can never be
routed off the machine. Failures raise ModelUnavailable; there is no fallback.
"""

from __future__ import annotations

import threading
import time
from urllib.parse import urlparse

import httpx

from vaultai.config import Settings, is_local_host
from vaultai.domain import ModelKind, ModelStatus
from vaultai.model import prompts
from vaultai.model.base import ModelUnavailable, Task, TranscribeTask

STATUS_TTL_S = 10.0
STATUS_TIMEOUT_S = 2.0


class OpenAICompatModel:
    kind = ModelKind.OPENAI_COMPAT

    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None) -> None:
        self.settings = settings
        self.name = settings.model_name
        self.vision = settings.model_vision
        self.endpoint = settings.model_endpoint.rstrip("/")
        headers = {"Authorization": f"Bearer {settings.model_api_key}"} if settings.model_api_key else {}
        self._client = httpx.Client(transport=transport, trust_env=False, headers=headers, follow_redirects=False)
        self._status: tuple[float, ModelStatus] | None = None
        self._lock = threading.Lock()

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

    def run(self, task: Task) -> object:
        self._ensure_local()
        if isinstance(task, TranscribeTask) and not self.vision:
            raise ModelUnavailable("the configured model does not accept images")
        payload: dict = {
            "model": self.name,
            "messages": prompts.messages(task, self.settings.model_system_prefix),
            "temperature": self.settings.model_temperature,
            "max_tokens": self.settings.model_max_tokens,
        }
        if self.settings.model_json_mode:
            payload["response_format"] = {"type": "json_object"}
        try:
            r = self._client.post(f"{self.endpoint}/chat/completions", json=payload, timeout=self.settings.model_timeout_s)
        except httpx.HTTPError as exc:
            raise ModelUnavailable(f"model request failed ({type(exc).__name__})") from exc
        if r.status_code != 200:
            raise ModelUnavailable(f"model server answered HTTP {r.status_code}")
        try:
            content = r.json()["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ModelUnavailable("model server returned an unexpected response shape") from exc
        return prompts.parse_reply(content) if isinstance(content, str) else content
