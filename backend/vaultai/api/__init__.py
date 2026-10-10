"""Shared dependencies for the desktop API routers."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, FastAPI, Request

from vaultai.context import AppContext


def get_ctx(request: Request) -> AppContext:
    return request.app.state.ctx


def get_conn(request: Request) -> Iterator[sqlite3.Connection]:
    with get_ctx(request).db() as conn:
        yield conn


Ctx = Annotated[AppContext, Depends(get_ctx)]
Conn = Annotated[sqlite3.Connection, Depends(get_conn)]


async def start_queue(app: FastAPI) -> None:
    from vaultai.pipeline.queue import JobQueue

    ctx: AppContext = app.state.ctx
    queue = JobQueue(ctx)
    await queue.start()
    ctx.queue = queue
    app.state.job_queue = queue


async def stop_queue(app: FastAPI) -> None:
    queue = getattr(app.state, "job_queue", None)
    if queue is not None:
        await queue.stop()
        app.state.job_queue = None
        app.state.ctx.queue = None
