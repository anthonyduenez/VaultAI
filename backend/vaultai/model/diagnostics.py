"""Stable, privacy-safe codes for why model output was not used, and the counters built from them.

A code names a category, never an instance: no reply text, prompt, quote, value,
or document line is ever attached to one. Counters always carry every known code
(zero when unused), so a metrics record has the same shape whatever happened,
and records written before these counters existed read as all zeros.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from enum import StrEnum


class FailureReason(StrEnum):
    """Why one model attempt was unusable. Decided before grounding; drives the single repair attempt."""

    EMPTY_CONTENT = "empty_content"
    INVALID_JSON = "invalid_json"
    SCHEMA_INVALID = "schema_invalid"
    RESPONSE_TOO_LARGE = "response_too_large"


class GroundingReason(StrEnum):
    """Why a well-formed model answer was discarded by the evidence check. Never retried."""

    INVALID_CITATION = "invalid_citation"
    UNSUPPORTED_BY_DOCUMENT = "unsupported_by_document"
    CONFLICTING_EVIDENCE = "conflicting_evidence"
    WEAK_EVIDENCE = "weak_evidence"
    OBLIGATION_NOT_GROUNDED = "obligation_not_grounded"
    DEADLINE_NOT_GROUNDED = "deadline_not_grounded"
    UNKNOWN_FIELD = "unknown_field"
    OTHER = "other"


def failure_reason(raw: object) -> FailureReason:
    """The code for decoded output that did not validate: nothing, something that is not a JSON object, or the wrong object."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return FailureReason.EMPTY_CONTENT
    if not isinstance(raw, dict):
        return FailureReason.INVALID_JSON
    return FailureReason.SCHEMA_INVALID


def counts(codes: Iterable[str | None], kind: type[StrEnum], fallback: str | None = None) -> dict[str, int]:
    """Every code of `kind` with how often it occurs; anything unrecognized counts as `fallback`, or is ignored."""
    known = {c.value for c in kind}
    tally = Counter(c if c in known else fallback for c in codes)
    return {c.value: tally.get(c.value, 0) for c in kind}


def merge(into: dict[str, int], more: Mapping[str, object] | None, kind: type[StrEnum]) -> dict[str, int]:
    """Add a stored count map onto `into`, keeping only known codes and integer counts."""
    for code in (c.value for c in kind):
        value = (more or {}).get(code, 0)
        into[code] = into.get(code, 0) + (value if isinstance(value, int) and value >= 0 else 0)
    return into


MODEL_MAPS = {
    "failure_reasons": FailureReason,
    "rejections_by_reason": FailureReason,
    "grounding_rejections_by_reason": GroundingReason,
}

# Process-wide totals in the counters table, one row per code: e.g. "model_failure:empty_content".
COUNTER_PREFIXES = {
    "failure_reasons": "model_failure:",
    "rejections_by_reason": "model_rejection:",
    "grounding_rejections_by_reason": "grounding_rejection:",
}


def from_counters(counters: Mapping[str, int], key: str) -> dict[str, int]:
    prefix, kind = COUNTER_PREFIXES[key], MODEL_MAPS[key]
    return {c.value: int(counters.get(prefix + c.value, 0)) for c in kind}


def normalize(metrics: Mapping | None) -> dict:
    """A stored doc_metrics record with every diagnostic count map present; older records gain zero maps."""
    out = dict(metrics or {})
    model = dict(out.get("model") or {})
    for key, kind in MODEL_MAPS.items():
        model[key] = merge({}, model.get(key) if isinstance(model.get(key), Mapping) else None, kind)
    out["model"] = model
    return out
