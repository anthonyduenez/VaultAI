"""Human review: approve / reject / defer / reopen, field edits, batch review, obligation status.

Every status change goes through domain.check_transition and leaves an audit row.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from vaultai import db, repo
from vaultai.context import AppContext
from vaultai.domain import (
    EXPORTABLE,
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
    check_transition,
)
from vaultai.repo import Problem
from vaultai.schemas import SCHEMAS

EDITABLE: frozenset[DocStatus] = REVIEWABLE | EXPORTABLE | {DocStatus.REJECTED}


@dataclass(frozen=True)
class ReviewRule:
    target: DocStatus
    audit: AuditAction
    # Narrower than the state machine: deferring only makes sense while a human still has to decide.
    only_from: frozenset[DocStatus] | None = None


RULES: dict[ReviewAction, ReviewRule] = {
    ReviewAction.APPROVE: ReviewRule(DocStatus.APPROVED, AuditAction.APPROVE),
    ReviewAction.REJECT: ReviewRule(DocStatus.REJECTED, AuditAction.REJECT),
    ReviewAction.DEFER: ReviewRule(DocStatus.NEEDS_REVIEW, AuditAction.DEFER, REVIEWABLE),
    ReviewAction.REOPEN: ReviewRule(DocStatus.NEEDS_REVIEW, AuditAction.REOPEN),
}

_CLEAR_APPROVAL = {"approved_at": None, "approved_by": None, "approval_kind": None, "deferred_at": None}


def _columns(action: ReviewAction, actor: str, stamp: str) -> dict[str, object]:
    return {
        ReviewAction.APPROVE: {
            "approved_at": stamp, "approved_by": actor, "approval_kind": ApprovalKind.HUMAN.value, "deferred_at": None,
        },
        ReviewAction.REJECT: {"deferred_at": None},
        ReviewAction.DEFER: {"deferred_at": stamp},
        ReviewAction.REOPEN: _CLEAR_APPROVAL,
    }[action]


def plan_review(
    current: DocStatus, action: ReviewAction, *, error_count: int, note: str | None, actor: str, now: datetime
) -> dict[str, object]:
    """Column changes for one review action, or InvalidTransition / Problem when it is not allowed."""
    rule = RULES[action]
    if rule.only_from is not None and current not in rule.only_from:
        raise InvalidTransition(current, rule.target)
    check_transition(current, rule.target)
    if action is ReviewAction.APPROVE and error_count and not (note or "").strip():
        raise Problem(
            422, "note_required", "Add a note explaining why you are approving a document with unresolved errors."
        )
    return {"status": rule.target.value, **_columns(action, actor, db.iso(now))}


def _update_document(conn: sqlite3.Connection, doc_id: str, changes: dict[str, object], now: datetime) -> None:
    assignments = ", ".join(f"{column} = ?" for column in changes)
    conn.execute(
        f"UPDATE documents SET {assignments}, updated_at = ? WHERE id = ?", (*changes.values(), db.iso(now), doc_id)
    )


def apply_review(
    conn: sqlite3.Connection, doc_id: str, action: ReviewAction, note: str | None, *, actor: str, now: datetime
) -> DocStatus:
    row = repo.doc_row(conn, doc_id)
    changes = plan_review(
        DocStatus(row["status"]), action, error_count=row["error_count"], note=note, actor=actor, now=now
    )
    _update_document(conn, doc_id, changes, now)
    db.audit(
        conn, RULES[action].audit, actor, doc_id=doc_id, before=row["status"], after=str(changes["status"]), note=note
    )
    return DocStatus(str(changes["status"]))


def batch_review(ctx: AppContext, request: BatchReviewRequest, *, actor: str, now: datetime) -> BatchReviewResult:
    results: list[BatchItemResult] = []
    with ctx.db() as conn:
        for doc_id in dict.fromkeys(request.doc_ids):
            try:
                with db.transaction(conn):
                    status = apply_review(conn, doc_id, request.action, request.note, actor=actor, now=now)
            except InvalidTransition as exc:
                results.append(BatchItemResult(doc_id=doc_id, ok=False, error=f"Not allowed while {exc.current.value}."))
            except Problem as exc:
                results.append(BatchItemResult(doc_id=doc_id, ok=False, error=exc.message))
            else:
                results.append(BatchItemResult(doc_id=doc_id, ok=True, status=status))
    return BatchReviewResult(results=results)


# ---------------------------------------------------------------- field edits


def _money(value: str, options: tuple[str, ...]) -> str:
    cleaned = re.sub(r"[\s,$€£¥]", "", value)
    if not re.fullmatch(r"-?\d+(\.\d+)?", cleaned):
        raise ValueError(value)
    return str(Decimal(cleaned).quantize(Decimal("0.01")))


def _date(value: str, options: tuple[str, ...]) -> str:
    return date.fromisoformat(value).isoformat()


def _enum(value: str, options: tuple[str, ...]) -> str:
    if not options:
        return value
    match = next((o for o in options if o.casefold() == value.casefold()), None)
    if match is None:
        raise ValueError(value)
    return match


_TRUE, _FALSE = frozenset({"true", "yes", "y", "1"}), frozenset({"false", "no", "n", "0"})


def _bool(value: str, options: tuple[str, ...]) -> str:
    lowered = value.casefold()
    if lowered in _TRUE:
        return "true"
    if lowered in _FALSE:
        return "false"
    raise ValueError(value)


Normalizer = Callable[[str, tuple[str, ...]], str]

NORMALIZERS: dict[ValueType, tuple[Normalizer, str]] = {
    ValueType.MONEY: (_money, "Enter an amount such as 1234.56."),
    ValueType.DATE: (_date, "Enter a date as YYYY-MM-DD."),
    ValueType.ENUM: (_enum, "Choose one of the allowed values."),
    ValueType.BOOL: (_bool, "Enter true or false."),
}


def normalize_value(value_type: ValueType, raw: str | None, options: tuple[str, ...] = ()) -> str | None:
    """Canonical stored form of a human-entered value; None clears the field. Raises Problem(422) when invalid."""
    text = (raw or "").strip()
    if not text:
        return None
    if value_type not in NORMALIZERS:
        return text
    normalize, message = NORMALIZERS[value_type]
    try:
        return normalize(text, options)
    except (ValueError, InvalidOperation):
        raise Problem(422, "invalid_value", message) from None


def _options(doc_type: str | None, key: str) -> tuple[str, ...]:
    schema = SCHEMAS.get(DocType(doc_type)) if doc_type else None
    if schema is None or key not in schema.keys:
        return ()
    return schema.field(key).options


def edit_field(
    ctx: AppContext, conn: sqlite3.Connection, doc_id: str, key: str, edit: FieldEdit, *, actor: str, now: datetime
) -> None:
    """Record a human value next to the AI value, revalidate, and reopen an approved record. Caller holds the transaction."""
    from vaultai.pipeline.process import revalidate

    doc = repo.doc_row(conn, doc_id)
    status = DocStatus(doc["status"])
    if status not in EDITABLE:
        raise Problem(409, "document_busy", "This document cannot be edited until processing finishes.")
    field = conn.execute("SELECT * FROM fields WHERE doc_id = ? AND key = ?", (doc_id, key)).fetchone()
    if field is None:
        raise repo.not_found()
    new_value = normalize_value(ValueType(field["value_type"]), edit.value, _options(doc["doc_type"], key))
    before = field["human_value"] if field["edited_at"] is not None else field["ai_value"]
    stamp = db.iso(now)
    conn.execute(
        "UPDATE fields SET human_value = ?, edited_by = ?, edited_at = ? WHERE id = ?",
        (new_value, actor, stamp, field["id"]),
    )
    db.audit(conn, AuditAction.EDIT_FIELD, actor, doc_id=doc_id, field_key=key, before=before, after=new_value, note=edit.note)
    revalidate(ctx, conn, doc_id)
    if status in EXPORTABLE:
        check_transition(status, DocStatus.NEEDS_REVIEW)
        _update_document(conn, doc_id, {"status": DocStatus.NEEDS_REVIEW.value, **_CLEAR_APPROVAL}, now)
        db.audit(
            conn, AuditAction.REOPEN, actor, doc_id=doc_id, before=status.value,
            after=DocStatus.NEEDS_REVIEW.value, note="Reopened because a field was edited after approval.",
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
    """needs_review first, then ready, deferred last; oldest first within each. Groups feed batch review."""
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
    rows = conn.execute(repo.SUMMARY_SQL + " WHERE d.status IN ('needs_review', 'ready')")
    return build_review_queue([repo.summary_from_row(r) for r in rows])
