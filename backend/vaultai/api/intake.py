from __future__ import annotations

import secrets

from fastapi import APIRouter, Request, Response
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile

from vaultai import deletion, repo
from vaultai.api import Conn, Ctx
from vaultai.domain import DocStatus, IntakeItem, ProcessRequest, ProcessResult, Source
from vaultai.intake.ingest import ingest_bytes, ingest_samples
from vaultai.pipeline.process import request_processing
from vaultai.repo import Problem
from vaultai.security.http import read_capped, read_form

router = APIRouter()

UPLOAD_SOURCES = {"upload": Source.UPLOAD, "folder": Source.FOLDER}


def new_batch_id() -> str:
    return secrets.token_hex(8)


@router.post("/intake/upload", response_model=list[IntakeItem])
async def upload(request: Request, ctx: Ctx) -> list[IntakeItem]:
    limit = ctx.settings.max_batch_files
    form = await read_form(
        request, max_files=limit, too_many=Problem(422, "too_many_files", f"Upload at most {limit} files at a time.")
    )
    try:
        source = UPLOAD_SOURCES.get(str(form.get("source") or "upload"))
        if source is None:
            raise Problem(422, "invalid_request", "Invalid or missing value: source.")
        files = [f for f in form.getlist("files") if isinstance(f, UploadFile)]
        if not files:
            raise Problem(422, "no_files", "Choose at least one file to upload.")
        batch_id = new_batch_id()
        items: list[IntakeItem] = []
        for upload_file in files:
            data = await read_capped(upload_file, ctx.settings.max_file_bytes)
            items.append(
                await run_in_threadpool(
                    ingest_bytes, ctx, filename=upload_file.filename or "", data=data, source=source,
                    batch_id=batch_id,
                )
            )
        return items
    finally:
        await form.close()


@router.post("/intake/samples", response_model=list[IntakeItem])
def load_samples(ctx: Ctx) -> list[IntakeItem]:
    return ingest_samples(ctx, batch_id=new_batch_id())


@router.get("/intake", response_model=list[IntakeItem])
def list_intake(conn: Conn) -> list[IntakeItem]:
    return repo.list_intake(conn)


@router.delete("/intake/{intake_id}", status_code=204)
def remove_intake(intake_id: int, ctx: Ctx, conn: Conn) -> Response:
    item = repo.get_intake(conn, intake_id)
    if item.document_id is not None and item.doc_status is DocStatus.IMPORTED:
        deletion.delete_document(ctx, conn, item.document_id, actor=ctx.settings.reviewer_name)
    conn.execute("UPDATE intake SET dismissed = 1 WHERE id = ?", (intake_id,))
    return Response(status_code=204)


@router.post("/intake/process", response_model=ProcessResult)
def process(body: ProcessRequest, ctx: Ctx, conn: Conn) -> ProcessResult:
    if body.intake_ids is None:
        rows = conn.execute("SELECT id FROM documents WHERE status = ? ORDER BY created_at, id", (DocStatus.IMPORTED.value,))
    else:
        marks = ",".join("?" * len(body.intake_ids)) or "NULL"
        rows = conn.execute(
            f"SELECT document_id FROM intake WHERE document_id IS NOT NULL AND id IN ({marks}) ORDER BY id",
            body.intake_ids,
        )
    doc_ids = list(dict.fromkeys(r[0] for r in rows))
    return ProcessResult(queued=request_processing(ctx, doc_ids, actor=ctx.settings.reviewer_name))

