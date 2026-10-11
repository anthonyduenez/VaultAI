"""Document list: full-text search, filters, facets, and whitelisted sorting."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Literal

from vaultai import repo
from vaultai.domain import DocStatus, DocType, DocumentList, DocumentSort, StatusCount, TypeCount

SORT_COLUMNS: dict[DocumentSort, str] = {
    DocumentSort.CREATED_AT: "d.created_at",
    DocumentSort.DOC_DATE: "d.doc_date",
    DocumentSort.TOTAL_AMOUNT: "CAST(d.total_amount AS REAL)",
    DocumentSort.CONFIDENCE: "d.confidence",
    DocumentSort.FILENAME: "d.filename COLLATE NOCASE",
    DocumentSort.NEXT_DEADLINE: "d.next_deadline",
}

MAX_QUERY_TOKENS = 12


@dataclass(frozen=True)
class DocumentQuery:
    q: str | None = None
    doc_types: tuple[DocType, ...] = ()
    statuses: tuple[DocStatus, ...] = ()
    date_from: date | None = None
    date_to: date | None = None
    party: str | None = None
    deadline_within_days: int | None = None
    min_confidence: float | None = None
    sort: DocumentSort = DocumentSort.CREATED_AT
    order: Literal["asc", "desc"] = "desc"
    limit: int = 50
    offset: int = 0
    today: date = field(default_factory=date.today)


def fts_query(text: str) -> str | None:
    """User text as FTS5 prefix tokens. Only letters and digits survive, so no FTS syntax can be injected."""
    tokens = re.findall(r"[^\W_]+", text)[:MAX_QUERY_TOKENS]
    return " ".join(f'"{t}"*' for t in tokens) or None


def _like(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def where(query: DocumentQuery) -> tuple[str, list[object]]:
    """The WHERE clause (empty when nothing filters) over `documents d` and its parameters."""
    clauses: list[str] = []
    params: list[object] = []
    if query.q and query.q.strip():
        text_match = ["d.id IN (SELECT doc_id FROM fields WHERE "
                      "(CASE WHEN edited_at IS NOT NULL THEN human_value ELSE ai_value END) LIKE ? ESCAPE '\\')"]
        params_q: list[object] = [_like(query.q.strip())]
        match = fts_query(query.q)
        if match is not None:
            text_match.insert(0, "d.id IN (SELECT doc_id FROM doc_fts WHERE doc_fts MATCH ?)")
            params_q.insert(0, match)
        clauses.append(f"({' OR '.join(text_match)})")
        params.extend(params_q)
    if query.doc_types:
        clauses.append(f"d.doc_type IN ({','.join('?' * len(query.doc_types))})")
        params.extend(t.value for t in query.doc_types)
    if query.statuses:
        clauses.append(f"d.status IN ({','.join('?' * len(query.statuses))})")
        params.extend(s.value for s in query.statuses)
    if query.date_from:
        clauses.append("d.doc_date >= ?")
        params.append(query.date_from.isoformat())
    if query.date_to:
        clauses.append("d.doc_date <= ?")
        params.append(query.date_to.isoformat())
    if query.party and query.party.strip():
        clauses.append("d.counterparty LIKE ? ESCAPE '\\'")
        params.append(_like(query.party.strip()))
    if query.deadline_within_days is not None:
        clauses.append("d.next_deadline IS NOT NULL AND d.next_deadline <= ?")
        params.append((query.today + timedelta(days=query.deadline_within_days)).isoformat())
    if query.min_confidence is not None:
        clauses.append("d.confidence >= ?")
        params.append(query.min_confidence)
    return (" WHERE " + " AND ".join(clauses)) if clauses else "", params


def list_documents(conn: sqlite3.Connection, query: DocumentQuery) -> DocumentList:
    clause, params = where(query)
    column = SORT_COLUMNS[query.sort]
    direction = "ASC" if query.order == "asc" else "DESC"
    rows = conn.execute(
        f"{repo.SUMMARY_SQL}{clause} ORDER BY ({column}) IS NULL, {column} {direction}, d.id {direction} LIMIT ? OFFSET ?",
        (*params, query.limit, query.offset),
    ).fetchall()
    total = conn.execute(f"SELECT COUNT(*) FROM documents d{clause}", params).fetchone()[0]
    by_type = conn.execute(
        f"SELECT d.doc_type, COUNT(*) FROM documents d{clause}{' AND' if clause else ' WHERE'} d.doc_type IS NOT NULL "
        "GROUP BY d.doc_type ORDER BY COUNT(*) DESC, d.doc_type",
        params,
    ).fetchall()
    by_status = conn.execute(
        f"SELECT d.status, COUNT(*) FROM documents d{clause} GROUP BY d.status ORDER BY COUNT(*) DESC, d.status", params
    ).fetchall()
    return DocumentList(
        items=[repo.summary_from_row(r) for r in rows],
        total=total,
        by_type=[TypeCount(doc_type=t, count=n) for t, n in by_type],
        by_status=[StatusCount(status=s, count=n) for s, n in by_status],
    )
