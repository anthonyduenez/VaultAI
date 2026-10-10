"""Deterministic stand-in for a local model. Runs no language model at all.

Classification and extraction add nothing beyond the rules. Analysis returns a
template summary that only restates verified facts, each claim citing the line
its fact came from.
"""

from __future__ import annotations

from vaultai.domain import ModelKind, ModelStatus
from vaultai.model.base import AnalyzeTask, ClassifyTask, ExtractTask, Task, TranscribeTask
from vaultai.model.grounding import CITATION

MAX_CLAIMS = 6


class MockModel:
    kind = ModelKind.MOCK
    name = "mock-rules"
    vision = False

    def status(self) -> ModelStatus:
        return ModelStatus(
            kind=self.kind, name=self.name, endpoint=None, reachable=True, vision=False,
            detail="Mock mode: deterministic rules only. No language model is running.",
        )

    def run(self, task: Task) -> object:
        if isinstance(task, ClassifyTask):
            return {"doc_type": None, "confidence": 0.0}
        if isinstance(task, ExtractTask):
            return {"fields": []}
        if isinstance(task, TranscribeTask):
            return {"lines": []}
        assert isinstance(task, AnalyzeTask)
        claims = []
        for key, value in list(task.known_facts.items())[:MAX_CLAIMS]:
            ids = CITATION.findall(key)
            label = CITATION.sub("", key).strip()
            if ids:
                claims.append({"text": f"{label}: {value}.", "line_ids": ids})
        return {"claims": claims, "obligations": []}
