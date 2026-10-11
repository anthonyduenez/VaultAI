"""Content-addressed cache for OCR and model outputs.

A key covers what produced the value (kind, input hash, engine or model name,
schema and prompt versions), so changing any of them misses instead of
returning a stale answer.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3

from vaultai import db
from vaultai.config import Settings
from vaultai.domain import SCHEMA_VERSION
from vaultai.model.prompts import PROMPT_VERSION

HITS = "cache_hits"
MISSES = "cache_misses"


def key(kind: str, content_hash: str, engine: str) -> str:
    return hashlib.sha256("|".join((kind, content_hash, engine, SCHEMA_VERSION, PROMPT_VERSION)).encode()).hexdigest()


MODEL_CONFIG_VERSION = "2026.10.3"


def model_config(settings: Settings) -> dict:
    """The settings that change what a model answers. Part of every model cache key and of each document's metrics."""
    return {
        "version": MODEL_CONFIG_VERSION,
        "temperature": settings.model_temperature,
        "max_tokens": settings.model_max_tokens,
        "response_format": settings.model_response_format if settings.model_json_mode else "none",
        "system_prefix": settings.model_system_prefix,
        "enable_thinking": settings.model_enable_thinking,
        "extract_context_chars": settings.model_extract_context_chars,
        "analysis_context_chars": settings.model_analysis_context_chars,
    }


def model_key(task_kind: str, doc_sha256: str, messages: object, prompt_version: str, model_name: str,
              settings: Settings) -> str:
    """Keyed by document hash, the exact prompt (which covers page content), prompt version, model, and configuration."""
    body = {"doc": doc_sha256, "messages": messages, "prompt": prompt_version, "config": model_config(settings)}
    return key(task_kind, content_hash(body), model_name)


def content_hash(obj: object) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


def get(conn: sqlite3.Connection, cache_key: str) -> object | None:
    row = conn.execute("SELECT value FROM cache WHERE key = ?", (cache_key,)).fetchone()
    db.bump(conn, HITS if row else MISSES)
    return json.loads(row["value"]) if row else None


def put(conn: sqlite3.Connection, cache_key: str, value: object) -> None:
    conn.execute(
        "INSERT INTO cache (key, value, created_at) VALUES (?, ?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (cache_key, json.dumps(value, default=str), db.iso()),
    )
