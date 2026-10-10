from __future__ import annotations

from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Query, Response
from fastapi.responses import FileResponse

from vaultai import db, deletion, repo, review
from vaultai.api import Conn, Ctx
from vaultai.domain import (
    DocStatus,
    DocType,
    DocumentDetail,
    DocumentList,
    DocumentSummary,
    FieldEdit,
    Obligation,
    ObligationUpdate,
    ReviewRequest,
)
from vaultai.repo import Problem
from vaultai.search import DocumentQuery, SortKey, list_documents

router = APIRouter()


@router.get("/documents", response_model=DocumentList)
def search_documents(
    conn: Conn,
    q: Annotated[str | None, Query(max_length=200)] = None,
    doc_type: Annotated[list[DocType], Query()] = [],
    status: Annotated[list[DocStatus], Query()] = [],
    date_from: date | None = None,
    date_to: date | None = None,
    party: Annotated[str | None, Query(max_length=200)] = None,
    deadline_within_days: Annotated[int | None, Query(ge=0, le=3650)] = None,
    min_confidence: Annotated[float | None, Query(ge=0, le=1)] = None,
    sort: SortKey = "created_at",
    order: Literal["asc", "desc"] = "desc",
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> DocumentList:
    query = DocumentQuery(
        q=q, doc_types=tuple(doc_type), statuses=tuple(status), date_from=date_from, date_to=date_to, party=party,
        deadline_within_days=deadline_within_days, min_confidence=min_confidence, sort=sort, order=order,
        limit=limit, offset=offset,
    )
    return list_documents(conn, query)


@router.get("/documents/{doc_id}", response_model=DocumentDetail)
def get_document(doc_id: str, conn: Conn) -> DocumentDetail:
    return repo.get_detail(conn, doc_id)


@router.get("/documents/{doc_id}/pages/{page_no}/image")
def page_image(doc_id: str, page_no: int, ctx: Ctx, conn: Conn) -> FileResponse:
    row = conn.execute("SELECT image_name FROM pages WHERE doc_id = ? AND page_no = ?", (doc_id, page_no)).fetchone()
    if row is None or row["image_name"] is None:
        raise repo.not_found()
    path = ctx.storage.page_image_path(doc_id, row["image_name"])
    if not path.is_file():
        raise repo.not_found()
    return FileResponse(path, media_type="image/png")


@router.get("/documents/{doc_id}/original")
def original(doc_id: str, ctx: Ctx, conn: Conn) -> FileResponse:
    row = repo.doc_row(conn, doc_id)
    path = ctx.storage.raw_path(row["stored_name"])
    if not path.is_file():
        raise repo.not_found()
    return FileResponse(path, media_type=row["mime"], filename=row["filename"], content_disposition_type="attachment")


@router.patch("/documents/{doc_id}/fields/{key}", response_model=DocumentDetail)
def edit_field(doc_id: str, key: str, body: FieldEdit, ctx: Ctx, conn: Conn) -> DocumentDetail:
    with db.transaction(conn):
        review.edit_field(ctx, conn, doc_id, key, body, actor=ctx.settings.reviewer_name, now=db.now())
    return repo.get_detail(conn, doc_id)


@router.post("/documents/{doc_id}/review", response_model=DocumentDetail)
def review_document(doc_id: str, body: ReviewRequest, ctx: Ctx, conn: Conn) -> DocumentDetail:
    with db.transaction(conn):
        review.apply_review(conn, doc_id, body.action, body.note, actor=ctx.settings.reviewer_name, now=db.now())
    return repo.get_detail(conn, doc_id)


@router.post("/documents/{doc_id}/reprocess", response_model=DocumentSummary)
def reprocess(doc_id: str, ctx: Ctx, conn: Conn) -> DocumentSummary:
    from vaultai.pipeline.process import request_processing

    repo.doc_row(conn, doc_id)
    if not request_processing(ctx, [doc_id], actor=ctx.settings.reviewer_name, reprocess=True):
        raise Problem(409, "invalid_transition", "This document cannot be reprocessed in its current status.")
    return repo.get_summary(conn, doc_id)


@router.delete("/documents/{doc_id}", status_code=204)
def delete_document(doc_id: str, ctx: Ctx, conn: Conn) -> Response:
    deletion.delete_document(ctx, conn, doc_id, actor=ctx.settings.reviewer_name)
    return Response(status_code=204)


@router.patch("/obligations/{obligation_id}", response_model=Obligation)
def update_obligation(obligation_id: int, body: ObligationUpdate, ctx: Ctx, conn: Conn) -> Obligation:
    with db.transaction(conn):
        review.update_obligation(conn, obligation_id, body.status, actor=ctx.settings.reviewer_name, now=db.now())
    return repo.get_obligation(conn, obligation_id)
