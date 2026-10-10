"""Human review: approve / reject / defer / reopen, field edits, batch review, obligation status.

Every status change goes through db.set_status, which enforces the state machine and leaves an audit row.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Callable
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from vaultai import db, repo
from vaultai.context import AppContext
from vaultai.domain import (
    EDITABLE,
    EXPORTABLE,
    REVIEW_ACTION_FROM,
    REVIEW_ACTION_TARGET,
    REVIEWABLE,
    ApprovalKind,
    AuditAction,
    BatchItemResult,
    BatchReviewRequest,
    BatchReviewResult,
    DocStatus,
    DocType,
    DocumentSummary,
    FieldEdit,
    InvalidTransition,
    ObligationStatus,
    ReviewAction,
    ReviewGroup,
    ReviewQueue,
    ValueType,
)
from vaultai.extraction.normalize import CURRENCY_SYMBOLS, money_str
from vaultai.pipeline.process import revalidate
from vaultai.repo import Problem
from vaultai.schemas import match_option


def apply_review(
    conn: sqlite3.Connection, doc_id: str, action: ReviewAction, note: str | None, *, actor: str, now: datetime
) -> DocStatus:
    row = repo.doc_row(conn, doc_id)
    current, target = DocStatus(row["status"]), REVIEW_ACTION_TARGET[action]
    if current not in REVIEW_ACTION_FROM[action]:
        raise InvalidTransition(current, target)
    if action is ReviewAction.APPROVE and row["error_count"] and not (note or "").strip():
        raise Problem(
            422, "note_required", "Add a note explaining why you are approving a document with unresolved errors."
        )
    stamp = db.iso(now)
    columns = {
        ReviewAction.APPROVE: {"approved_at": stamp, "approved_by": actor, "approval_kind": ApprovalKind.HUMAN.value},
        ReviewAction.DEFER: {"deferred_at": stamp},
    }.get(action)
    db.set_status(conn, doc_id, target, actor=actor, action=AuditAction(action.value), note=note, columns=columns, at=now)
    return target


def batch_review(ctx: AppContext, request: BatchReviewRequest, *, actor: str, now: datetime) -> BatchReviewResult:
    results: list[BatchItemResult] = []
    with ctx.db() as conn:
        for doc_id in dict.fromkeys(request.doc_ids):
            try:
                with db.transaction(conn):
                    status = apply_review(conn, doc_id, request.action, request.note, actor=actor, now=now)
            except InvalidTransition as exc:
                results.append(BatchItemResult(doc_id=doc_id, ok=False, error=str(exc)))
            except Problem as exc:
                results.append(BatchItemResult(doc_id=doc_id, ok=False, error=exc.message))
            else:
                results.append(BatchItemResult(doc_id=doc_id, ok=True, status=status))
    return BatchReviewResult(results=results)


_MONEY_NOISE = re.compile("|".join(map(re.escape, sorted(CURRENCY_SYMBOLS, key=len, reverse=True))) + r"|[\s,]")


def _money(value: str, key: str) -> str:
    cleaned = _MONEY_NOISE.sub("", value)
    if not re.fullmatch(r"-?\d+(\.\d+)?", cleaned):
        raise ValueError(value)
    return money_str(Decimal(cleaned))


def _date(value: str, key: str) -> str:
    return date.fromisoformat(value).isoformat()


def _enum(value: str, key: str) -> str:
    match = match_option(key, value)
    if match is None:
        raise ValueError(value)
    return match


_TRUE, _FALSE = frozenset({"true", "yes", "y", "1"}), frozenset({"false", "no", "n", "0"})


def _bool(value: str, key: str) -> str:
    lowered = value.casefold()
    if lowered in _TRUE:
        return "true"
    if lowered in _FALSE:
        return "false"
    raise ValueError(value)


Normalizer = Callable[[str, str], str]

NORMALIZERS: dict[ValueType, tuple[Normalizer, str]] = {
    ValueType.MONEY: (_money, "Enter an amount such as 1234.56."),
    ValueType.DATE: (_date, "Enter a date as YYYY-MM-DD."),
    ValueType.ENUM: (_enum, "Choose one of the allowed values."),
    ValueType.BOOL: (_bool, "Enter true or false."),
}


def normalize_value(value_type: ValueType, raw: str | None, key: str = "") -> str | None:
    """Canonical stored form of a human-entered value; None clears the field. Raises Problem(422) when invalid."""
    text = (raw or "").strip()
    if not text:
        return None
    if value_type not in NORMALIZERS:
        return text
    normalize, message = NORMALIZERS[value_type]
    try:
        return normalize(text, key)
    except (ValueError, InvalidOperation):
        raise Problem(422, "invalid_value", message) from None


def edit_field(
    ctx: AppContext, conn: sqlite3.Connection, doc_id: str, key: str, edit: FieldEdit, *, actor: str, now: datetime
) -> None:
    """Record a human value next to the AI value, revalidate, and reopen an approved record. Caller holds the transaction."""
    status = DocStatus(repo.doc_row(conn, doc_id)["status"])
    if status not in EDITABLE:
        raise Problem(409, "document_busy", "This document cannot be edited until processing finishes.")
    field = conn.execute("SELECT * FROM fields WHERE doc_id = ? AND key = ?", (doc_id, key)).fetchone()
    if field is None:
        raise repo.not_found()
    new_value = normalize_value(ValueType(field["value_type"]), edit.value, key)
    before = field["human_value"] if field["edited_at"] is not None else field["ai_value"]
    conn.execute(
        "UPDATE fields SET human_value = ?, edited_by = ?, edited_at = ? WHERE id = ?",
        (new_value, actor, db.iso(now), field["id"]),
    )
    db.audit(conn, AuditAction.EDIT_FIELD, actor, doc_id=doc_id, field_key=key, before=before, after=new_value, note=edit.note)
    revalidate(ctx, conn, doc_id)
    if status in EXPORTABLE:
        db.set_status(
            conn, doc_id, DocStatus.NEEDS_REVIEW, actor=actor, action=AuditAction.REOPEN,
            note="Reopened because a field was edited after approval.", at=now,
        )


def update_obligation(
    conn: sqlite3.Connection, obligation_id: int, status: ObligationStatus, *, actor: str, now: datetime
) -> None:
    current = repo.get_obligation(conn, obligation_id)
    conn.execute(
        "UPDATE obligations SET status = ?, updated_at = ? WHERE id = ?", (status.value, db.iso(now), obligation_id)
    )
    db.audit(
        conn, AuditAction.OBLIGATION_UPDATE, actor, doc_id=current.doc_id, field_key=f"obligation:{obligation_id}",
        before=current.status.value, after=status.value,
    )


def build_review_queue(docs: list[DocumentSummary]) -> ReviewQueue:
    pending = sorted(
        (d for d in docs if d.status in REVIEWABLE),
        key=lambda d: (d.deferred, d.status is not DocStatus.NEEDS_REVIEW, d.created_at, d.id),
    )
    similar: dict[tuple[DocType, str], list[DocumentSummary]] = {}
    for doc in pending:
        if doc.doc_type is not None and doc.counterparty and doc.counterparty.strip():
            similar.setdefault((doc.doc_type, doc.counterparty.strip().casefold()), []).append(doc)
    groups = [
        ReviewGroup(
            key=f"{doc_type.value}:{party}", doc_type=doc_type, counterparty=members[0].counterparty,
            doc_ids=[m.id for m in members],
        )
        for (doc_type, party), members in similar.items()
        if len(members) >= 2
    ]
    return ReviewQueue(items=pending, groups=groups)


def load_review_queue(conn: sqlite3.Connection) -> ReviewQueue:
    statuses = sorted(s.value for s in REVIEWABLE)
    return build_review_queue(repo.load_summaries(conn, f"WHERE d.status IN ({','.join('?' * len(statuses))})", statuses))
