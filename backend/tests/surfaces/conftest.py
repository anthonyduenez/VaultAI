from __future__ import annotations

import hashlib
import json
import secrets
import sys
import types
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.conftest import NullModel
from vaultai import db, repo
from vaultai.app import create_app
from vaultai.config import Settings
from vaultai.context import AppContext
from vaultai.domain import SCHEMA_VERSION, IntakeItem, Source
from vaultai.storage import Storage

BASE_URL = "http://127.0.0.1:8765"
CSRF = {"X-VaultAI": "1"}
T0 = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


def make_client(settings: Settings) -> TestClient:
    return TestClient(create_app(settings, model=NullModel()), base_url=BASE_URL, headers=CSRF)


@pytest.fixture
def client(settings: Settings) -> TestClient:
    return make_client(settings)


@pytest.fixture
def app_ctx(client: TestClient) -> AppContext:
    return client.app.state.ctx


def field_row(key: str, value: str | None, *, label: str | None = None, value_type: str = "text",
              required: bool = False, confidence: float = 0.95, origin: str = "rules", page: int | None = 1,
              line_id: str | None = "p1.l1", evidence_text: str | None = None, raw_text: str | None = None,
              bbox: tuple[float, float, float, float] | None = (0.1, 0.1, 0.4, 0.12)) -> dict[str, Any]:
    return {
        "key": key, "label": label or key.replace("_", " ").title(), "value_type": value_type, "required": required,
        "ai_value": value, "raw_text": raw_text if raw_text is not None else value, "confidence": confidence,
        "ai_origin": origin, "page": page, "line_id": line_id,
        "evidence_text": evidence_text if evidence_text is not None else value, "bbox": bbox,
    }


def insert_document(
    ctx: AppContext,
    *,
    filename: str = "doc.pdf",
    status: str = "needs_review",
    doc_type: str | None = "invoice",
    counterparty: str | None = None,
    doc_date: str | None = None,
    total: str | None = None,
    currency: str | None = "USD",
    primary_id: str | None = None,
    confidence: float | None = 0.9,
    text: str = "",
    fields: list[dict[str, Any]] = (),  # type: ignore[assignment]
    issues: list[tuple[str, str, str]] = (),  # type: ignore[assignment]
    obligations: list[dict[str, Any]] = (),  # type: ignore[assignment]
    clauses: list[dict[str, Any]] = (),  # type: ignore[assignment]
    line_items: list[dict[str, Any]] = (),  # type: ignore[assignment]
    approved_at: datetime | None = None,
    approved_by: str | None = None,
    approval_kind: str | None = None,
    created_at: datetime = T0,
    raw: bytes | None = None,
    source: str = "upload",
    error: str | None = None,
    next_deadline: str | None = None,
    model_name: str | None = "rules",
) -> str:
    """A processed document as the core pipeline would leave it, written straight into the tables."""
    doc_id = secrets.token_hex(8)
    data = raw if raw is not None else f"%PDF-1.4 {doc_id}".encode()
    stored_name = Storage.new_name("pdf")
    ctx.storage.write_new(ctx.storage.raw, stored_name, data)
    ctx.storage.write_new(ctx.storage.derived_doc_dir(doc_id), "p1.png", b"\x89PNG\r\n\x1a\nfake")
    stamp = db.iso(created_at)
    with ctx.db() as conn, db.transaction(conn):
        conn.execute(
            "INSERT INTO documents (id, filename, stored_name, sha256, size_bytes, mime, page_count, source, status, "
            "doc_type, doc_type_confidence, classification_method, counterparty, doc_date, total_amount, currency, "
            "primary_id, next_deadline, confidence, route, model_kind, model_name, schema_version, ocr_engine, "
            "processing_ms, error, approved_at, approved_by, approval_kind, created_at, processed_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (doc_id, filename, stored_name, hashlib.sha256(data).hexdigest(), len(data), "application/pdf", 1,
             source, status, doc_type, 0.97 if doc_type else None, "rules" if doc_type else None, counterparty,
             doc_date, total, currency, primary_id, next_deadline, confidence, "fast", "mock", model_name,
             SCHEMA_VERSION, None, 120, error, db.iso(approved_at) if approved_at else None, approved_by,
             approval_kind, stamp, stamp, stamp),
        )
        conn.execute(
            "INSERT INTO pages (doc_id, page_no, width, height, text_method, page_hash, image_name, lines_json) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (doc_id, 1, 612, 792, "pdf_text", "h", "p1.png",
             json.dumps([{"id": "p1.l1", "text": text[:200] or "line", "bbox": [0.1, 0.1, 0.4, 0.12]}])),
        )
        for f in fields:
            conn.execute(
                "INSERT INTO fields (doc_id, key, label, value_type, required, ai_value, raw_text, currency, confidence, "
                "ai_origin, page, bbox_json, line_id, evidence_text, model_name, schema_version, extracted_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (doc_id, f["key"], f["label"], f["value_type"], int(f["required"]), f["ai_value"], f["raw_text"],
                 currency if f["value_type"] == "money" else None, f["confidence"], f["ai_origin"], f["page"],
                 db.bbox_to_json(f["bbox"]), f["line_id"], f["evidence_text"], model_name, SCHEMA_VERSION, stamp),
            )
        for code, severity, message in issues:
            conn.execute(
                "INSERT INTO issues (doc_id, code, severity, message, created_at) VALUES (?,?,?,?,?)",
                (doc_id, code, severity, message, stamp),
            )
        for ob in obligations:
            conn.execute(
                "INSERT INTO obligations (doc_id, kind, party, description, due_date, status, origin, created_at, "
                "updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (doc_id, ob["kind"], ob.get("party"), ob["description"], ob.get("due_date"), ob.get("status", "open"),
                 ob.get("origin", "rules"), stamp, stamp),
            )
        for cl in clauses:
            conn.execute(
                "INSERT INTO clauses (doc_id, kind, heading, text, risk, origin) VALUES (?,?,?,?,?,?)",
                (doc_id, cl["kind"], cl["heading"], cl["text"], cl.get("risk", "none"), "rules"),
            )
        for i, li in enumerate(line_items):
            conn.execute(
                "INSERT INTO line_items (doc_id, idx, description, quantity, unit_price, amount) VALUES (?,?,?,?,?,?)",
                (doc_id, i, li["description"], li.get("quantity"), li.get("unit_price"), li.get("amount")),
            )
        conn.execute("INSERT INTO doc_fts (doc_id, filename, body) VALUES (?,?,?)", (doc_id, filename, text))
    return doc_id


@dataclass
class FakeCore:
    """Stands in for the core's ingest_bytes and revalidate seams, recording what they receive."""

    ingested: list[dict[str, Any]] = field(default_factory=list)
    revalidated: list[str] = field(default_factory=list)

    def ingest_bytes(self, ctx: AppContext, *, filename: str, data: bytes, source: Source, batch_id: str,
                     doc_type_hint: Any = None) -> IntakeItem:
        self.ingested.append({"filename": filename, "data": data, "source": source, "doc_type_hint": doc_type_hint})
        doc_id = insert_document(ctx, filename=filename, status="imported", doc_type=None, raw=data,
                                 source=source.value, confidence=None)
        with ctx.db() as conn:
            cur = conn.execute(
                "INSERT INTO intake (batch_id, filename, source, mime, size_bytes, preflight, document_id, created_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (batch_id, filename, source.value, "application/pdf", len(data), "accepted", doc_id, db.iso()),
            )
            return repo.get_intake(conn, cur.lastrowid)

    def revalidate(self, ctx: AppContext, conn: Any, doc_id: str) -> None:
        self.revalidated.append(doc_id)


@pytest.fixture
def fake_core(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeCore]:
    fake = FakeCore()
    ingest = types.ModuleType("vaultai.intake.ingest")
    ingest.ingest_bytes = fake.ingest_bytes  # type: ignore[attr-defined]
    process = types.ModuleType("vaultai.pipeline.process")
    process.revalidate = fake.revalidate  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "vaultai.intake.ingest", ingest)
    monkeypatch.setitem(sys.modules, "vaultai.pipeline.process", process)
    yield fake


def audit_rows(ctx: AppContext, doc_id: str | None = None) -> list[tuple[str, str | None, str | None, str | None]]:
    with ctx.db() as conn:
        rows = conn.execute(
            "SELECT action, field_key, before, after FROM audit_log WHERE (? IS NULL OR doc_id = ?) ORDER BY id",
            (doc_id, doc_id),
        ).fetchall()
    return [tuple(r) for r in rows]
