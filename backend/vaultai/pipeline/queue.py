"""In-process job queue: asyncio workers that run process_document in threads."""

from __future__ import annotations

import asyncio
import threading

from vaultai.context import AppContext
from vaultai.domain import DocStatus
from vaultai.logging_utils import get_logger
from vaultai.pipeline.process import process_document

log = get_logger("vaultai.queue")


class JobQueue:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx
        self._lock = threading.Lock()
        self._pending: set[str] = set()
        self._backlog: list[str] = []
        self._loop: asyncio.AbstractEventLoop | None = None
        self._queue: asyncio.Queue[str] | None = None
        self._workers: list[asyncio.Task[None]] = []

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._queue = asyncio.Queue()
        self._workers = [asyncio.create_task(self._work()) for _ in range(max(1, self.ctx.settings.workers))]
        with self._lock:
            backlog, self._backlog = self._backlog, []
        for doc_id in backlog:
            self._queue.put_nowait(doc_id)
        with self.ctx.db() as conn:
            stuck = [r["id"] for r in conn.execute(
                "SELECT id FROM documents WHERE status = ? ORDER BY created_at", (DocStatus.PROCESSING.value,))]
        for doc_id in stuck:
            self.enqueue(doc_id)
        if stuck:
            log.info("re-queued %d documents left in processing", len(stuck))

    async def stop(self) -> None:
        for w in self._workers:
            w.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers = []
        self._loop = None

    def enqueue(self, doc_id: str) -> None:
        with self._lock:
            if doc_id in self._pending:
                return
            self._pending.add(doc_id)
            if self._loop is None or self._queue is None:
                self._backlog.append(doc_id)
                return
            loop, queue = self._loop, self._queue
        try:
            on_loop = asyncio.get_running_loop() is loop
        except RuntimeError:
            on_loop = False
        if on_loop:
            queue.put_nowait(doc_id)
        else:
            loop.call_soon_threadsafe(queue.put_nowait, doc_id)

    async def join(self) -> None:
        """Wait until every queued document has been processed."""
        if self._queue is not None:
            await self._queue.join()

    async def _work(self) -> None:
        assert self._queue is not None
        while True:
            doc_id = await self._queue.get()
            with self._lock:
                self._pending.discard(doc_id)
            try:
                await asyncio.to_thread(process_document, self.ctx, doc_id)
            except Exception as exc:
                log.warning("worker error doc=%s error=%s", doc_id, type(exc).__name__)
            finally:
                self._queue.task_done()
