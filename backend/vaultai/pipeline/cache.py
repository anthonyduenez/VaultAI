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
from vaultai.domain import SCHEMA_VERSION
from vaultai.model.prompts import PROMPT_VERSION

HITS = "cache_hits"
MISSES = "cache_misses"


def key(kind: str, content_hash: str, engine: str) -> str:
    return hashlib.sha256("|".join((kind, content_hash, engine, SCHEMA_VERSION, PROMPT_VERSION)).encode()).hexdigest()


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
