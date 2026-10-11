"""The only shapes model output may take, as pydantic models and as JSON Schema for constrained decoding.

check() is the syntax-and-schema gate the adapter runs before deciding whether one
repair attempt is worth making. Whether a value is supported by the document is
decided later, in vaultai.model.grounding, and is never retried.
"""

from __future__ import annotations

import re
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from vaultai.domain import ObligationKind
from vaultai.model.base import AnalyzeTask, ClassifyTask, ExtractTask, Task, TranscribeTask

MAX_FIELDS = 64
MAX_TEXT = 500
MAX_ID = 40


class _Out(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=False)


class ClassifyOut(_Out):
    doc_type: str | None = Field(default=None, max_length=40)
    confidence: float = Field(default=0.0, ge=0, le=1)


class FieldOut(_Out):
    key: str = Field(max_length=64)
    value: str | None = Field(default=None, max_length=MAX_TEXT)
    line_id: str | None = Field(default=None, max_length=MAX_ID)
    quote: str | None = Field(default=None, max_length=MAX_TEXT)
    confidence: float | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def _evidence_when_valued(self) -> FieldOut:
        if self.value is not None and (not self.line_id or not self.quote or self.confidence is None):
            raise ValueError("a value needs line_id, quote, and confidence")
        return self


class ExtractOut(_Out):
    fields: list[FieldOut] = Field(default_factory=list, max_length=MAX_FIELDS)


class ClaimOut(_Out):
    text: str = Field(min_length=1, max_length=400)
    line_ids: list[Annotated[str, Field(max_length=MAX_ID)]] = Field(default_factory=list, max_length=8)


class ObligationOut(_Out):
    """One duty. `end_line_id` is set only when the quote continues onto the next line or two; replies cached before
    `deadline_text` and `end_line_id` existed still validate."""

    kind: ObligationKind
    party: str | None = Field(default=None, max_length=120)
    description: str = Field(min_length=1, max_length=400)
    deadline_text: str | None = Field(default=None, max_length=200)
    due_date: str | None = Field(default=None, max_length=40)
    line_id: str = Field(max_length=MAX_ID)
    end_line_id: str | None = Field(default=None, max_length=MAX_ID)
    quote: str = Field(min_length=1, max_length=MAX_TEXT)


class AnalyzeOut(_Out):
    claims: list[ClaimOut] = Field(default_factory=list, max_length=20)
    obligations: list[ObligationOut] = Field(default_factory=list, max_length=20)


class TranscribeOut(_Out):
    lines: list[Annotated[str, Field(max_length=MAX_TEXT)]] = Field(default_factory=list, max_length=300)


OUTPUTS: dict[type, type[_Out]] = {
    ClassifyTask: ClassifyOut, ExtractTask: ExtractOut, AnalyzeTask: AnalyzeOut, TranscribeTask: TranscribeOut,
}


def _nullable(schema: dict) -> dict:
    return {"anyOf": [schema, {"type": "null"}]}


def _object(properties: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": properties, "required": required or list(properties),
            "additionalProperties": False}


def json_schema(task: Task) -> dict:
    """JSON Schema for the server's constrained decoder. Kept to keywords every vLLM backend supports."""
    if isinstance(task, ClassifyTask):
        return _object({"doc_type": _nullable({"type": "string", "enum": [t.value for t in task.labels]}),
                        "confidence": {"type": "number"}})
    if isinstance(task, ExtractTask):
        item = _object({
            "key": {"type": "string", **({"enum": [f.key for f in task.fields]} if task.fields else {})},
            "value": _nullable({"type": "string"}),
            "line_id": _nullable({"type": "string"}),
            "quote": _nullable({"type": "string"}),
            "confidence": _nullable({"type": "number"}),
        })
        return _object({"fields": {"type": "array", "items": item}})
    if isinstance(task, AnalyzeTask):
        claim = _object({"text": {"type": "string"}, "line_ids": {"type": "array", "items": {"type": "string"}}})
        obligation = _object({
            "kind": {"type": "string", "enum": [k.value for k in ObligationKind]},
            "party": _nullable({"type": "string"}),
            "description": {"type": "string"},
            "deadline_text": _nullable({"type": "string"}),
            "due_date": _nullable({"type": "string"}),
            "line_id": {"type": "string"},
            "end_line_id": _nullable({"type": "string"}),
            "quote": {"type": "string"},
        })
        return _object({"claims": {"type": "array", "items": claim},
                        "obligations": {"type": "array", "items": obligation}})
    return _object({"lines": {"type": "array", "items": {"type": "string"}}})


def _part(p: object) -> str:
    """A schema path element; a key the model invented is shown as "?" so its text never travels further."""
    return str(p) if isinstance(p, int) or (isinstance(p, str) and re.fullmatch(r"[a-z_]{1,32}", p)) else "?"


def parse(task: Task, raw: object) -> _Out | None:
    try:
        return OUTPUTS[type(task)].model_validate(raw)
    except ValidationError:
        return None


def check(task: Task, raw: object) -> tuple[_Out | None, str | None]:
    """(parsed output, None) or (None, a problem description naming schema paths only, never document text)."""
    if not isinstance(raw, dict):
        return None, "it was not a JSON object"
    try:
        return OUTPUTS[type(task)].model_validate(raw), None
    except ValidationError as exc:
        where = "; ".join(f"{'.'.join(map(_part, err['loc'])) or 'top level'}: {err['type']}" for err in exc.errors()[:3])
        return None, f"it did not match the schema ({where})"
