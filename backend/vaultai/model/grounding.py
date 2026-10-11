"""The gate between untrusted model output and anything VaultAI keeps.

Output must parse into a closed schema (vaultai.model.output; extra keys such as
"status" or "approve" are rejected outright). Every kept value must cite a line
the model was shown, quote that line exactly (Unicode-normalized, whitespace
collapsed, case kept), and read as its declared type inside the quote: amounts
and dates are re-parsed from the printed text, and text values are stored as
printed, never as the model spelled them. Summary claims must cite a line,
state only amounts and dates printed in what they cite, and give no advice;
other claims are dropped (they change no record, so they raise no issue).
Values are then scored by
vaultai.model.confidence rather than trusted at the model's word, and never
replace a more confident rules value. There is no code path from model output
to a status change, an approval, or a file operation.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date

from vaultai.analysis import ObligationRec
from vaultai.domain import DocType, Evidence, FieldOrigin, IssueCode, ObligationKind, ValueType
from vaultai.extraction import Found, evidence
from vaultai.extraction.normalize import CURRENCY_CODES, find_dates, find_money, money_str
from vaultai.model import confidence, output
from vaultai.model.diagnostics import FailureReason, GroundingReason, failure_reason
from vaultai.model.base import AnalyzeTask, ClassifyTask, ExtractTask, TranscribeTask
from vaultai.parsing.layout import Doc, Line, page_of
from vaultai.validation import Issue

MODEL_CONFIDENCE_CAP = confidence.CAP
EARLIEST_YEAR, LATEST_YEAR = 1900, 2100

REJECTED = "The local model's answer did not match the required format, so none of it was used."
_LINE_ID = re.compile(r"^\s*\[?\s*(p\d{1,4}\.l\d{1,5})(?![\w.])")
_ADVICE = re.compile(
    r"\b(unenforceable|invalid|illegal|unlawful|you should|we recommend|i recommend|recommend(?:ed|s)? that|"
    r"legal advice|consult (?:a|an|your) (?:lawyer|attorney|counsel))\b", re.I)
_CONTENT_WORD = re.compile(r"[a-z0-9][a-z0-9'-]{3,}")


def rejected(reason: FailureReason | str) -> Issue:
    return Issue(IssueCode.MODEL_OUTPUT_REJECTED, REJECTED, reason=str(reason))


def canon(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).split())


# Curly quotes and dashes compare equal to their plain forms; nothing else is relaxed.
_TYPOGRAPHY = str.maketrans({"‘": "'", "’": "'", "‚": "'", "‛": "'", "′": "'", "“": '"', "”": '"', "„": '"',
                             "″": '"', "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "―": "-", "−": "-"})


def _matchable(text: str) -> str:
    return canon(text).translate(_TYPOGRAPHY)


def line_id(raw: str | None) -> str | None:
    """The bare citation handle, accepting the bracketed or annotated forms shown in the prompt ("[p1.l4 ocr 60%]")."""
    m = _LINE_ID.match(raw or "")
    return m.group(1) if m else None


def quote_on_line(doc: Doc, allowed: set[str], cited: str | None, quote: str | None) -> bool:
    if not cited or not quote or not canon(quote) or cited not in allowed or cited not in doc.by_id:
        return False
    return _matchable(quote) in _matchable(doc.by_id[cited].text)


def printed(value: str, quote: str) -> str | None:
    """`value` as it is printed inside `quote`, matched ignoring case and spacing, or None."""
    words = canon(value).split()
    if not words:
        return None
    m = re.search(r"\s+".join(map(re.escape, words)), canon(quote), re.I)
    return m.group() if m else None


def _ungrounded(reason: GroundingReason, label: str, key: str | None = None,
                why: str = "is not supported by the document text") -> Issue:
    return Issue(IssueCode.UNGROUNDED_VALUE, f"The model proposed a {label} that {why}; it was discarded.", key,
                 reason=reason.value)


def _cited(doc: Doc, allowed: set[str], cited: str | None) -> bool:
    return bool(cited) and cited in allowed and cited in doc.by_id


def ground_classify(raw: object, task: ClassifyTask) -> tuple[DocType | None, float, list[Issue]]:
    out = output.parse(task, raw)
    if out is None:
        return None, 0.0, [rejected(failure_reason(raw))]
    if out.doc_type is None:
        return None, 0.0, []
    try:
        doc_type = DocType(out.doc_type.strip().lower())
    except ValueError:
        return None, 0.0, [rejected(FailureReason.SCHEMA_INVALID)]
    if doc_type not in task.labels:
        return None, 0.0, [rejected(FailureReason.SCHEMA_INVALID)]
    return doc_type, min(out.confidence, MODEL_CONFIDENCE_CAP), []


def _date(text: str, date_order: str) -> date | None:
    try:
        wanted = date.fromisoformat(text.strip())
    except ValueError:
        hits = find_dates(text, date_order)
        if len(hits) != 1 or hits[0].value is None:
            return None
        wanted = hits[0].value
    return wanted if EARLIEST_YEAR <= wanted.year <= LATEST_YEAR else None


def typed(value_type: ValueType, value: str, quote: str, options: tuple[str, ...], date_order: str
          ) -> tuple[str, str, str | None] | None:
    """(stored value, printed text, currency) when `value` is a valid `value_type` printed in `quote`, else None."""
    if value_type is ValueType.MONEY:
        stated = find_money(value)
        if len(stated) != 1:
            return None
        hit = next((m for m in find_money(quote) if m.amount == stated[0].amount), None)
        if hit is None or (hit.currency and stated[0].currency and hit.currency != stated[0].currency):
            return None
        currency = hit.currency or stated[0].currency
        if currency is not None and currency not in CURRENCY_CODES:
            return None
        return money_str(hit.amount), hit.raw, currency
    if value_type is ValueType.DATE:
        wanted = _date(value, date_order)
        hit = next((h for h in find_dates(quote, date_order) if wanted and h.value == wanted), None)
        return (wanted.isoformat(), hit.raw, None) if hit else None
    if value_type is ValueType.BOOL:
        v = value.strip().lower()
        return (v, quote, None) if v in {"true", "false"} else None
    if value_type is ValueType.ENUM:
        match = next((o for o in options if o.lower() == value.strip().lower()), None)
        return (match, quote, None) if match else None
    text = printed(value, quote)
    if text is None:
        return None
    if value_type is ValueType.IDENTIFIER:
        text = text.strip(" .,;:")
    return (text, text, None) if re.search(r"[A-Za-z0-9]", text) else None


def ground_extract(raw: object, task: ExtractTask, doc: Doc, existing: Mapping[str, Found], date_order: str,
                   model_name: str) -> tuple[list[Found], list[Issue]]:
    out = output.parse(task, raw)
    if out is None:
        return [], [rejected(failure_reason(raw))]
    specs = {f.key: f for f in task.fields}
    allowed = {ln.id for ln in task.lines}
    issues: list[Issue] = []
    candidates: dict[str, list[tuple[output.FieldOut, str, tuple[str, str, str | None]]]] = {}
    for f in out.fields:
        if f.value is None:
            continue
        spec = specs.get(f.key)
        cited = line_id(f.line_id)
        if spec is None:
            issues.append(_ungrounded(GroundingReason.UNKNOWN_FIELD, "value"))
            continue
        if cited is None or not _cited(doc, allowed, cited):
            reason = GroundingReason.CITATION_MISSING if cited is None else GroundingReason.CITATION_LINE_NOT_SHOWN
            issues.append(_ungrounded(reason, "value", spec.key))
            continue
        if not quote_on_line(doc, allowed, cited, f.quote):
            issues.append(_ungrounded(quote_problem(doc, allowed, [doc.by_id[cited]], f.quote), "value", spec.key))
            continue
        value = typed(spec.value_type, f.value, f.quote or "", spec.options, date_order)
        if value is None:
            issues.append(_ungrounded(GroundingReason.UNSUPPORTED_BY_DOCUMENT, spec.label.lower(), spec.key))
            continue
        candidates.setdefault(spec.key, []).append((f, cited, value))
    kept: list[Found] = []
    for key, found in candidates.items():
        spec = specs[key]
        if len({v[0] for _, _, v in found}) > 1:
            issues.append(_ungrounded(GroundingReason.CONFLICTING_EVIDENCE, spec.label.lower(), key,
                                      "had more than one conflicting answer"))
            continue
        f, cited, (value, raw_text, currency) = found[0]
        line = doc.by_id[cited]
        score, _ = confidence.calibrate(spec, value, f.quote or "", line, doc, existing.get(key), f.confidence)
        if score < confidence.REJECT_BELOW:
            issues.append(_ungrounded(GroundingReason.WEAK_EVIDENCE, spec.label.lower(), key, "rests on weak evidence"))
            continue
        current = existing.get(key)
        if current is not None and current.value is not None and current.confidence >= score:
            continue
        kept.append(Found(key, value, raw_text, score, evidence(line), currency, FieldOrigin.MODEL, model_name))
    return kept, issues


@dataclass(frozen=True)
class Claim:
    text: str
    evidence: tuple[Evidence, ...]


CITATION = re.compile(r"\[(p\d+\.l\d+)\]")


def fact_line_ids(task: AnalyzeTask) -> set[str]:
    return {m for key in task.known_facts for m in CITATION.findall(key)}


def _supported(claim: str, sources: str, date_order: str) -> bool:
    """Every amount and date the claim states is also printed in its sources."""
    amounts = {m.amount for m in find_money(sources)}
    days = {h.value for h in find_dates(sources, date_order) if h.value}
    return (all(m.amount in amounts for m in find_money(claim, require_marker=True))
            and all(h.value in days for h in find_dates(claim, date_order) if h.value))


def _faithful(description: str, source: str) -> bool:
    words = set(_CONTENT_WORD.findall(description.casefold()))
    return bool(words) and len(words & set(_CONTENT_WORD.findall(source.casefold()))) >= 0.6 * len(words)


MAX_SPAN_LINES = 3


def cited_span(doc: Doc, allowed: set[str], start: str | None, end: str | None) -> list[Line] | None:
    """The cited lines from `start` to `end`: next to each other on one page, none quarantined, all shown to the model."""
    if not _cited(doc, allowed, start):
        return None
    if end is None or end == start:
        return [doc.by_id[start]]
    if not _cited(doc, allowed, end) or page_of(start) != page_of(end):
        return None
    lines = doc.page(page_of(start)).lines
    ids = [ln.id for ln in lines]
    i, j = ids.index(start), ids.index(end)
    span = list(lines[i:j + 1])
    if not 0 < j - i < MAX_SPAN_LINES or any(ln.flag or ln.id not in allowed for ln in span):
        return None
    return span


def quote_position(span: list[Line], quote: str | None) -> tuple[str, int, int] | None:
    """(span text, start, end) where the quote is copied exactly: starting on the first line and, for a span, ending
    on the last. Curly quotes and dashes match their plain forms; nothing else is relaxed."""
    wanted = _matchable(quote or "")
    if not wanted:
        return None
    texts = [_matchable(ln.text) for ln in span]
    joined = " ".join(texts)
    first_end, last_start = len(texts[0]), len(joined) - len(texts[-1])
    pos = joined.find(wanted)
    while pos != -1:
        if pos < first_end and (len(span) == 1 or pos + len(wanted) > last_start):
            return joined, pos, pos + len(wanted)
        pos = joined.find(wanted, pos + 1)
    return None


_SENTENCE_END = re.compile(r"[.;!?](?=\s|$)")
_ABBREVIATION = re.compile(r"\b(?:Inc|Co|Corp|Ltd|LLC|L\.L\.C|No|Nos|Mr|Mrs|Ms|Dr|St|Jr|Sr|vs|Sec|U\.S|e\.g|i\.e)\.$", re.I)


def sentence_around(text: str, start: int, end: int) -> str:
    """The sentence of `text` that holds text[start:end], ignoring periods that end common abbreviations."""
    begin = 0
    for m in _SENTENCE_END.finditer(text, 0, start):
        if not _ABBREVIATION.search(text[:m.end()]):
            begin = m.end()
    stop = len(text)
    for m in _SENTENCE_END.finditer(text, end):
        if not _ABBREVIATION.search(text[:m.end()]):
            stop = m.end()
            break
    return text[begin:stop].strip()


def resolve_span(doc: Doc, allowed: set[str], start_raw: str | None, end_raw: str | None
                 ) -> tuple[list[Line] | None, GroundingReason | None]:
    """The cited lines, or the citation code that explains why there are none."""
    start = line_id(start_raw)
    if start is None:
        return None, GroundingReason.CITATION_MISSING
    if not _cited(doc, allowed, start):
        return None, GroundingReason.CITATION_LINE_NOT_SHOWN
    end = line_id(end_raw)
    if end is None or end == start:
        return [doc.by_id[start]], None  # an end the model left empty or wrote unreadably means one line, as before
    if end in doc.by_id and end not in allowed:
        return None, GroundingReason.CITATION_LINE_NOT_SHOWN
    span = cited_span(doc, allowed, start, end)
    return (span, None) if span else (None, GroundingReason.CITATION_NON_ADJACENT)


_MARKUP = re.compile(r"\[\s*p\d+\.l\d+|<<|>>")


def quote_problem(doc: Doc, allowed: set[str], span: list[Line], quote: str | None) -> GroundingReason:
    """How a quote that is not on its cited lines fails, as a code. Nothing about the quote itself is kept."""
    text = canon(quote or "")
    if text and _MARKUP.search(text):
        return GroundingReason.QUOTE_INCLUDES_MARKUP
    if "..." in text:
        return GroundingReason.QUOTE_ELIDED
    if not text:
        return GroundingReason.QUOTE_MISMATCH
    page = [ln for ln in doc.page(page_of(span[0].id)).lines]
    first = [ln.id for ln in page].index(span[0].id)
    for extra in range(len(span), MAX_SPAN_LINES):
        if first + extra < len(page):
            longer = cited_span(doc, allowed, span[0].id, page[first + extra].id)
            if longer and quote_position(longer, quote):
                return GroundingReason.QUOTE_NEEDS_END_LINE
    for p in doc.pages:
        for i, ln in enumerate(p.lines):
            if ln.id not in allowed or ln.id == span[0].id:
                continue
            for extra in range(MAX_SPAN_LINES):
                if i + extra < len(p.lines):
                    other = cited_span(doc, allowed, ln.id, p.lines[i + extra].id)
                    if other and quote_position(other, quote):
                        return GroundingReason.QUOTE_WRONG_LINE
    return GroundingReason.QUOTE_MISMATCH


def _span_evidence(span: list[Line]) -> Evidence:
    if len(span) == 1:
        return evidence(span[0])
    boxes = [ln.bbox for ln in span if ln.bbox]
    bbox = (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)) \
        if len(boxes) == len(span) else span[0].bbox
    return Evidence(page=page_of(span[0].id), bbox=bbox, line_id=span[0].id, text=" ".join(ln.text for ln in span))


_MONTH = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d"
_NOT_DUTY_VERBS = r"(?:not\s+)?(?:receive|be|have|retain|own|remain|continue|survive|accrue|benefit|apply|include)\b"
_DUTY = re.compile(
    rf"\b(?:(?:shall|will)\s+(?!{_NOT_DUTY_VERBS})\w+|must|agrees?\s+to|agreed\s+to|undertakes?\s+to|"
    r"(?:is|are)\s+(?:responsible|required|obligated|obliged)\b|required\s+to|(?:is|are)\s+to\s+\w+|needs?\s+to|"
    r"no\s+later\s+than|on\s+or\s+before|(?:is|are)\s+due\b|please|may\s+not|cannot|can\s*not|upon\s+termination|"
    r"within\s+(?:\w+\s+)?\(?\d|(?:by|before|prior\s+to)\s+(?:" + _MONTH + r"|\d{1,2}[/.-]\d))", re.I)
_KIND_CUE = {
    ObligationKind.RENEWAL: re.compile(r"\brenew|\bextend|\bextension\b|\bsuccessive\b", re.I),
    ObligationKind.EXPIRATION: re.compile(r"\bexpir|\bends?\b|\buntil\b|\bthrough\b|\bterminat|\bterm\b", re.I),
    ObligationKind.NOTICE: re.compile(r"\bnotice\b|\bnotif", re.I),
    ObligationKind.SIGNATURE: re.compile(r"\bsign|\baccept|\bcountersign|\bexecut", re.I),
    ObligationKind.PAYMENT: re.compile(r"\bpa(?:y|id)|\bpayable\b|\binvoice|\bfees?\b|\bdue\b", re.I),
}
_GENERIC_PARTIES = (("either party", "either party"), ("each party", "each party"), ("both parties", "both parties"),
                    ("the parties", "the parties"))


def is_duty(kind: ObligationKind, text: str, table: str | None) -> bool:
    """Binding language for this kind of obligation. Permissions ("may"), descriptions, and background are not duties."""
    if kind is ObligationKind.DELIVERABLE and table and "due" in table:
        return True
    if kind in (ObligationKind.RENEWAL, ObligationKind.EXPIRATION):
        return bool(_KIND_CUE[kind].search(text))
    if _DUTY.search(text):
        return True
    return kind in _KIND_CUE and bool(_KIND_CUE[kind].search(text))


def _mentions(text: str, names: tuple[str, ...]) -> int:
    """Where the last of `names` ends in `text`, or -1."""
    ends = [m.end() for n in names if n for m in re.finditer(rf"(?<!\w){re.escape(n.casefold())}(?!\w)", text.casefold())]
    return max(ends, default=-1)


def party_check(party: str, quote: str, parties: tuple[tuple[str, str], ...], known_text: str) -> str:
    """How the quote supports `party` as the one who must act.

    "subject": the words before the binding verb name this party. "other": they name a different party.
    "known": they name no party, but the document mentions this one. "unknown": the document never mentions it.
    """
    duty = _DUTY.search(quote)
    subject = quote[:duty.start()] if duty else ""
    position, entry = max(((_mentions(subject, e), e) for e in (*parties, *_GENERIC_PARTIES)), key=lambda m: m[0])
    wanted = canon(party).casefold()
    if position >= 0:
        same = any(n and (wanted in n.casefold() or n.casefold() in wanted) for n in entry)
        return "subject" if same else "other"
    return "known" if wanted in known_text else "unknown"


def obligation_outcomes(proposed: list[output.ObligationOut], task: AnalyzeTask, doc: Doc, date_order: str
                        ) -> list[tuple[str | None, ObligationRec | Issue | None]]:
    """(cited line id, outcome) for each proposed obligation, in order.

    The outcome is the record to keep, the rejection Issue (with its stable code), or None for a repeat of a duty
    already kept from this reply. The line id is the normalized citation handle, or None when there is none.
    """
    allowed = {ln.id for ln in task.lines} | fact_line_ids(task)
    tables = {ln.id: ln.table for ln in task.lines if ln.table}
    known_text = canon(" ".join(ln.text for ln in task.lines) + " " + " ".join(task.known_facts.values())).casefold()
    out: list[tuple[str | None, ObligationRec | Issue | None]] = []
    seen: set[tuple] = set()
    for o in proposed:
        cited = line_id(o.line_id)
        span, citation = resolve_span(doc, allowed, o.line_id, o.end_line_id)
        if span is None:
            out.append((cited, _ungrounded(citation, "obligation")))
            continue
        found = quote_position(span, o.quote)
        if found is None:
            out.append((cited, _ungrounded(quote_problem(doc, allowed, span, o.quote), "obligation")))
            continue
        quote = canon(o.quote)
        sentence = sentence_around(*found)
        if not is_duty(o.kind, sentence, tables.get(span[0].id)):
            out.append((cited, _ungrounded(GroundingReason.NOT_AN_OBLIGATION, "obligation")))
            continue
        party = " ".join(o.party.split()) if o.party else None
        support = party_check(party, sentence, task.parties, known_text) if party else "unknown"
        if support == "other":
            out.append((cited, _ungrounded(GroundingReason.PARTY_NOT_GROUNDED, "responsible party")))
            continue
        if support == "unknown":
            party = None
        deadline = canon(o.deadline_text) if o.deadline_text and canon(o.deadline_text) else None
        if deadline and _matchable(deadline) not in sentence:
            out.append((cited, _ungrounded(GroundingReason.DEADLINE_NOT_GROUNDED, "deadline")))
            continue
        due = None
        if o.due_date:
            value = typed(ValueType.DATE, o.due_date, deadline or quote, (), date_order)
            if value is None:
                out.append((cited, _ungrounded(GroundingReason.DEADLINE_NOT_GROUNDED, "deadline")))
                continue
            due = date.fromisoformat(value[0])
        description = " ".join(o.description.split())
        if not _faithful(description, sentence) or not _supported(description, sentence, date_order):
            description = quote
        if deadline and _matchable(deadline) not in _matchable(description):
            description = f"{description} ({deadline})"
        key = (o.kind, span[0].id, _matchable(quote).casefold())
        if key in seen:
            out.append((cited, None))
            continue
        seen.add(key)
        out.append((cited, ObligationRec(o.kind, party, description, due, _span_evidence(span), FieldOrigin.MODEL)))
    return out


def ground_analyze(raw: object, task: AnalyzeTask, doc: Doc, date_order: str
                   ) -> tuple[list[Claim], list[ObligationRec], list[Issue]]:
    out = output.parse(task, raw)
    if out is None:
        return [], [], [rejected(failure_reason(raw))]
    allowed = {ln.id for ln in task.lines} | fact_line_ids(task)
    facts = " ".join(task.known_facts.values())
    issues: list[Issue] = []
    claims = []
    for c in out.claims:
        cited = [doc.by_id[i] for i in dict.fromkeys(filter(None, map(line_id, c.line_ids))) if i in allowed and i in doc.by_id]
        if not cited:
            continue
        sources = " ".join(ln.text for ln in cited) + " " + facts
        if _ADVICE.search(c.text) or not _supported(c.text, sources, date_order):
            continue
        claims.append(Claim(" ".join(c.text.split()), tuple(evidence(ln) for ln in cited)))
    outcomes = obligation_outcomes(out.obligations, task, doc, date_order)
    obligations = [r for _, r in outcomes if isinstance(r, ObligationRec)]
    issues += [r for _, r in outcomes if isinstance(r, Issue)]
    return claims, obligations, issues


def ground_transcribe(raw: object) -> tuple[list[str], list[Issue]]:
    out = output.parse(TranscribeTask(b""), raw)
    if out is None:
        return [], [rejected(failure_reason(raw))]
    return [" ".join(t.split()) for t in out.lines if t.strip()], []
