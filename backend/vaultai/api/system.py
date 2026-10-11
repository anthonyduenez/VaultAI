from __future__ import annotations

import ipaddress
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Query, Response

from vaultai import deletion, repo
from vaultai.api import Conn, Ctx, start_queue, stop_queue
from vaultai.capture.scanner import backend_names
from vaultai.dashboard import load_dashboard
from vaultai.domain import (
    APP_VERSION,
    SCHEMA_VERSION,
    AuditEntry,
    Dashboard,
    DeleteAllRequest,
    Metrics,
    SystemStatus,
)
from vaultai.metrics import compute_metrics
from vaultai.parsing.ocr import ocr_status

router = APIRouter()


def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


@router.get("/system/status", response_model=SystemStatus)
def system_status(ctx: Ctx) -> SystemStatus:
    s = ctx.settings
    ocr_available, ocr_engine = ocr_status(s)
    return SystemStatus(
        app_version=APP_VERSION,
        schema_version=SCHEMA_VERSION,
        local_only=_is_loopback(s.host),
        egress_guard=s.egress_guard,
        model=ctx.model.status(),
        ocr_available=ocr_available,
        ocr_engine=ocr_engine,
        phone_enabled=s.phone_enabled,
        scanner_backends=backend_names(s),
        data_dir=str(s.data_dir),
        auto_approve_types=sorted(s.auto_approve_types),
    )


@router.get("/system/metrics", response_model=Metrics)
def system_metrics(ctx: Ctx) -> Metrics:
    return compute_metrics(ctx)


@router.get("/dashboard", response_model=Dashboard)
def dashboard(conn: Conn) -> Dashboard:
    return load_dashboard(conn, today=date.today())


@router.get("/audit", response_model=list[AuditEntry])
def audit_log(
    conn: Conn,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    doc_id: Annotated[str | None, Query(max_length=100)] = None,
) -> list[AuditEntry]:
    return repo.load_audit(conn, doc_id=doc_id or None, limit=limit)


@router.delete("/data", status_code=204)
async def delete_all_data(body: DeleteAllRequest, ctx: Ctx) -> Response:
    restart = ctx.queue is not None
    await stop_queue(ctx)
    try:
        deletion.delete_all(ctx, actor=ctx.settings.reviewer_name)
    finally:
        if restart:
            await start_queue(ctx)
    return Response(status_code=204)

