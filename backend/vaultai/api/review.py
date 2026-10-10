from __future__ import annotations

from fastapi import APIRouter

from vaultai import db, review
from vaultai.api import Conn, Ctx
from vaultai.domain import BatchReviewRequest, BatchReviewResult, ReviewQueue

router = APIRouter()


@router.get("/review/queue", response_model=ReviewQueue)
def review_queue(conn: Conn) -> ReviewQueue:
    return review.load_review_queue(conn)


@router.post("/review/batch", response_model=BatchReviewResult)
def batch_review(body: BatchReviewRequest, ctx: Ctx) -> BatchReviewResult:
    return review.batch_review(ctx, body, actor=ctx.settings.reviewer_name, now=db.now())
