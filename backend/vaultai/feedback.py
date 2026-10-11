"""Reviewer corrections as local evaluation or tuning data. Nothing here trains a model.

export_feedback() writes one JSON line per corrected field to data/exports
(owner-only, created exclusively). Only fields a person reviewed count as
ground truth:

  * the document must be approved (or exported after approval) by a person.
    Rejected, unreviewed, policy-approved, and deferred documents are skipped;
  * documents with injection or hidden-text findings are skipped, because
    their content was written to manipulate a model;
  * by default only fields a reviewer edited are written ("corrected"). With
    include_confirmed, model values on approved documents that the reviewer
    left alone are added as "confirmed"; they were seen, not checked one by one.

Records carry the evidence line, prompt and model versions, and OCR metadata,
but no reviewer name and no raw document. include_document_text adds a
second file with each exported document's visible lines (never quarantined
ones). Every record names a split chosen from its template group (document
type plus counterparty), so one vendor's layouts never land in both a tuning
and an evaluation split, and near-duplicates stay together.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from vaultai import db
from vaultai.context import AppContext
from vaultai.domain import ApprovalKind, AuditAction, DocStatus, IssueCode

FORMAT_VERSION = "vaultai-feedback/1"
SPLITS = (("train", 8), ("validation", 1), ("test", 1))
_REVIEWED = (DocStatus.APPROVED.value, DocStatus.EXPORTED.value)
_UNTRUSTED = (IssueCode.PROMPT_INJECTION.value, IssueCode.HIDDEN_TEXT.value)


@dataclass(frozen=True)
class FeedbackExport:
    path: Path
    records: int
    documents: int
    skipped_documents: int
    document_text_path: Path | None = None


def template_group(doc_type: str | None, counterparty: str | None, sha256: str) -> str:
    """Documents of one type from one counterparty share a layout; documents without a counterparty stand alone."""
    party = re.sub(r"\W+", "", (counterparty or "").casefold())
    return hashlib.sha256(f"{doc_type}|{party or sha256}".encode()).hexdigest()[:16]


def split_for(group: str) -> str:
    bucket = int(group[:8], 16) % sum(n for _, n in SPLITS)
    for name, size in SPLITS:
        if bucket < size:
            return name
        bucket -= size
    return SPLITS[-1][0]


def _line_confidence(lines_json: str | None, line_id: str | None) -> float | None:
    if not lines_json or not line_id:
        return None
    return next((ln.get("confidence") for ln in json.loads(lines_json) if ln.get("id") == line_id), None)


def _documents(conn: sqlite3.Connection) -> tuple[list[sqlite3.Row], int]:
    rows = conn.execute(
        f"SELECT * FROM documents WHERE status IN ({','.join('?' * len(_REVIEWED))}) AND approval_kind = ? "
        "ORDER BY approved_at, id", (*_REVIEWED, ApprovalKind.HUMAN.value)).fetchall()
    untrusted = {r[0] for r in conn.execute(
        f"SELECT DISTINCT doc_id FROM issues WHERE code IN ({','.join('?' * len(_UNTRUSTED))})", _UNTRUSTED)}
    kept = [r for r in rows if r["id"] not in untrusted and not r["injection_suspected"]]
    return kept, len(rows) - len(kept)


def collect(conn: sqlite3.Connection, *, include_confirmed: bool = False, include_evidence_text: bool = True,
            exported_at: str | None = None) -> tuple[list[dict], list[sqlite3.Row], int]:
    docs, skipped = _documents(conn)
    records: list[dict] = []
    seen: set[tuple] = set()
    for d in docs:
        metrics_row = conn.execute("SELECT metrics_json FROM doc_metrics WHERE doc_id = ?", (d["id"],)).fetchone()
        model_metrics = json.loads(metrics_row[0]).get("model", {}) if metrics_row else {}
        pages = {r["page_no"]: r for r in conn.execute("SELECT * FROM pages WHERE doc_id = ?", (d["id"],))}
        group = template_group(d["doc_type"], d["counterparty"], d["sha256"])
        where = "edited_at IS NOT NULL" + (" OR ai_origin = 'model'" if include_confirmed else "")
        for f in conn.execute(f"SELECT * FROM fields WHERE doc_id = ? AND ({where}) ORDER BY key", (d["id"],)):
            edited = f["edited_at"] is not None
            corrected = f["human_value"] if edited else f["ai_value"]
            key = (group, f["key"], corrected, f["evidence_text"])
            if key in seen:
                continue
            seen.add(key)
            page = pages.get(f["page"])
            records.append({
                "format": FORMAT_VERSION,
                "record_id": hashlib.sha256(f"{d['sha256']}|{f['key']}".encode()).hexdigest()[:16],
                "document_sha256": d["sha256"],
                "doc_type": d["doc_type"],
                "template_group": group,
                "split": split_for(group),
                "field": f["key"],
                "value_type": f["value_type"],
                "required": bool(f["required"]),
                "original_value": f["ai_value"],
                "original_origin": f["ai_origin"],
                "original_confidence": f["confidence"],
                "corrected_value": corrected,
                "label": ("confirmed" if f["human_value"] == f["ai_value"] else "corrected") if edited else "confirmed",
                "evidence": {
                    "page": f["page"],
                    "line_id": f["line_id"],
                    "bbox": json.loads(f["bbox_json"]) if f["bbox_json"] else None,
                    **({"text": f["evidence_text"]} if include_evidence_text else {}),
                },
                "review": {"decision": "approved", "approval_kind": d["approval_kind"], "approved_at": d["approved_at"],
                           "edited_at": f["edited_at"]},
                "model": {"name": f["model_name"] or d["model_name"], "kind": d["model_kind"],
                          "prompt_versions": model_metrics.get("prompt_versions", []),
                          "config": model_metrics.get("config")},
                "ocr": {"engine": d["ocr_engine"],
                        "text_method": page["text_method"] if page else None,
                        "page_confidence": page["ocr_confidence"] if page else None,
                        "line_confidence": _line_confidence(page["lines_json"] if page else None, f["line_id"])},
                "schema_version": f["schema_version"],
                "exported_at": exported_at,
            })
    return records, docs, skipped


def _jsonl(rows: list[dict]) -> bytes:
    return "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows).encode()


def export_feedback(ctx: AppContext, *, actor: str, now: datetime, include_confirmed: bool = False,
                    include_evidence_text: bool = True, include_document_text: bool = False) -> FeedbackExport:
    stamp = db.iso(now)
    with ctx.db() as conn:
        records, docs, skipped = collect(conn, include_confirmed=include_confirmed,
                                         include_evidence_text=include_evidence_text, exported_at=stamp)
        texts = []
        if include_document_text:
            exported = {r["document_sha256"] for r in records}
            for d in docs:
                if d["sha256"] not in exported:
                    continue
                lines = [{"id": ln["id"], "text": ln["text"]}
                         for p in conn.execute("SELECT lines_json FROM pages WHERE doc_id = ? ORDER BY page_no", (d["id"],))
                         for ln in json.loads(p["lines_json"]) if ln.get("flag") is None]
                texts.append({"document_sha256": d["sha256"], "doc_type": d["doc_type"], "lines": lines})
        storage = ctx.storage
        path = storage.write_new(storage.exports, storage.new_name("jsonl"), _jsonl(records))
        text_path = storage.write_new(storage.exports, storage.new_name("jsonl"), _jsonl(texts)) if include_document_text else None
        with db.transaction(conn):
            db.audit(conn, AuditAction.EXPORT, actor,
                     note=f"feedback jsonl: {len(records)} records from {len({r['document_sha256'] for r in records})} "
                          f"documents{', with document text' if include_document_text else ''}")
    return FeedbackExport(path, len(records), len({r["document_sha256"] for r in records}), skipped, text_path)
