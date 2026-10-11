"""Validation issues and the one table that decides how severe each kind is."""

from __future__ import annotations

from dataclasses import dataclass

from vaultai.domain import Evidence, IssueCode, Severity

_E, _W, _I = Severity.ERROR, Severity.WARNING, Severity.INFO
SEVERITY: dict[IssueCode, Severity] = {
    IssueCode.MISSING_REQUIRED: _E,
    IssueCode.LOW_OCR_CONFIDENCE: _W,
    IssueCode.LOW_EXTRACTION_CONFIDENCE: _W,
    IssueCode.UNREADABLE: _E,
    IssueCode.INVALID_DATE: _E,
    IssueCode.DATE_ORDER: _E,
    IssueCode.CONFLICTING_VALUES: _E,
    IssueCode.AMOUNT_MISMATCH: _E,
    IssueCode.WRITTEN_AMOUNT_MISMATCH: _E,
    IssueCode.TAX_INCONSISTENT: _E,
    IssueCode.CURRENCY_MISMATCH: _E,
    IssueCode.UNUSUAL_AMOUNT: _W,
    IssueCode.INVALID_IDENTIFIER: _E,
    IssueCode.DUPLICATE_DOCUMENT: _W,
    IssueCode.MISSING_SIGNATURE: _W,
    IssueCode.AUTO_RENEWAL: _W,
    IssueCode.RISKY_CLAUSE: _W,
    IssueCode.AMBIGUOUS_CLAUSE: _W,
    IssueCode.UNUSUAL_OBLIGATION: _W,
    IssueCode.PROMPT_INJECTION: _E,
    IssueCode.HIDDEN_TEXT: _E,
    IssueCode.UNGROUNDED_VALUE: _W,
    IssueCode.MODEL_OUTPUT_REJECTED: _W,
    IssueCode.MODEL_UNAVAILABLE: _W,
    IssueCode.LEGAL_REVIEW_REQUIRED: _I,
}

INJECTION_CODES = frozenset({IssueCode.PROMPT_INJECTION, IssueCode.HIDDEN_TEXT})

PROCESS_CODES = frozenset({IssueCode.MODEL_UNAVAILABLE, IssueCode.MODEL_OUTPUT_REJECTED, IssueCode.UNGROUNDED_VALUE})


@dataclass(frozen=True)
class Issue:
    """`reason` is an optional stable diagnostic code (vaultai.model.diagnostics) for model rejections; never content."""

    code: IssueCode
    message: str
    field_key: str | None = None
    evidence: Evidence | None = None
    reason: str | None = None

    @property
    def severity(self) -> Severity:
        return SEVERITY[self.code]
