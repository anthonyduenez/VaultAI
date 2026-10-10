"""Legal-document knowledge: parties, term and renewal, signatures, clauses, risks, and obligations.

Deterministic and always run for contracts, agreement letters, statements of
work, and policies (memos get obligations only). Clause text keeps the exact
original wording; every risk flag carries a plain reason. This is issue
spotting for a human reviewer, not legal advice.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import date, timedelta

from vaultai.analysis import Analysis, ClauseRec, ObligationRec
from vaultai.domain import LEGAL_DOC_TYPES, ClauseKind, ClauseRisk, DocType, IssueCode, ObligationKind
from vaultai.extraction import PATTERN, Found, evidence
from vaultai.extraction.normalize import clean_text, find_dates, find_money, money_str, number_word
from vaultai.extraction.rules import CLOSING, ORG_SUFFIX, Ctx, SignatureBlock, found, signature_blocks
from vaultai.parsing.layout import Doc, Line
from vaultai.parsing.tables import DELIVERABLE_COLUMNS, read_rows
from vaultai.schemas import SCHEMAS

# ---------------------------------------------------------------- text structure

FOOTER_ZONE = 0.93
_PAGE_FOOTER = re.compile(r"^page \d+(?: of \d+)?$", re.I)
# "1. Services", "4.2 Fees", "Section 7: Term". A bare number ("410 Pier Avenue") is an address, not a heading.
HEADING = re.compile(
    r"^(?:(?:Section|SECTION|Article|ARTICLE)\s+\d{1,3}[.:]?|\d{1,2}\.(?:\d{1,2}\.?)*)\s+(?P<title>[A-Z][A-Za-z0-9 ,&/'()-]{1,60})$")
_ABBREV = re.compile(r"\b(?:Inc|Co|Corp|Ltd|No|Nos|Mr|Mrs|Ms|Dr|St|Jr|Sr|vs|Rev|Sec|approx|e\.g|i\.e|U\.S|L\.L\.C)\.$", re.I)
_BOUNDARY = re.compile(r"(?<=[.!?])[\"”)]?\s+(?=[A-Z0-9\"“(\[])")
_NUMBERING = re.compile(r"^\d+(?:\.\d+)*[.)]?\s+")


def is_heading(line: Line) -> bool:
    m = HEADING.match(line.text.strip())
    return bool(m and len(m["title"].split()) <= 8 and not m["title"].rstrip().endswith("."))


def _furniture(line: Line) -> bool:
    return bool(_PAGE_FOOTER.match(line.text.strip()) or (line.bbox and line.bbox[1] >= FOOTER_ZONE))


def body_lines(doc: Doc) -> list[Line]:
    return [ln for ln in doc.visible if not _furniture(ln)]


def _centre(line: Line) -> float:
    return (line.bbox[1] + line.bbox[3]) / 2 if line.bbox else 0.0


def _continues(prev: Line, line: Line, max_gap: float) -> bool:
    if is_heading(prev) or is_heading(line) or len(prev.spans) > 1 or len(line.spans) > 1:
        return False
    if prev.bbox is None or line.bbox is None:
        return True
    return 0 < _centre(line) - _centre(prev) <= max_gap and abs(line.bbox[0] - prev.bbox[0]) < 0.05


def _median(values: list[float]) -> float:
    return sorted(values)[len(values) // 2] if values else 0.0


def paragraphs(doc: Doc) -> list[list[Line]]:
    """Runs of single-column lines; a gap clearly wider than the page's usual line pitch starts a new paragraph."""
    out: list[list[Line]] = []
    for page in doc.pages:
        rows = [ln for ln in page.lines if ln.flag is None and not _furniture(ln)]
        height = _median([ln.bbox[3] - ln.bbox[1] for ln in rows if ln.bbox])
        pitch = _median([_centre(b) - _centre(a) for a, b in zip(rows, rows[1:]) if a.bbox and b.bbox])
        max_gap = max(1.85 * height, 1.3 * pitch)
        current: list[Line] = []
        for ln in rows:
            if current and _continues(current[-1], ln, max_gap):
                current.append(ln)
            else:
                if current:
                    out.append(current)
                current = [ln]
        if current:
            out.append(current)
    return out


@dataclass(frozen=True)
class Sentence:
    text: str
    line: Line
    para: int


def sentences(doc: Doc) -> list[Sentence]:
    out: list[Sentence] = []
    for pi, para in enumerate(paragraphs(doc)):
        if len(para) == 1 and is_heading(para[0]):
            continue
        text, starts = "", []
        for ln in para:
            text += " " if text else ""
            starts.append((len(text), ln))
            text += ln.text

        def line_at(offset: int) -> Line:
            return next(ln for start, ln in reversed(starts) if start <= offset)

        pos = 0
        for m in _BOUNDARY.finditer(text):
            if _ABBREV.search(text[pos:m.start()].rstrip("\"”)")):
                continue
            chunk = text[pos:m.start()].strip()
            if chunk:
                out.append(Sentence(chunk, line_at(pos), pi))
            pos = m.end()
        tail = text[pos:].strip()
        if tail:
            out.append(Sentence(tail, line_at(pos), pi))
    return out


def _sentence_found(ctx: Ctx, key: str, s: Sentence, value: str | None, raw: str | None, conf: float = PATTERN) -> Found:
    return found(ctx, key, s.line, value, raw, conf)


# ---------------------------------------------------------------- clauses and risk

CLAUSE_KINDS: tuple[tuple[ClauseKind, re.Pattern[str]], ...] = (
    (ClauseKind.SIGNATURES, re.compile(r"in witness whereof|accepted and agreed|\bsignatures?\b", re.I)),
    (ClauseKind.RENEWAL, re.compile(r"\brenew", re.I)),
    (ClauseKind.TERMINATION, re.compile(r"\bterminat", re.I)),
    (ClauseKind.NON_COMPETE, re.compile(r"non-?compet|non-?solicit|\bsolicit\b|\bcompete\b", re.I)),
    (ClauseKind.INDEMNITY, re.compile(r"\bindemni", re.I)),
    (ClauseKind.INSURANCE, re.compile(r"\binsurance\b", re.I)),
    (ClauseKind.LIABILITY, re.compile(r"\bliabilit", re.I)),
    (ClauseKind.CONFIDENTIALITY, re.compile(r"\bconfidential", re.I)),
    (ClauseKind.INTELLECTUAL_PROPERTY, re.compile(r"intellectual property|\bownership\b|\blicen[cs]e", re.I)),
    (ClauseKind.DATA_PROTECTION, re.compile(r"data protection|\bprivacy\b|\bsecurity\b|personal data", re.I)),
    (ClauseKind.SERVICE_LEVEL, re.compile(r"service levels?|\bavailability\b|\buptime\b|\bsla\b", re.I)),
    (ClauseKind.WARRANTY, re.compile(r"\bwarrant", re.I)),
    (ClauseKind.GOVERNING_LAW, re.compile(r"governing law|governed by", re.I)),
    (ClauseKind.DISPUTE_RESOLUTION, re.compile(r"\bdisputes?\b|\barbitrat", re.I)),
    (ClauseKind.PAYMENT, re.compile(r"\bfees?\b|\bpayment|\bcompensation\b|\binvoic|\bpay\b", re.I)),
    (ClauseKind.DELIVERABLES, re.compile(r"\bdeliverable|\bmilestone|\bscope\b|\bservices\b|\bdeliver\b", re.I)),
    (ClauseKind.TERM, re.compile(r"\bterm\b|\bduration\b", re.I)),
)
_MUTUAL = re.compile(r"\b(each party|either party|both parties|the parties|mutual(?:ly)?)\b", re.I)


@dataclass(frozen=True)
class Finding:
    code: IssueCode
    risk: ClauseRisk
    reason: str
    phrase: str


def _regex(pattern: str) -> Callable[[str], str | None]:
    rx = re.compile(pattern, re.I)

    def find(text: str) -> str | None:
        m = rx.search(text)
        return m.group(0) if m else None

    return find


def _one_sided_indemnity(text: str) -> str | None:
    hits = list(re.finditer(r"\b(?P<who>[A-Z][\w]*(?: [A-Z][\w]*)*|[Ee]ach party|[Ee]ither party|[Bb]oth parties)\s+"
                            r"(?:shall|will|must|agrees? to)\s+(?:defend,?\s+)?indemnif\w*", text))
    subjects = {h["who"].lower() for h in hits}
    if len(subjects) == 1 and not _MUTUAL.search(text):
        return hits[0].group(0)
    return None


def _one_sided_convenience(text: str) -> str | None:
    m = re.search(r"\b(?P<who>[A-Z]\w*(?: [A-Z]\w*)*) may terminate\b[^.]{0,120}?\bfor convenience\b", text)
    if m and not _MUTUAL.search(m["who"]) and m["who"].lower() not in {"either", "each", "any party"}:
        return m.group(0)
    return None


_auto_renews = _regex(r"(?<!not )\b(?:automatically renew\w*|auto-?renew\w*|renew\w* automatically)\b")
RISKS: tuple[tuple[IssueCode, ClauseRisk, str, Callable[[str], str | None]], ...] = (
    (IssueCode.AUTO_RENEWAL, ClauseRisk.ATTENTION, "Renews automatically unless notice is given in time", _auto_renews),
    (IssueCode.RISKY_CLAUSE, ClauseRisk.PROFESSIONAL_REVIEW, "Liability is unlimited for at least one party",
     _regex(r"\bliabilit\w*\b[^.]{0,120}?\b(?:shall be|is|are|will be)\s+unlimited\b|\bunlimited liability\b|\buncapped\b")),
    (IssueCode.RISKY_CLAUSE, ClauseRisk.PROFESSIONAL_REVIEW, "Only one party gives an indemnity", _one_sided_indemnity),
    (IssueCode.RISKY_CLAUSE, ClauseRisk.PROFESSIONAL_REVIEW, "Restricts competing, soliciting, or hiring",
     _regex(r"\b(?:shall|will|must|agrees? to)\s+not\s+(?:\w+\s+){0,2}?(?:compete|solicit|hire)\b|\bnon-?compet\w*|\bnon-?solicit\w*")),
    (IssueCode.RISKY_CLAUSE, ClauseRisk.ATTENTION, "Only one party may terminate for convenience", _one_sided_convenience),
    (IssueCode.AMBIGUOUS_CLAUSE, ClauseRisk.NONE, "Vague standard that the document does not define",
     _regex(r"\bcommercially reasonable efforts\b|\breasonable best efforts\b|\bbest efforts\b|\bpromptly\b|"
            r"\bsole discretion\b|\bas soon as (?:reasonably )?practicable\b|\bin a timely manner\b")),
)
_RISK_ORDER = {ClauseRisk.NONE: 0, ClauseRisk.ATTENTION: 1, ClauseRisk.PROFESSIONAL_REVIEW: 2}


def clause_findings(text: str) -> list[Finding]:
    return [Finding(code, risk, reason, phrase) for code, risk, reason, find in RISKS if (phrase := find(text))]


def clause_kind(heading: str, text: str) -> ClauseKind:
    """The heading decides; body keywords only break a tie when the heading names no known kind."""
    for kind, rx in CLAUSE_KINDS:
        if rx.search(heading):
            return kind
    for kind, rx in CLAUSE_KINDS:
        if kind is not ClauseKind.SIGNATURES and rx.search(text):
            return kind
    return ClauseKind.OTHER


KIND_LABELS = {k: k.value.replace("_", " ").capitalize() for k in ClauseKind}


def _clause(kind: ClauseKind | None, heading_line: Line | None, heading: str, body: list[Line]) -> ClauseRec | None:
    text = " ".join(ln.text for ln in body).strip()
    if not text and heading_line is None:
        return None
    kind = kind or clause_kind(re.sub(r"^\S+\s+", "", heading) if heading_line else "", text)
    findings = [f for f in clause_findings(text) if f.risk is not ClauseRisk.NONE]
    risk = max((f.risk for f in findings), key=_RISK_ORDER.__getitem__, default=ClauseRisk.NONE)
    reason = "; ".join(dict.fromkeys(f.reason for f in findings)) or None
    anchor = heading_line or body[0]
    return ClauseRec(kind, heading if heading_line else KIND_LABELS[kind], text or heading,
                     risk, reason, evidence(anchor))


_SIGNATURE_START = re.compile(r"\b(in witness whereof|accepted and agreed|agreed and accepted)\b", re.I)
_SALUTATION = re.compile(r"^dear\b", re.I)


def clauses(doc: Doc) -> list[ClauseRec]:
    body = body_lines(doc)
    sig = next((i for i, ln in enumerate(body) if _SIGNATURE_START.search(ln.text)), len(body))
    heads = [i for i, ln in enumerate(body[:sig]) if is_heading(ln)]
    out: list[ClauseRec] = []
    if heads:
        for n, i in enumerate(heads):
            end = heads[n + 1] if n + 1 < len(heads) else sig
            rec = _clause(None, body[i], body[i].text.strip(), body[i + 1:end])
            if rec:
                out.append(rec)
    else:
        start = next((i + 1 for i, ln in enumerate(body) if _SALUTATION.match(ln.text)), None)
        stop = next((i for i, ln in enumerate(body) if CLOSING.match(ln.text.strip())), sig)
        if start is not None:
            ids = {ln.id for ln in body[start:min(stop, sig)]}
            for para in paragraphs(doc):
                if para[0].id in ids:
                    rec = _clause(None, None, "", para)
                    if rec:
                        out.append(rec)
    if sig < len(body):
        rec = _clause(ClauseKind.SIGNATURES, body[sig], "Signatures", body[sig + 1:])
        if rec:
            out.append(replace(rec, heading="Signatures", text=" ".join(ln.text for ln in body[sig:])))
    return out


# ---------------------------------------------------------------- parties and legal fields

_DQ = "[\"“”]"
_PARTY = (r"(?P<name{n}>[A-Z][\w&.,' -]+?)(?:,\s+an?\s+[^()]*?)?\s*\(\s*(?:the\s+|hereinafter\s+)?" + _DQ
          + r"(?P<term{n}>[^\"“”]+)" + _DQ + r"\s*\)")
PREAMBLE = re.compile(r"\bbetween\s+" + _PARTY.format(n=1) + r"\s*,?\s*and\s+" + _PARTY.format(n=2))


def preamble_parties(sents: list[Sentence]) -> tuple[list[tuple[str, str]], Sentence | None]:
    for s in sents[:12]:
        m = PREAMBLE.search(s.text)
        if m:
            return [(clean_text(m["name1"]), m["term1"].strip()), (clean_text(m["name2"]), m["term2"].strip())], s
    return [], None


def _date_after(text: str, rx: re.Pattern[str], date_order: str) -> tuple[date | None, str] | None:
    m = rx.search(text)
    if not m:
        return None
    hits = find_dates(text[m.end():m.end() + 40], date_order)
    return (hits[0].value, hits[0].raw) if hits else None


_TERM_END = re.compile(r"\b(?:continu\w*|remain\w* in (?:full force and )?effect|expir\w*|end\w*|run\w*)\s+(?:on|until|through|thru)\s+", re.I)
_EFFECTIVE = re.compile(r"\b(?:effective (?:as of|on)|dated as of|commenc\w* on|begin\w* on|start\w* on)\s+", re.I)
_NOTICE = re.compile(r"(?P<n>(?:[a-z-]+\s*)?\(\d+\)|\d+|[a-z-]+)\s+(?P<unit>days?|months?)\b(?:'|’)?[^.]{0,40}?\b(?:before|prior to|in advance)", re.I)
_RENEW_NEG = re.compile(r"\b(?:not|never)\s+(?:be\s+)?(?:automatically\s+)?renew", re.I)
_GOVERNING = re.compile(
    r"governed by (?:and construed (?:in accordance with|under) )?the laws? of (?:the )?"
    r"(?:(?:State|Commonwealth|Province|Republic) of )?(?P<j>[A-Z][A-Za-z]+(?: [A-Z][A-Za-z]+)*)")
_DISPUTE = re.compile(r"\b(arbitration|mediation|courts? of|exclusive jurisdiction|venue)\b", re.I)
_PAYMENT_TERMS = re.compile(r"\b(within|days|due|net \d+|in advance|upon|installments?)\b", re.I)
_FEE = re.compile(r"\b(fee|fees|compensation|retainer|price|pay|payable)\b", re.I)
_ACCEPT = re.compile(r"\b(accept\w*|countersign\w*|sign\w*)\b", re.I)
_AGREEMENT = re.compile(r"\b(this letter confirms|we are pleased to confirm|agreement between|agrees? to)\b", re.I)


@dataclass(frozen=True)
class Renewal:
    auto: bool
    sentence: Sentence
    notice_days: int | None
    notice_raw: str | None


def renewal(sents: list[Sentence]) -> Renewal | None:
    for s in sents:
        if not re.search(r"\brenew", s.text, re.I):
            continue
        auto = bool(_auto_renews(s.text)) and not _RENEW_NEG.search(s.text)
        m = _NOTICE.search(s.text)
        days = None
        raw = None
        if m and m["unit"].lower().startswith("day"):
            days = number_word(m["n"])
            raw = f"{days} days" if days else None
        return Renewal(auto, s, days, raw)
    return None


def _first(sents: Iterable[Sentence], test: Callable[[Sentence], bool]) -> Sentence | None:
    return next((s for s in sents if test(s)), None)


def _blocks_status(doc_type: DocType, blocks: list[SignatureBlock]) -> tuple[str, list[SignatureBlock]]:
    relevant = [b for b in blocks if not b.closing]
    if doc_type is DocType.AGREEMENT_LETTER:
        relevant = [b for b in relevant if b.acceptance] or relevant or [b for b in blocks if b.closing]
    if not relevant:
        return "unknown", []
    signed = sum(b.signed for b in relevant)
    status = "signed" if signed == len(relevant) else "unsigned" if signed == 0 else "partially_signed"
    return status, relevant


def legal_fields(ctx: Ctx) -> Iterable[Found]:
    doc_type = ctx.schema.doc_type
    sents = sentences(ctx.doc)
    clause_list = clauses(ctx.doc)

    parties, preamble = preamble_parties(sents)
    if parties and preamble:
        names = "; ".join(name for name, _ in parties)
        yield _sentence_found(ctx, "parties", preamble, names, names)

    eff = _first(sents, lambda s: _date_after(s.text, _EFFECTIVE, ctx.date_order) is not None)
    if eff:
        value, raw = _date_after(eff.text, _EFFECTIVE, ctx.date_order)
        yield _sentence_found(ctx, "effective_date", eff, value.isoformat() if value else None, raw)

    end = _first(sents, lambda s: _date_after(s.text, _TERM_END, ctx.date_order) is not None)
    expiration = None
    if end:
        expiration, raw = _date_after(end.text, _TERM_END, ctx.date_order)
        for key in ("expiration_date", "end_date"):
            yield _sentence_found(ctx, key, end, expiration.isoformat() if expiration else None, raw)

    ren = renewal(sents)
    if ren:
        yield _sentence_found(ctx, "auto_renewal", ren.sentence, "true" if ren.auto else "false", ren.sentence.text)
        yield _sentence_found(ctx, "renewal_terms", ren.sentence, ren.sentence.text, ren.sentence.text)
        if ren.notice_raw:
            yield _sentence_found(ctx, "notice_period", ren.sentence, ren.notice_raw, ren.notice_raw)
        if ren.auto and ren.notice_days and expiration:
            deadline = expiration - timedelta(days=ren.notice_days)
            yield _sentence_found(ctx, "renewal_notice_deadline", ren.sentence, deadline.isoformat(), ren.sentence.text)

    by_kind = {c.kind: c for c in reversed(clause_list)}
    pay = by_kind.get(ClauseKind.PAYMENT)
    if pay:
        money = find_money(pay.text, require_marker=True)
        line = ctx.doc.by_id[pay.evidence.line_id]
        if money:
            yield found(ctx, "contract_value", line, money_str(money[0].amount), money[0].raw, PATTERN, money[0].currency)
        terms = [s.text for s in sents if s.text in pay.text and _PAYMENT_TERMS.search(s.text)]
        if terms:
            yield found(ctx, "payment_terms", line, " ".join(terms), " ".join(terms), PATTERN)
    term = by_kind.get(ClauseKind.TERMINATION)
    if term:
        yield found(ctx, "termination", ctx.doc.by_id[term.evidence.line_id], term.text, term.text, PATTERN)

    for s in sents:
        m = _GOVERNING.search(s.text)
        if m:
            yield _sentence_found(ctx, "governing_law", s, m["j"], m.group(0))
            break
    dispute = _first(sents, lambda s: bool(_DISPUTE.search(s.text)))
    if dispute:
        yield _sentence_found(ctx, "dispute_resolution", dispute, clean_text(dispute.text), dispute.text)

    blocks = signature_blocks(ctx)
    status, relevant = _blocks_status(doc_type, blocks)
    anchor = relevant[0].evidence if relevant else None
    if anchor:
        line = ctx.doc.by_id[anchor.line_id]
        yield found(ctx, "signature_status", line, status, anchor.text, PATTERN)
        names = [b.name for b in blocks if b.name and not b.closing]
        if names:
            yield found(ctx, "signatories", line, "; ".join(names), "; ".join(names), PATTERN)
    elif ctx.doc.visible:
        yield found(ctx, "signature_status", ctx.doc.visible[-1], "unknown", None, 0.5)

    if doc_type is DocType.AGREEMENT_LETTER:
        yield from _letter_fields(ctx, sents, blocks)


def _letter_fields(ctx: Ctx, sents: list[Sentence], blocks: list[SignatureBlock]) -> Iterable[Found]:
    tops = [ln for ln in ctx.doc.pages[0].lines if ln.flag is None][:12]
    date_idx = next((i for i, ln in enumerate(tops) if (h := find_dates(ln.text, ctx.date_order))
                     and h[0].raw == clean_text(ln.text)), None)
    if date_idx is not None:
        line = tops[date_idx]
        hit = find_dates(line.text, ctx.date_order)[0]
        yield found(ctx, "letter_date", line, hit.value.isoformat() if hit.value else None, hit.raw, PATTERN)
        block = []
        for ln in tops[date_idx + 1:]:
            if re.match(r"^(re|subject)\s*:|^dear\b", ln.text, re.I):
                break
            block.append(ln)
        if block:
            yield found(ctx, "recipient", block[0], clean_text(block[0].text), block[0].text, PATTERN)
            org = next((ln for ln in block[1:] if ORG_SUFFIX.search(ln.text)), None)
            if org:
                yield found(ctx, "recipient_organization", org, clean_text(org.text), org.text, PATTERN)
    closing = next((b for b in blocks if b.closing), None)
    if closing and closing.name:
        yield found(ctx, "sender", ctx.doc.by_id[closing.evidence.line_id], closing.name, closing.name, PATTERN)
    statement = _first(sents, lambda s: bool(_AGREEMENT.search(s.text)))
    if statement:
        yield _sentence_found(ctx, "agreement_statement", statement, statement.text, statement.text)
    for i, s in enumerate(sents):
        money = find_money(s.text, require_marker=True)
        if money and _FEE.search(s.text):
            yield found(ctx, "compensation", s.line, money_str(money[0].amount), money[0].raw, PATTERN, money[0].currency)
            follow = [t.text for t in sents[i + 1:i + 3] if t.para == s.para and re.search(r"\bdue\b", t.text, re.I)]
            if follow:
                yield _sentence_found(ctx, "payment_terms", s, " ".join(follow), " ".join(follow))
            break
    for s in sents:
        hits = find_dates(s.text, ctx.date_order)
        if hits and _ACCEPT.search(s.text) and re.search(r"\b(return|by|before|no later than)\b", s.text, re.I):
            yield _sentence_found(ctx, "acceptance_deadline", s, hits[-1].value.isoformat() if hits[-1].value else None, hits[-1].raw)
            break
    term = _first(sents, lambda s: bool(re.search(r"\bterminat", s.text, re.I)))
    if term:
        yield _sentence_found(ctx, "termination", term, term.text, term.text)


# ---------------------------------------------------------------- obligations

_MODAL = (r"(?P<modal>shall not|shall|will not|will|must not|must|agrees? to|undertakes? to|"
          r"(?:is|are) required(?: to)?)")
_NOT_DUTIES = {"receive", "be", "have", "retain", "own", "remain", "continue", "survive", "accrue", "benefit"}
_DUE = re.compile(r"\b(?:by|no later than|on or before|before|prior to|due on|due)\s+$", re.I)
_PERSON_NOUNS = {"employee", "employees", "staff", "manager", "managers", "supervisor", "supervisors", "contractor",
                 "contractors", "operator", "operators", "everyone", "vendor", "vendors", "traveler", "travelers"}
_STOP = {"this", "that", "with", "will", "shall", "must", "from", "into", "within", "being", "have", "their", "there",
         "which", "these", "those", "under", "upon", "such", "each", "other", "party", "parties", "agreement"}
_GENERIC_PARTIES = ("either party", "each party", "both parties", "the parties")


def _due(text: str, date_order: str) -> date | None:
    for hit in find_dates(text, date_order):
        if _DUE.search(text[max(0, hit.start - 20):hit.start]):
            return hit.value
    return None


def _content_words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]{4,}", text.lower()) if w not in _STOP}


def _obligation_kind(rest: str, sentence: str) -> ObligationKind:
    if re.match(r"(?:\w+\s+){0,1}pay\b", rest, re.I):
        return ObligationKind.PAYMENT
    if re.search(r"\bdeliver\b", sentence, re.I):
        return ObligationKind.DELIVERABLE
    return ObligationKind.OBLIGATION


def _resolve(party: str | None, names: Mapping[str, str]) -> str | None:
    if not party:
        return None
    low = party.lower()
    for key, name in names.items():
        if key.lower() == low:
            return name
    if low in _GENERIC_PARTIES:
        return party[0].upper() + party[1:].lower()
    return party


def _party_names(sents: list[Sentence], fields: Mapping[str, Found]) -> dict[str, str]:
    names: dict[str, str] = {}
    for name, term in preamble_parties(sents)[0]:
        names[term] = name
        names[name] = name
    for key, term in (("client", "Client"), ("provider", "Provider")):
        f = fields.get(key)
        if f and f.value:
            names.setdefault(term, f.value)
            names[f.value] = f.value
    for key in ("recipient_organization", "sender_organization"):
        f = fields.get(key)
        if f and f.value:
            names[f.value] = f.value
    return names


def _duties(sents: list[Sentence], subject: str, date_order: str) -> Iterable[tuple[Sentence, str, str, date | None]]:
    rx = re.compile(rf"(?:^|[\s,;(])(?P<subj>{subject})\s+{_MODAL}\s+(?P<rest>.+)", re.I)
    for i, s in enumerate(sents):
        m = rx.search(_NUMBERING.sub("", s.text))
        if not m:
            continue
        verb = m["rest"].split()[0].lower() if m["rest"].split() else ""
        # "Services will be performed" describes; "Airfare must be booked" obliges.
        if verb in _NOT_DUTIES and not (verb == "be" and m["modal"].lower() != "will"):
            continue
        due = _due(s.text, date_order)
        if due is None and i + 1 < len(sents) and sents[i + 1].para == s.para:
            nxt = sents[i + 1]
            if not rx.search(nxt.text) and len(_content_words(s.text) & _content_words(nxt.text)) >= 2:
                due = _due(nxt.text, date_order)
        yield s, m["subj"], m["rest"], due


def _description(s: Sentence) -> str:
    return _NUMBERING.sub("", s.text).strip()


def obligations(doc: Doc, doc_type: DocType, fields: Mapping[str, Found], date_order: str) -> list[ObligationRec]:
    sents = sentences(doc)
    out: list[ObligationRec] = []

    def add(kind: ObligationKind, party: str | None, description: str, due: date | None, line: Line) -> None:
        out.append(ObligationRec(kind, party, description, due, evidence(line)))

    def value(key: str) -> str | None:
        f = fields.get(key)
        return f.value if f else None

    if doc_type is DocType.CORRESPONDENCE:
        for s, subj, rest, due in _duties(sents, r"[A-Z][\w ,'-]{0,60}?", date_order):
            if due:
                add(ObligationKind.OBLIGATION, value("recipient") or subj, _description(s), due, s.line)
        return out

    if doc_type is DocType.POLICY:
        aud = next((m for s in sents if (m := re.search(r"\bapplies to (?:all )?(?P<a>[a-z]+)", s.text))), None)
        audience = aud["a"].capitalize() if aud else None
        for s, subj, rest, due in _duties(sents, r"[A-Z][\w '-]{0,40}?", date_order):
            party = subj if subj.split()[-1].lower() in _PERSON_NOUNS else audience
            add(_obligation_kind(rest, s.text), party, _description(s), due, s.line)
        return out

    names = _party_names(sents, fields)
    subjects = sorted({*names, *_GENERIC_PARTIES}, key=len, reverse=True)
    payments: dict[str | None, list[Sentence]] = {}
    for s, subj, rest, due in _duties(sents, "|".join(re.escape(x) for x in subjects), date_order):
        party = _resolve(subj, names)
        kind = _obligation_kind(rest, s.text)
        if kind is ObligationKind.PAYMENT:
            payments.setdefault(party, []).append(s)
        else:
            add(kind, party, _description(s), due, s.line)
    for party, group in payments.items():
        add(ObligationKind.PAYMENT, party, " ".join(_description(s) for s in group), None, group[0].line)

    client = names.get("Client") or value("client") or value("recipient_organization")
    provider = names.get("Provider") or value("provider") or value("sender_organization")
    if doc_type is DocType.STATEMENT_OF_WORK:
        for row in read_rows(doc, DELIVERABLE_COLUMNS, frozenset({"description", "due"}), re.compile(r"$^")):
            hits = find_dates(row.cells.get("due", ""), date_order)
            add(ObligationKind.DELIVERABLE, provider, row.cells["description"], hits[0].value if hits else None, row.line)

    fee = fields.get(SCHEMAS[doc_type].total or "")
    if not payments and fee and fee.value and fee.evidence:
        terms = value("payment_terms")
        add(ObligationKind.PAYMENT, client, f"Pay {fee.raw_text}" + (f": {terms}" if terms else ""), None,
            doc.by_id[fee.evidence.line_id])

    expiration = value("expiration_date") or value("end_date")
    end_key = "expiration_date" if value("expiration_date") else "end_date"
    if expiration and fields[end_key].evidence:
        ev = fields[end_key].evidence
        add(ObligationKind.EXPIRATION, None, ev.text or "Term ends", date.fromisoformat(expiration), doc.by_id[ev.line_id])
    ren = renewal(sents)
    deadline = value("renewal_notice_deadline")
    if ren and ren.auto and deadline:
        who = next((p for p in _GENERIC_PARTIES if p in ren.sentence.text.lower()), None)
        add(ObligationKind.NOTICE, _resolve(who, names) if who else None, _description(ren.sentence),
            date.fromisoformat(deadline), ren.sentence.line)

    ctx = Ctx(doc, SCHEMAS[doc_type], date_order)
    status, relevant = _blocks_status(doc_type, signature_blocks(ctx))
    if status in {"unsigned", "partially_signed"}:
        acceptance = value("acceptance_deadline")
        for b in relevant:
            if not b.signed:
                party = _resolve(b.party, names) or client
                add(ObligationKind.SIGNATURE, party, f"Signature for {party or 'a party'} is missing",
                    date.fromisoformat(acceptance) if acceptance else None, doc.by_id[b.evidence.line_id])
    return out


def analyze(doc: Doc, doc_type: DocType, fields: Mapping[str, Found], date_order: str) -> Analysis:
    if doc_type is DocType.CORRESPONDENCE:
        return Analysis((), tuple(obligations(doc, doc_type, fields, date_order)))
    if doc_type not in LEGAL_DOC_TYPES:
        return Analysis()
    return Analysis(tuple(clauses(doc)), tuple(obligations(doc, doc_type, fields, date_order)))

