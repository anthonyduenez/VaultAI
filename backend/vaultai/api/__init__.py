"""Shared dependencies for the desktop API routers."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Request

from vaultai.context import AppContext
from vaultai.pipeline.queue import JobQueue


def get_ctx(request: Request) -> AppContext:
    return request.app.state.ctx


def get_conn(request: Request) -> Iterator[sqlite3.Connection]:
    with get_ctx(request).db() as conn:
        yield conn


Ctx = Annotated[AppContext, Depends(get_ctx)]
Conn = Annotated[sqlite3.Connection, Depends(get_conn)]


async def start_queue(ctx: AppContext) -> None:
    queue = JobQueue(ctx)
    await queue.start()
    ctx.queue = queue


async def stop_queue(ctx: AppContext) -> None:
    if ctx.queue is not None:
        await ctx.queue.stop()
        ctx.queue = None
