"""Process-wide dependencies passed explicitly to every service function."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Protocol

from vaultai import db
from vaultai.config import Settings
from vaultai.model.base import ModelAdapter
from vaultai.storage import Storage


class Queue(Protocol):
    def enqueue(self, doc_id: str) -> None: ...

    async def stop(self) -> None: ...


@dataclass
class AppContext:
    settings: Settings
    storage: Storage
    model: ModelAdapter
    queue: Queue | None = field(default=None)

    @contextmanager
    def db(self) -> Iterator[sqlite3.Connection]:
        conn = db.connect(self.settings.db_path)
        try:
            yield conn
        finally:
            conn.close()
