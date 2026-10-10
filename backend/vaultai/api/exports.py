from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import FileResponse

from vaultai import db, repo
from vaultai.api import Conn, Ctx
from vaultai.domain import ExportFilters, ExportFormat, ExportJob, ExportPreview
from vaultai.export import service

router = APIRouter()


@router.post("/exports/preview", response_model=ExportPreview)
def preview_export(body: ExportFilters, conn: Conn) -> ExportPreview:
    return service.preview(conn, body)


@router.post("/exports", response_model=ExportJob)
def create_export(body: ExportFilters, ctx: Ctx) -> ExportJob:
    return service.create_export(ctx, body, actor=ctx.settings.reviewer_name, now=db.now())


@router.get("/exports", response_model=list[ExportJob])
def list_exports(conn: Conn) -> list[ExportJob]:
    return [repo.export_from_row(r) for r in conn.execute("SELECT * FROM exports ORDER BY created_at DESC, id")]


@router.get("/exports/{export_id}/download")
def download_export(export_id: str, ctx: Ctx, conn: Conn) -> FileResponse:
    row = conn.execute("SELECT * FROM exports WHERE id = ?", (export_id,)).fetchone()
    if row is None:
        raise repo.not_found()
    path = ctx.storage.resolve(ctx.storage.exports, row["stored_name"])
    if not path.is_file():
        raise repo.not_found()
    job = repo.export_from_row(row)
    return FileResponse(
        path, media_type=service.MEDIA_TYPES[ExportFormat(job.format)], filename=job.filename,
        content_disposition_type="attachment",
    )
