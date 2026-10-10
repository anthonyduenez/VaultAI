from __future__ import annotations

from pathlib import Path

import pytest

from vaultai import db
from vaultai.config import REPO_ROOT, Settings
from vaultai.context import AppContext
from vaultai.domain import ModelKind, ModelStatus
from vaultai.storage import Storage

SAMPLES = REPO_ROOT / "samples"


class NullModel:
    """Adapter stand-in for tests that never reach the model."""

    kind = ModelKind.MOCK
    name = "null"
    vision = False

    def status(self) -> ModelStatus:
        return ModelStatus(kind=self.kind, name=self.name, reachable=True, vision=False, detail="test stub")

    def run(self, task: object) -> object:
        return {}


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path / "data", ocr_engine="none", scanner_backend="mock", egress_guard=False)


def make_ctx(settings: Settings, model: object | None = None) -> AppContext:
    storage = Storage(settings)
    storage.ensure_dirs()
    conn = db.connect(settings.db_path)
    db.init_db(conn)
    conn.close()
    return AppContext(settings=settings, storage=storage, model=model or NullModel())


@pytest.fixture
def ctx(settings: Settings) -> AppContext:
    return make_ctx(settings)
