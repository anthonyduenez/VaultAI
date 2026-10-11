"""Records produced by document analysis: clauses with risk, and obligations with deadlines."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from vaultai.domain import ClauseKind, ClauseRisk, Evidence, FieldOrigin, ObligationKind


@dataclass(frozen=True)
class ClauseRec:
    kind: ClauseKind
    heading: str
    text: str
    risk: ClauseRisk
    risk_reason: str | None
    evidence: Evidence | None
    interpretation: str | None = None
    origin: FieldOrigin = FieldOrigin.RULES


@dataclass(frozen=True)
class ObligationRec:
    kind: ObligationKind
    party: str | None
    description: str
    due_date: date | None
    evidence: Evidence | None
    origin: FieldOrigin = FieldOrigin.RULES


@dataclass(frozen=True)
class Analysis:
    clauses: tuple[ClauseRec, ...] = ()
    obligations: tuple[ObligationRec, ...] = ()
