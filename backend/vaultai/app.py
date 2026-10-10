"""Desktop application: loopback-only API, the built frontend, and the background workers."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, closing
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse

from vaultai import db, repo
from vaultai.api import actions, documents, exports, intake, phone, review, scanner, start_queue, stop_queue, system
from vaultai.capture.scanner import build_scanner
from vaultai.config import Settings, load_settings
from vaultai.context import AppContext
from vaultai.domain import APP_VERSION
from vaultai.model.base import ModelAdapter
from vaultai.security.http import install_desktop_guard, install_error_handlers
from vaultai.storage import Storage

ROUTERS = (system, intake, documents, review, actions, exports, scanner, phone)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    ctx: AppContext = app.state.ctx
    if ctx.settings.egress_guard:
        from vaultai.security.egress import install_egress_guard

        install_egress_guard()
    await start_queue(app)
    stop_phone = None
    if ctx.settings.phone_enabled:
        from vaultai.phone_app import start_listener

        stop_phone = start_listener(ctx)
    try:
        yield
    finally:
        if stop_phone is not None:
            stop_phone()
        await stop_queue(app)


def _mount_frontend(app: FastAPI, dist: Path) -> None:
    if not (dist / "index.html").is_file():
        return
    root = dist.resolve()

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> FileResponse:
        if path == "api" or path.startswith("api/"):
            raise repo.not_found()
        target = (root / path).resolve()
        if path and target.is_file() and root in target.parents:
            return FileResponse(target)
        return FileResponse(root / "index.html")


def create_app(settings: Settings | None = None, *, model: ModelAdapter | None = None) -> FastAPI:
    settings = settings or load_settings()
    storage = Storage(settings)
    storage.ensure_dirs()
    with closing(db.connect(settings.db_path)) as conn:
        db.init_db(conn)
    if model is None:
        from vaultai.model.factory import build_model

        model = build_model(settings)

    app = FastAPI(title="VaultAI", version=APP_VERSION, lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.ctx = AppContext(settings=settings, storage=storage, model=model)
    app.state.scanner = build_scanner(settings)
    app.state.job_queue = None
    install_error_handlers(app)
    install_desktop_guard(app, settings)
    for module in ROUTERS:
        app.include_router(module.router, prefix="/api")
    _mount_frontend(app, settings.frontend_dist)
    return app
