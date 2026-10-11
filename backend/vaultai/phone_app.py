"""The LAN-only phone listener. It serves the capture page and two token-guarded endpoints, nothing else."""

from __future__ import annotations

import secrets
import threading
from collections.abc import Callable
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile

from vaultai import db
from vaultai.capture import phone
from vaultai.config import ConfigError, is_local_host
from vaultai.context import AppContext
from vaultai.domain import IntakeItem
from vaultai.repo import Problem
from vaultai.security.http import install_error_handlers, install_phone_guard, read_capped, read_form

MOBILE_HTML = Path(__file__).with_name("capture").joinpath("mobile.html").read_text(encoding="utf-8")


def mobile_csp(nonce: str) -> str:
    return (
        f"default-src 'none'; script-src 'nonce-{nonce}'; style-src 'nonce-{nonce}'; img-src blob: data:; "
        "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    )


def create_phone_app(ctx: AppContext) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    install_error_handlers(app)
    install_phone_guard(app)
    settings = ctx.settings

    @app.get("/m/{session_id}", response_class=HTMLResponse)
    def mobile_page(session_id: str) -> HTMLResponse:
        """A closed or unknown session gets the same page with 404; its status check then shows the expired state."""
        try:
            with ctx.db() as conn:
                phone.open_session(conn, session_id, now=db.now())
            status = 200
        except Problem:
            status = 404
        nonce = secrets.token_urlsafe(18)
        return HTMLResponse(
            MOBILE_HTML.replace("{{NONCE}}", nonce), status_code=status,
            headers={"content-security-policy": mobile_csp(nonce)},
        )

    @app.get("/p/{session_id}/status")
    def session_status(session_id: str, request: Request) -> JSONResponse:
        with ctx.db() as conn:
            row = phone.verify(conn, session_id, request.headers.get("x-pairing-token"), now=db.now())
        return JSONResponse(phone.status_payload(row, settings))

    @app.post("/p/{session_id}/submit", response_model=IntakeItem)
    async def submit(session_id: str, request: Request) -> IntakeItem:
        token = request.headers.get("x-pairing-token")

        def check() -> None:
            with ctx.db() as conn:
                phone.verify(conn, session_id, token, now=db.now())

        await run_in_threadpool(check)
        limit = settings.phone_max_pages
        form = await read_form(
            request, max_files=limit, too_many=Problem(422, "too_many_pages", f"Send at most {limit} pages.")
        )
        try:
            files = [f for f in form.getlist("pages") if isinstance(f, UploadFile)]
            pages = [await read_capped(f, settings.phone_max_image_mb * 1024 * 1024) for f in files]
            edits = form.get("edits")
            return await run_in_threadpool(
                phone.submit, ctx, session_id, token, pages, edits if isinstance(edits, str) else None, now=db.now()
            )
        finally:
            await form.close()

    return app


def start_listener(ctx: AppContext) -> Callable[[], None]:
    """Serve the phone app on the one LAN address the QR code names; returns the function that stops it.

    Refuses to start when that address is not loopback, private, or link-local.
    """
    host = phone.lan_ip(ctx.settings)
    if not is_local_host(host):
        raise ConfigError(f"phone capture needs a private LAN address, not {host}; set VAULTAI_PHONE_HOST")
    config = uvicorn.Config(
        create_phone_app(ctx), host=host, port=ctx.settings.phone_port, log_level="warning",
        lifespan="off", proxy_headers=False, server_header=False, access_log=False,
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="vaultai-phone", daemon=True)
    thread.start()

    def stop() -> None:
        server.should_exit = True
        thread.join(timeout=5)

    return stop
