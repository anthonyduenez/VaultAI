from __future__ import annotations

import hashlib

from fastapi.testclient import TestClient

from tests.surfaces.conftest import FakeCore, field_row, insert_document
from vaultai import db
from vaultai.context import AppContext
from vaultai.domain import Source


def stored_name(ctx: AppContext, doc_id: str) -> str:
    with ctx.db() as conn:
        return conn.execute("SELECT stored_name FROM documents WHERE id = ?", (doc_id,)).fetchone()[0]


def test_delete_document_removes_files_and_rows_and_redacts_its_audit(
    client: TestClient, app_ctx: AppContext, fake_core: FakeCore
) -> None:
    doc_id = insert_document(app_ctx, filename="acme.pdf", text="unique-word", status="needs_review",
                             fields=[field_row("vendor", "Acme")], raw=b"%PDF-1.7 acme",
                             obligations=[{"kind": "payment", "description": "Pay", "due_date": "2026-11-01"}])
    keep = insert_document(app_ctx, filename="other.pdf")
    raw = app_ctx.storage.raw_path(stored_name(app_ctx, doc_id))
    with app_ctx.db() as conn:
        conn.execute(
            "INSERT INTO intake (batch_id, filename, source, size_bytes, preflight, document_id, created_at) "
            "VALUES ('b', 'acme.pdf', 'upload', 1, 'accepted', ?, ?)", (doc_id, db.iso()),
        )
    client.patch(f"/api/documents/{doc_id}/fields/vendor", json={"value": "Acme Supply", "note": "per invoice"})
    client.post(f"/api/documents/{doc_id}/review", json={"action": "reject", "note": "Acme duplicate"})

    assert client.delete(f"/api/documents/{doc_id}").status_code == 204

    assert not raw.exists()
    assert not app_ctx.storage.derived_doc_dir(doc_id).exists()
    assert client.get(f"/api/documents/{doc_id}").status_code == 404
    with app_ctx.db() as conn:
        leftovers = [conn.execute(f"SELECT COUNT(*) FROM {t} WHERE doc_id = ?", (doc_id,)).fetchone()[0]
                     for t in ("fields", "obligations", "pages", "doc_fts")]
        intake_rows = conn.execute("SELECT COUNT(*) FROM intake").fetchone()[0]
    assert leftovers == [0, 0, 0, 0]
    assert intake_rows == 0
    with app_ctx.db() as conn:
        audit = [tuple(r) for r in conn.execute(
            "SELECT action, actor, field_key, before, after, note FROM audit_log WHERE doc_id = ? ORDER BY id", (doc_id,)
        )]
    sha12 = hashlib.sha256(b"%PDF-1.7 acme").hexdigest()[:12]
    assert audit == [
        ("edit_field", "local-user", "vendor", None, None, None),
        ("reject", "local-user", None, None, None, None),
        ("delete", "local-user", None, None, None, f"sha256 {sha12}"),
    ]
    assert client.get(f"/api/documents/{keep}").status_code == 200


def test_delete_all_requires_confirmation_and_leaves_one_audit_row(client: TestClient, app_ctx: AppContext) -> None:
    doc_id = insert_document(app_ctx)
    raw = app_ctx.storage.raw_path(stored_name(app_ctx, doc_id))
    (app_ctx.storage.exports / "old.xlsx").write_bytes(b"x")

    assert client.request("DELETE", "/api/data", json={"confirm": "yes"}).status_code == 422
    assert raw.exists()

    assert client.request("DELETE", "/api/data", json={"confirm": "DELETE"}).status_code == 204
    assert not raw.exists()
    assert list(app_ctx.storage.exports.iterdir()) == []
    assert client.get("/api/documents").json()["total"] == 0
    assert [(a["action"], a["doc_id"]) for a in client.get("/api/audit").json()] == [("delete_all", None)]


def test_removing_an_intake_row_deletes_only_unprocessed_documents(
    client: TestClient, app_ctx: AppContext, fake_core: FakeCore
) -> None:
    first = fake_core.ingest_bytes(app_ctx, filename="a.pdf", data=b"%PDF a", source=Source.UPLOAD, batch_id="b")
    processed_id = insert_document(app_ctx, status="needs_review")
    with app_ctx.db() as conn:
        second = conn.execute(
            "INSERT INTO intake (batch_id, filename, source, size_bytes, preflight, document_id, created_at) "
            "VALUES ('b', 'b.pdf', 'upload', 1, 'accepted', ?, ?)", (processed_id, db.iso()),
        ).lastrowid

    assert client.delete(f"/api/intake/{first.id}").status_code == 204
    assert client.delete(f"/api/intake/{second}").status_code == 204

    assert client.get(f"/api/documents/{first.document_id}").status_code == 404
    assert client.get(f"/api/documents/{processed_id}").json()["status"] == "needs_review"
    assert client.get("/api/intake").json() == []
