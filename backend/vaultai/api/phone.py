from __future__ import annotations

from fastapi import APIRouter, Response

from vaultai import db
from vaultai.api import Conn, Ctx
from vaultai.capture import phone
from vaultai.domain import CaptureSession, PhonePairing
from vaultai.repo import Problem

router = APIRouter()


@router.post("/phone/sessions", response_model=PhonePairing)
def create_pairing(ctx: Ctx, conn: Conn) -> PhonePairing:
    if not ctx.settings.phone_enabled:
        raise Problem(409, "phone_disabled", "Phone capture is off. Set VAULTAI_PHONE_ENABLED=true and restart VaultAI.")
    with db.transaction(conn):
        return phone.create_pairing(ctx, conn, now=db.now())


@router.get("/phone/sessions/{session_id}", response_model=CaptureSession)
def get_pairing(session_id: str, conn: Conn) -> CaptureSession:
    return phone.desktop_view(conn, session_id, now=db.now())


@router.delete("/phone/sessions/{session_id}", status_code=204)
def cancel_pairing(session_id: str, conn: Conn) -> Response:
    phone.cancel(conn, session_id)
    return Response(status_code=204)
