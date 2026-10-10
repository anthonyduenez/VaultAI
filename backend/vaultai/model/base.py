"""The replaceable local-model boundary.

Application code hands an adapter a typed Task and receives raw, untrusted,
JSON-decoded output. Adapters never see the database, the filesystem, or
any tool; they cannot approve, delete, or send anything. Callers validate
every output (vaultai.model.grounding) before anything is persisted.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from vaultai.domain import DocType, ModelKind, ModelStatus
from vaultai.schemas import FieldSpec


@dataclass(frozen=True)
class DocLine:
    """One line of document text as the model sees it. `id` is the citation handle, e.g. "p2.l14"."""

    id: str
    text: str


@dataclass(frozen=True)
class ClassifyTask:
    lines: tuple[DocLine, ...]
    labels: tuple[DocType, ...]


@dataclass(frozen=True)
class ExtractTask:
    doc_type: DocType
    fields: tuple[FieldSpec, ...]
    lines: tuple[DocLine, ...]


@dataclass(frozen=True)
class AnalyzeTask:
    """Summary claims, clause interpretation, obligations, and action items for one chunk of a document."""

    doc_type: DocType
    lines: tuple[DocLine, ...]
    known_facts: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class TranscribeTask:
    """Vision fallback: read the text on one rendered page image."""

    image_png: bytes


Task = ClassifyTask | ExtractTask | AnalyzeTask | TranscribeTask


class ModelUnavailable(RuntimeError):
    """The configured local model could not be reached or answered with an error. Never triggers a fallback."""


class ModelAdapter(Protocol):
    kind: ModelKind
    name: str
    vision: bool

    def status(self) -> ModelStatus: ...

    def run(self, task: Task) -> object:
        """Return raw JSON-decoded output for `task`. Raise ModelUnavailable on transport failure."""
        ...
