"""Action Center: what needs a human today, computed on request from local records."""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from datetime import date, datetime, timedelta

from vaultai import repo
from vaultai.domain import (
    EXPORTABLE,
    ActionCategory,
    ActionCenter,
    ActionItem,
    ApprovalKind,
    CategoryCount,
    DocStatus,
    DocumentSummary,
    IssueCode,
    Obligation,
    ObligationKind,
    ObligationStatus,
    Severity,
    ValidationIssue,
)

A = ActionCategory

OBLIGATION_CATEGORY: dict[ObligationKind, ActionCategory] = {
    ObligationKind.RENEWAL: A.RENEWAL,
    ObligationKind.NOTICE: A.RENEWAL,
    ObligationKind.EXPIRATION: A.RENEWAL,
    ObligationKind.PAYMENT: A.PAYMENT_DUE,
    ObligationKind.SIGNATURE: A.UNSIGNED,
    ObligationKind.OBLIGATION: A.OBLIGATION,
    ObligationKind.DELIVERABLE: A.OBLIGATION,
}

OBLIGATION_LABEL: dict[ObligationKind, str] = {
    ObligationKind.RENEWAL: "Renewal",
    ObligationKind.NOTICE: "Notice",
    ObligationKind.EXPIRATION: "Expiration",
    ObligationKind.PAYMENT: "Payment",
    ObligationKind.SIGNATURE: "Signature",
    ObligationKind.OBLIGATION: "Obligation",
    ObligationKind.DELIVERABLE: "Deliverable",
}

ISSUE_CATEGORY: dict[IssueCode, ActionCategory] = {
    IssueCode.MISSING_REQUIRED: A.MISSING_INFO,
    IssueCode.AMOUNT_MISMATCH: A.AMOUNT_MISMATCH,
    IssueCode.WRITTEN_AMOUNT_MISMATCH: A.AMOUNT_MISMATCH,
    IssueCode.TAX_INCONSISTENT: A.AMOUNT_MISMATCH,
    IssueCode.DUPLICATE_DOCUMENT: A.DUPLICATE,
    IssueCode.MISSING_SIGNATURE: A.UNSIGNED,
    IssueCode.RISKY_CLAUSE: A.RISKY_CLAUSE,
    IssueCode.AMBIGUOUS_CLAUSE: A.RISKY_CLAUSE,
    IssueCode.AUTO_RENEWAL: A.RISKY_CLAUSE,
    IssueCode.PROMPT_INJECTION: A.INJECTION,
    IssueCode.HIDDEN_TEXT: A.INJECTION,
}

DOC_TITLE: dict[ActionCategory, str] = {
    A.NEEDS_REVIEW: "Needs review",
    A.READY: "Ready to approve",
    A.PROCESSING_FAILURE: "Processing failed",
    A.RECENTLY_APPROVED: "Approved",
    A.MISSING_INFO: "Missing information",
    A.AMOUNT_MISMATCH: "Amounts do not match",
    A.DUPLICATE: "Possible duplicate",
    A.UNSIGNED: "Missing signature",
    A.RISKY_CLAUSE: "Clause needs attention",
    A.INJECTION: "Suspicious instructions found",
}

STATUS_ACTION: dict[DocStatus, tuple[ActionCategory, Severity]] = {
    DocStatus.NEEDS_REVIEW: (A.NEEDS_REVIEW, Severity.WARNING),
    DocStatus.READY: (A.READY, Severity.INFO),
    DocStatus.FAILED: (A.PROCESSING_FAILURE, Severity.ERROR),
}

# Issues on a record a human already approved or rejected were seen at review time.
SETTLED: frozenset[DocStatus] = EXPORTABLE | {DocStatus.REJECTED}
DEADLINE_WINDOW_DAYS = 60
URGENT_DAYS = 30
RECENT_APPROVAL_DAYS = 7
MAX_DETAIL_MESSAGES = 3

_SEVERITY_RANK = {Severity.ERROR: 0, Severity.WARNING: 1, Severity.INFO: 2}
_CATEGORY_ORDER = {c: i for i, c in enumerate(ActionCategory)}


def _days(n: int) -> str:
    return f"{n} day" if n == 1 else f"{n} days"


def due_phrase(due: date, today: date) -> str:
    delta = (due - today).days
    if delta < 0:
        return f"overdue by {_days(-delta)}"
    if delta == 0:
        return "due today"
    return f"due in {_days(delta)}"


def _obligation_severity(due: date, today: date) -> Severity:
    delta = (due - today).days
    if delta < 0:
        return Severity.ERROR
    return Severity.WARNING if delta <= URGENT_DAYS else Severity.INFO


def _doc_item(doc: DocumentSummary, category: ActionCategory, severity: Severity, detail: str | None,
              created_at: datetime) -> ActionItem:
    return ActionItem(
        id=f"{category.value}:{doc.id}",
        category=category,
        severity=severity,
        title=f"{DOC_TITLE[category]}: {doc.filename}",
        detail=detail,
        doc_id=doc.id,
        doc_filename=doc.filename,
        doc_type=doc.doc_type,
        created_at=created_at,
    )


def _issue_detail(issues: list[ValidationIssue]) -> str:
    messages = [i.message for i in issues[:MAX_DETAIL_MESSAGES]]
    extra = len(issues) - len(messages)
    return " ".join(messages) + (f" ({extra} more)" if extra else "")


def build_action_center(
    docs: list[DocumentSummary],
    obligations: list[Obligation],
    issues: list[tuple[str, ValidationIssue]],
    *,
    today: date,
    now: datetime,
) -> ActionCenter:
    by_id = {d.id: d for d in docs}
    items: list[ActionItem] = []

    for doc in docs:
        if doc.status in STATUS_ACTION:
            category, severity = STATUS_ACTION[doc.status]
            detail = doc.error if doc.status is DocStatus.FAILED else None
            items.append(_doc_item(doc, category, severity, detail, doc.processed_at or doc.created_at))
        if doc.status in EXPORTABLE and doc.approved_at and now - doc.approved_at <= timedelta(days=RECENT_APPROVAL_DAYS):
            who = f"Approved by {doc.approved_by}" if doc.approval_kind is ApprovalKind.HUMAN else "Auto-approved by policy"
            items.append(_doc_item(doc, A.RECENTLY_APPROVED, Severity.INFO, who, doc.approved_at))

    grouped: dict[tuple[str, ActionCategory], list[ValidationIssue]] = defaultdict(list)
    for doc_id, issue in issues:
        doc = by_id.get(doc_id)
        if doc is not None and doc.status not in SETTLED and issue.code in ISSUE_CATEGORY:
            grouped[(doc_id, ISSUE_CATEGORY[issue.code])].append(issue)
    for (doc_id, category), found in grouped.items():
        severity = min((i.severity for i in found), key=_SEVERITY_RANK.__getitem__)
        latest = max(i.created_at for i in found)
        items.append(_doc_item(by_id[doc_id], category, severity, _issue_detail(found), latest))

    horizon = today + timedelta(days=DEADLINE_WINDOW_DAYS)
    for ob in obligations:
        doc = by_id.get(ob.doc_id)
        if ob.status is not ObligationStatus.OPEN or ob.due_date is None or ob.due_date > horizon:
            continue
        if doc is None or doc.status is DocStatus.REJECTED:
            continue
        items.append(
            ActionItem(
                id=f"obligation:{ob.id}",
                category=OBLIGATION_CATEGORY[ob.kind],
                severity=_obligation_severity(ob.due_date, today),
                title=f"{OBLIGATION_LABEL[ob.kind]} {due_phrase(ob.due_date, today)}",
                detail=ob.description,
                doc_id=ob.doc_id,
                doc_filename=ob.doc_filename,
                doc_type=doc.doc_type,
                due_date=ob.due_date,
                obligation_id=ob.id,
                created_at=doc.created_at,
            )
        )

    items.sort(
        key=lambda i: (
            _SEVERITY_RANK[i.severity], i.due_date is None, i.due_date or today, _CATEGORY_ORDER[i.category], i.title
        )
    )
    counts: dict[ActionCategory, int] = defaultdict(int)
    for item in items:
        counts[item.category] += 1
    return ActionCenter(
        items=items,
        counts=[CategoryCount(category=c, count=counts[c]) for c in ActionCategory if counts[c]],
    )


def load_action_center(conn: sqlite3.Connection, *, today: date, now: datetime) -> ActionCenter:
    docs = [repo.summary_from_row(r) for r in conn.execute(repo.SUMMARY_SQL)]
    obligations = [
        repo.obligation_from_row(r)
        for r in conn.execute(repo.OBLIGATION_SQL + " WHERE o.status = 'open' AND o.due_date IS NOT NULL")
    ]
    issues = [(r["doc_id"], repo.issue_from_row(r)) for r in conn.execute("SELECT * FROM issues")]
    return build_action_center(docs, obligations, issues, today=today, now=now)
