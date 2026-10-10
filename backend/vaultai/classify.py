"""Document type classification from a registry of weighted textual cues.

Cues match against the document's spans, one per line, so `^invoice$` means a
span that reads exactly "INVOICE". Each cue counts once. Confidence grows with
the winning score and with its margin over the runner-up.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable
from dataclasses import dataclass

from vaultai.domain import ClassificationMethod, DocType
from vaultai.parsing.layout import Doc

Cue = tuple[re.Pattern[str] | Callable[[str], bool], float]


def _rx(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.I | re.M)


def _numbered_sections(text: str) -> bool:
    return len(re.findall(r"(?m)^\d{1,2}\.\s+[A-Z]", text)) >= 5


CUES: dict[DocType, tuple[Cue, ...]] = {
    DocType.CHECK: (
        (_rx(r"pay to the order of"), 5),
        (_rx(r"[A⑆]\d{9}[A⑆]\s*[\d -]+[C⑈]\s*\d+"), 4),
        (_rx(r"\band\s+\d{2}/100\b"), 2),
        (_rx(r"^authorized signature$"), 1),
        (_rx(r"^(memo|for):"), 0.5),
    ),
    DocType.RECEIPT: (
        (_rx(r"^(sales )?receipt\b|\breceipt\s*(#|no\b|number)"), 2),
        (_rx(r"^sub-?total\b"), 1),
        (_rx(r"thank you for (shopping|your purchase|visiting|dining)|^thank you!"), 2),
        (_rx(r"\b(visa|mastercard|amex|discover|debit|credit)\b[\s:]*[*x•]{2,}"), 2.5),
        (_rx(r"\b(cashier|register|auth(orization)? code|items sold|change due|store #|terminal id)\b"), 2),
        (_rx(r"\breturns? (accepted|policy|within)\b"), 1),
    ),
    DocType.INVOICE: (
        (_rx(r"^(tax |commercial )?invoice$"), 4),
        (_rx(r"\binvoice\s*(number|no\.?|#|date)"), 3),
        (_rx(r"^bill(ed)? to\b"), 2),
        (_rx(r"\b(amount|total|balance) due\b"), 1.5),
        (_rx(r"\bremit(tance)?\b"), 1),
        (_rx(r"^due date\b"), 1),
    ),
    DocType.PURCHASE_ORDER: (
        (_rx(r"^purchase order$"), 5),
        (_rx(r"\b(po|p\.o\.|purchase order)\s*(number|no\.?|#)"), 1.5),
        (_rx(r"^ship to\b"), 1),
        (_rx(r"\b(order date|delivery date|deliver by|required by)\b"), 2),
        (_rx(r"^(vendor|supplier):"), 1.5),
        (_rx(r"\bauthorized by\b"), 0.5),
    ),
    DocType.PROPOSAL: (
        (_rx(r"^(quote|quotation|proposal|estimate|price quote)$"), 4),
        (_rx(r"\b(quote|quotation|proposal|estimate)\s*(number|no\.?|#)"), 3),
        (_rx(r"\bvalid (until|through|for)\b"), 2),
        (_rx(r"^prepared for\b"), 1.5),
        (_rx(r"\bwritten approval\b|\baccept(ance of)? this (quote|proposal)\b"), 1),
    ),
    DocType.CONTRACT: (
        (_rx(r"^[a-z ,&-]*\bagreement$"), 4),
        (_rx(r"\bwhereas\b"), 2),
        (_rx(r"\bin witness whereof\b"), 3),
        (_rx(r"\bgoverned by the laws\b|\bgoverning law\b"), 2),
        (_rx(r"\bindemnif"), 1.5),
        (_rx(r"\bentered into\b"), 1),
        (_rx(r"\blimitation of liability\b|\bconfidential information\b"), 1.5),
        (_numbered_sections, 2),
    ),
    DocType.STATEMENT_OF_WORK: (
        (_rx(r"\bstatement of work\b"), 5),
        (_rx(r"\(\W?sow\W?\)|\bsow\b"), 1.5),
        (_rx(r"\bdeliverables?\b"), 1.5),
        (_rx(r"\bmilestones?\b"), 1),
        (_rx(r"\b(scope|acceptance criteria)\b"), 0.5),
    ),
    DocType.AGREEMENT_LETTER: (
        (_rx(r"^dear\b"), 2),
        (_rx(r"^(sincerely|regards|best regards|very truly yours|yours truly),?$"), 2),
        (_rx(r"^re:"), 1),
        (_rx(r"\bthis letter confirms\b|\bengagement letter\b|\boffer letter\b|\bletter agreement\b"), 3),
        (_rx(r"\baccepted and agreed\b|\bindicate your acceptance\b|\bsign(ing)? (below|and return)\b"), 3),
        (_rx(r"\bagree(s|d)?\b"), 0.5),
    ),
    DocType.POLICY: (
        (_rx(r"^[a-z ,&-]*\bpolicy$"), 4),
        (_rx(r"\bpolicy\s*(number|no\.?|#)"), 3),
        (_rx(r"\b(next review|review date|policy owner)\b"), 2),
        (_rx(r"\bthis policy (applies|sets|establishes|describes)\b"), 2),
        (_rx(r"^version:?"), 1),
    ),
    DocType.ADMIN_FORM: (
        (_rx(r"^[a-z ]*\bform$"), 3),
        (_rx(r"^form [a-z0-9-]+"), 2),
        (_rx(r"\b(complete (every|all|each) (field|section)|please print|for office use only)\b"), 2),
        (_rx(r"^section \d+\s*[-–:]"), 1.5),
        (_rx(r"\bi (hereby )?certify\b"), 1.5),
        (_rx(r"\b(legal business name|ein|tax id|date of birth|applicant)\b"), 1),
    ),
    DocType.CORRESPONDENCE: (
        (_rx(r"^(internal )?(memorandum|memo)$"), 4),
        (_rx(r"^from:"), 1.5),
        (_rx(r"^to:"), 1),
        (_rx(r"^subject:"), 1.5),
        (_rx(r"^cc:"), 0.5),
        (_rx(r"^dear\b"), 1),
        (_rx(r"^(sincerely|regards|best regards),?$"), 1),
    ),
    DocType.OTHER: (),
}

MIN_SCORE = 1.5
MODEL_BELOW_CONFIDENCE = 0.6
MODEL_BELOW_MARGIN = 1.5
MODEL_LINES = 60


@dataclass(frozen=True)
class Classification:
    doc_type: DocType
    confidence: float
    method: ClassificationMethod
    margin: float = math.inf

    @property
    def uncertain(self) -> bool:
        return self.confidence < MODEL_BELOW_CONFIDENCE or self.margin < MODEL_BELOW_MARGIN


def span_text(doc: Doc) -> str:
    return "\n".join(s.text.strip() for ln in doc.visible for s in ln.spans)


def scores(text: str) -> dict[DocType, float]:
    return {
        t: sum(w for cue, w in cues if (cue(text) if callable(cue) else cue.search(text)))
        for t, cues in CUES.items()
    }


def classify_rules(doc: Doc) -> Classification:
    ranked = sorted(scores(span_text(doc)).items(), key=lambda kv: kv[1], reverse=True)
    (best, s1), (_, s2) = ranked[0], ranked[1]
    if s1 < MIN_SCORE:
        return Classification(DocType.OTHER, 0.5, ClassificationMethod.RULES, 0.0)
    confidence = (1 - math.exp(-s1 / 3)) * (0.5 + 0.5 * math.tanh((s1 - s2) / 2))
    return Classification(best, round(confidence, 3), ClassificationMethod.RULES, s1 - s2)


def from_hint(hint: DocType) -> Classification:
    return Classification(hint, 1.0, ClassificationMethod.USER)
