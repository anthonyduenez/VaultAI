"""Dashboard: status counts, deadlines, and per-currency money totals from local records."""

from __future__ import annotations

import sqlite3
from collections import Counter
from datetime import date, timedelta
from decimal import Decimal

from vaultai import repo
from vaultai.actions import OBLIGATION_CATEGORY
from vaultai.domain import (
    EXPORTABLE,
    REVIEWABLE,
    ActionCategory,
    AuditEntry,
    Dashboard,
    DocStatus,
    DocType,
    DocumentSummary,
    MoneyTotal,
    Obligation,
    ObligationStatus,
    TypeCount,
)

APPROVED_BUCKET: dict[DocType, str] = {
    DocType.INVOICE: "approved_payables",
    DocType.PURCHASE_ORDER: "approved_payables",
    DocType.RECEIPT: "approved_receipts",
    DocType.CHECK: "approved_checks",
}
MONEY_FIELDS = ("approved_payables", "approved_receipts", "approved_checks", "pending_review")
IN_FLIGHT = frozenset({DocStatus.PROCESSING, DocStatus.EXTRACTED, DocStatus.VALIDATED})
UNKNOWN_CURRENCY = "Unknown"
UPCOMING_DAYS = 30
RENEWAL_DAYS = 90
LIST_LIMIT = 10
RECENT_ACTIVITY = 15


def _money_bucket(doc: DocumentSummary) -> str | None:
    bucket = APPROVED_BUCKET.get(doc.doc_type) if doc.doc_type else None
    if bucket is None or doc.total_amount is None:
        return None
    if doc.status in EXPORTABLE:
        return bucket
    return "pending_review" if doc.status in REVIEWABLE else None


def financial_totals(docs: list[DocumentSummary]) -> list[MoneyTotal]:
    totals: dict[str, dict[str, Decimal]] = {}
    for doc in docs:
        bucket = _money_bucket(doc)
        if bucket is None or doc.total_amount is None:
            continue
        sums = totals.setdefault(doc.currency or UNKNOWN_CURRENCY, dict.fromkeys(MONEY_FIELDS, Decimal("0")))
        sums[bucket] += doc.total_amount
    return [MoneyTotal(currency=currency, **sums) for currency, sums in sorted(totals.items())]


def build_dashboard(
    docs: list[DocumentSummary], obligations: list[Obligation], recent: list[AuditEntry], *, today: date
) -> Dashboard:
    status = Counter(d.status for d in docs)
    types = Counter(d.doc_type for d in docs if d.doc_type is not None)
    rejected = {d.id for d in docs if d.status is DocStatus.REJECTED}
    open_dated = sorted(
        (o for o in obligations if o.status is ObligationStatus.OPEN and o.due_date and o.doc_id not in rejected),
        key=lambda o: (o.due_date, o.id),
    )
    upcoming = [o for o in open_dated if o.due_date <= today + timedelta(days=UPCOMING_DAYS)]
    renewals = [
        o for o in open_dated
        if OBLIGATION_CATEGORY[o.kind] is ActionCategory.RENEWAL and o.due_date <= today + timedelta(days=RENEWAL_DAYS)
    ]
    return Dashboard(
        total_documents=len(docs),
        processing=sum(status[s] for s in IN_FLIGHT),
        needs_review=status[DocStatus.NEEDS_REVIEW],
        ready=status[DocStatus.READY],
        approved=status[DocStatus.APPROVED] + status[DocStatus.EXPORTED],
        rejected=status[DocStatus.REJECTED],
        failed=status[DocStatus.FAILED],
        by_type=[TypeCount(doc_type=t, count=n) for t, n in sorted(types.items(), key=lambda kv: (-kv[1], kv[0]))],
        upcoming_deadlines=upcoming[:LIST_LIMIT],
        renewals=renewals[:LIST_LIMIT],
        financial_totals=financial_totals(docs),
        recent_activity=recent,
    )


def load_dashboard(conn: sqlite3.Connection, *, today: date) -> Dashboard:
    return build_dashboard(
        repo.load_summaries(conn), repo.open_dated_obligations(conn), repo.load_audit(conn, limit=RECENT_ACTIVITY),
        today=today,
    )
