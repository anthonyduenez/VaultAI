from __future__ import annotations

from fastapi import APIRouter, Request, Response
from fastapi.responses import FileResponse
from pydantic import Field
from starlette.concurrency import run_in_threadpool

from vaultai import db, repo
from vaultai.api import Conn, Ctx
from vaultai.capture import scanner
from vaultai.capture.scanner import ScannerBackend
from vaultai.domain import ApiModel, AssembleRequest, CaptureSession, IntakeItem, ScannerDevice, ScanRequest

router = APIRouter()


class NewScanSession(ApiModel):
    device_id: str = Field(min_length=1, max_length=300)


def _scanner(request: Request) -> ScannerBackend | None:
    return request.app.state.scanner


@router.get("/scanner/devices", response_model=list[ScannerDevice])
async def list_devices(request: Request) -> list[ScannerDevice]:
    backend = _scanner(request)
    return [] if backend is None else await run_in_threadpool(backend.devices)


@router.post("/scanner/sessions", response_model=CaptureSession)
def create_session(body: NewScanSession, request: Request, conn: Conn) -> CaptureSession:
    session_id = scanner.create_session(conn, _scanner(request), body.device_id, now=db.now())
    return repo.get_capture_session(conn, session_id)


@router.get("/scanner/sessions/{session_id}", response_model=CaptureSession)
def get_session(session_id: str, conn: Conn) -> CaptureSession:
    return repo.get_capture_session(conn, session_id)


@router.post("/scanner/sessions/{session_id}/scan", response_model=CaptureSession)
def scan(session_id: str, body: ScanRequest, request: Request, ctx: Ctx, conn: Conn) -> CaptureSession:
    scanner.scan_into(ctx, _scanner(request), session_id, body, now=db.now())
    return repo.get_capture_session(conn, session_id)


@router.post("/scanner/sessions/{session_id}/pages/{page_id}/retake", response_model=CaptureSession)
def retake(session_id: str, page_id: int, body: ScanRequest, request: Request, ctx: Ctx, conn: Conn) -> CaptureSession:
    scanner.retake(ctx, _scanner(request), session_id, page_id, body)
    return repo.get_capture_session(conn, session_id)


@router.delete("/scanner/sessions/{session_id}/pages/{page_id}", response_model=CaptureSession)
def delete_page(session_id: str, page_id: int, ctx: Ctx, conn: Conn) -> CaptureSession:
    scanner.delete_page(ctx, conn, session_id, page_id)
    return repo.get_capture_session(conn, session_id)


@router.get("/scanner/sessions/{session_id}/pages/{page_id}/image")
def page_image(session_id: str, page_id: int, ctx: Ctx, conn: Conn) -> FileResponse:
    return FileResponse(scanner.page_path(ctx, conn, session_id, page_id), media_type="image/png")


@router.post("/scanner/sessions/{session_id}/assemble", response_model=IntakeItem)
def assemble(session_id: str, body: AssembleRequest, ctx: Ctx) -> IntakeItem:
    return scanner.assemble(ctx, session_id, body, now=db.now())


@router.delete("/scanner/sessions/{session_id}", status_code=204)
def cancel(session_id: str, ctx: Ctx, conn: Conn) -> Response:
    scanner.cancel(ctx, conn, session_id)
    return Response(status_code=204)
