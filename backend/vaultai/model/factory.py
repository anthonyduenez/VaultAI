from __future__ import annotations

from vaultai.config import Settings
from vaultai.domain import ModelKind
from vaultai.model.base import ModelAdapter
from vaultai.model.mock import MockModel
from vaultai.model.openai_compat import OpenAICompatModel


def build_model(settings: Settings) -> ModelAdapter:
    if settings.model_backend is ModelKind.OPENAI_COMPAT:
        return OpenAICompatModel(settings)
    return MockModel()
