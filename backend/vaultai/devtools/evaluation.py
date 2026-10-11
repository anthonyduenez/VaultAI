"""Evaluation helpers for scripts/eval_samples.py: document variants and model-call instrumentation.

Variants turn the bundled sample PDFs into harder inputs without editing samples/:
  ocr     every page rendered to an image, so tesseract has to read it;
  stress  printed labels renamed to synonyms the rules do not know, so required
          fields fall to the model. Values and layout are unchanged, so a person
          (or a model) can still read every field.
The stress set is a held-out check of the model path. Do not add its synonyms to
the rules' aliases, or the evaluation stops measuring anything.

Recorder wraps any adapter and counts calls, prompt size, latency, and token
usage. OracleModel is a perfect reader limited to the lines it is shown: it
answers a field only when the expected value is printed on one of those lines.
Its score is an upper bound on what any model could extract from the same
context. For analysis it proposes each expected obligation that has a printed
date, quoted as the sentence holding that date, which checks that the
obligation validator accepts correct obligations. It is not a language model
and its numbers are not Nemotron's.
"""

from __future__ import annotations

import io
import re
import threading
import time
import zlib
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation

from vaultai.devtools.pdfgen import Page, PdfDoc
from vaultai.domain import DocType, ModelStatus, ValueType
from vaultai.extraction.normalize import find_dates, find_money
from vaultai.parsing.layout import Doc
from vaultai.model.base import AnalyzeTask, ClassifyTask, ExtractTask, ModelUnavailable, Reply, Task, TranscribeTask

STRESS_LABELS: dict[str, tuple[tuple[str, str], ...]] = {
    "demo/check_4521_northwind.pdf": (("PAY TO THE ORDER OF", "PAYABLE TO"), ("Date: October", "Dated October")),
    "scanner/check_4522_pinecrest.pdf": (("PAY TO THE ORDER OF", "PAYABLE TO"), ("Date: October", "Dated October")),
    "demo/receipt_cornerstone_office_mart.pdf": (("Date: 10/03", "Txn 10/03"), ("TOTAL", "AMOUNT CHARGED")),
    "scanner/receipt_harbor_fuel.pdf": (("Date: 10/08", "Txn 10/08"), ("TOTAL", "AMOUNT CHARGED")),
    **{
        path: (("Invoice Number:", "Our Reference:"), ("Invoice Date:", "Issued:"), ("Total Due:", "Balance Payable:"))
        for path in (
            "demo/invoice_acme_INV-2026-0142.pdf",
            "demo/invoice_pinecrest_PFS-10388.pdf",
            "demo/invoice_quickship_QS-55021.pdf",
            "scanner/invoice_two_page_tidewater.pdf",
        )
    },
    "demo/purchase_order_PO-77310.pdf": (
        ("PO Number:", "Order Ref:"), ("Order Date:", "Raised:"), ("Total:", "Order Value:"), ("Vendor:", "Seller:"),
    ),
    "demo/quote_coastal_air_Q-2291.pdf": (("Date:", "Issued:"), ("Total:", "Quoted Price:")),
    "demo/contract_msa_brightpath.pdf": (("Effective Date: January", "Commencement: January"),),
    "demo/sow_brightpath_onboarding.pdf": (
        ("Client: Harborview", "Customer: Harborview"), ("Provider: Brightpath", "Delivered by: Brightpath"),
        ("Effective Date:", "Kick-off:"),
    ),
    "demo/policy_travel_expense_FIN-104.pdf": (("Effective Date:", "In force from:"),),
    "demo/memo_forklift_recertification.pdf": (("From:", "Sent by:"), ("Date:", "Sent:")),
}

_PAGE = re.compile(rb"/MediaBox \[0 0 ([\d.]+) ([\d.]+)\] /Resources << /Font << [^>]* >> >> /Contents (\d+) 0 R")


class _RawPage(Page):
    def __init__(self, width: float, height: float, content: bytes) -> None:
        super().__init__(width, height)
        self._content = content

    def content(self) -> bytes:
        return self._content


def _stream(pdf: bytes, obj: int) -> bytes:
    m = re.search(rb"(?:^|\n)%d 0 obj\n<< /Length (\d+) /Filter /FlateDecode >>\nstream\n" % obj, pdf)
    if m is None:
        raise ValueError(f"content stream {obj} not found")
    return zlib.decompress(pdf[m.end(): m.end() + int(m.group(1))])


def relabel_pdf(pdf: bytes, substitutions: tuple[tuple[str, str], ...]) -> bytes:
    """Rewrite text in a pdfgen sample's content streams. Every substitution must match at least once."""
    pages = [(float(w), float(h), _stream(pdf, int(obj))) for w, h, obj in _PAGE.findall(pdf)]
    hits = dict.fromkeys((old for old, _ in substitutions), 0)
    out = PdfDoc()
    for w, h, content in pages:
        for old, new in substitutions:
            hits[old] += content.count(old.encode("cp1252"))
            content = content.replace(old.encode("cp1252"), new.encode("cp1252"))
        out.pages.append(_RawPage(w, h, content))
    missing = [old for old, n in hits.items() if n == 0]
    if missing:
        raise ValueError(f"substitutions did not match: {missing}")
    return out.to_bytes()


def rasterize_pdf(pdf: bytes, dpi: int = 200) -> bytes:
    """Every page as an 8-bit grayscale image in one multi-page TIFF, with no text layer."""
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(pdf)
    try:
        images = [doc[i].render(scale=dpi / 72).to_pil().convert("L") for i in range(len(doc))]
    finally:
        doc.close()
    buf = io.BytesIO()
    images[0].save(buf, format="TIFF", compression="tiff_lzw", save_all=len(images) > 1, append_images=images[1:],
                   dpi=(dpi, dpi))
    return buf.getvalue()


@dataclass
class Call:
    doc: str | None
    task: str
    ms: float
    prompt_chars: int
    ok: bool
    attempts: int = 1
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


def _prompt_chars(task: Task) -> int:
    if isinstance(task, TranscribeTask):
        return len(task.image_png)
    from vaultai.model import prompts

    return sum(len(m["content"]) for m in prompts.messages(task) if isinstance(m["content"], str))


class Recorder:
    """Pass-through adapter that records every call. Set `current` to label the calls of one document."""

    def __init__(self, inner: object) -> None:
        self.inner = inner
        self.kind = inner.kind
        self.name = inner.name
        self.vision = inner.vision
        self.current: str | None = None
        self.calls: list[Call] = []
        # Analysis replies, kept in memory only so the evaluator can score the model's own obligations.
        self.analyses: list[tuple[str | None, AnalyzeTask, object]] = []
        self._lock = threading.Lock()

    def status(self) -> ModelStatus:
        return self.inner.status()

    def run(self, task: Task) -> object:
        started = time.monotonic()
        ok = True
        out = None
        try:
            out = self.inner.run(task)
            return out
        except ModelUnavailable:
            ok = False
            raise
        finally:
            usage = getattr(out, "usage", None) or {}
            call = Call(self.current, type(task).__name__, (time.monotonic() - started) * 1000, _prompt_chars(task), ok,
                        getattr(out, "attempts", 1), usage.get("prompt_tokens"), usage.get("completion_tokens"))
            with self._lock:
                self.calls.append(call)
                if ok and isinstance(task, AnalyzeTask):
                    self.analyses.append((self.current, task, out.output if isinstance(out, Reply) else out))


def _printed(value_type: ValueType, gold: str, text: str, date_order: str) -> bool:
    if value_type is ValueType.MONEY:
        try:
            want = Decimal(gold)
        except InvalidOperation:
            return False
        return any(m.amount == want for m in find_money(text))
    if value_type is ValueType.DATE:
        want_day = date.fromisoformat(gold)
        return any(h.value == want_day for h in find_dates(text, date_order))
    if value_type in (ValueType.BOOL, ValueType.ENUM):
        return False
    return " ".join(gold.split()).casefold() in " ".join(text.split()).casefold()


@dataclass
class OracleModel:
    """Answers from samples/expected.json, but only with values printed on a line it was shown."""

    expected: Mapping[str, Mapping]
    date_order: str = "MDY"
    current: str | None = None
    kind: object = field(default=None)
    name: str = "oracle"
    vision: bool = False

    def __post_init__(self) -> None:
        from vaultai.domain import ModelKind

        self.kind = ModelKind.OPENAI_COMPAT

    def status(self) -> ModelStatus:
        return ModelStatus(kind=self.kind, name=self.name, reachable=True, vision=False, detail="evaluation oracle")

    def run(self, task: Task) -> object:
        want = self.expected.get(self.current or "", {})
        if isinstance(task, ClassifyTask):
            gold = want.get("doc_type")
            return {"doc_type": gold if gold and DocType(gold) in task.labels else None, "confidence": 0.9}
        if isinstance(task, ExtractTask):
            gold_fields = want.get("fields", {}) if want.get("doc_type") == task.doc_type.value else {}
            out = []
            for spec in task.fields:
                gold = gold_fields.get(spec.key)
                if gold is None:
                    continue
                line = next((ln for ln in task.lines if _printed(spec.value_type, gold, ln.text, self.date_order)), None)
                if line is not None:
                    out.append({"key": spec.key, "value": gold, "line_id": line.id, "quote": line.text, "confidence": 0.9})
            return {"fields": out}
        if isinstance(task, AnalyzeTask):
            gold = want.get("obligations", []) if want.get("doc_type") == task.doc_type.value else []
            return {"claims": [], "obligations": [o for g in gold if (o := self._obligation(task, g))]}
        return {"lines": []}

    def _obligation(self, task: AnalyzeTask, gold: Mapping) -> dict | None:
        """The expected obligation, quoted as the whole printed sentence that holds its date, from 1-3 adjacent lines.

        Only dated obligations are answered: without a printed date there is no unambiguous line to quote. The
        sentence is chosen without VaultAI's duty check, so a rejection here means the validator refused a correct one.
        """
        from vaultai.model.grounding import canon, sentence_around

        if not gold.get("due_date"):
            return None
        due = date.fromisoformat(gold["due_date"])
        lines = list(task.lines)
        for size in (1, 2, 3):
            for i in range(len(lines) - size + 1):
                window = lines[i:i + size]
                if any(b.page != a.page or int(b.id.split(".l")[1]) != int(a.id.split(".l")[1]) + 1
                       for a, b in zip(window, window[1:])):
                    continue
                texts = [canon(ln.text) for ln in window]
                joined = " ".join(texts)
                hit = next((h for h in find_dates(joined, self.date_order) if h.value == due), None)
                if hit is None:
                    continue
                sentence = sentence_around(joined, hit.start, hit.start + len(hit.raw))
                start = joined.find(sentence)
                if start >= len(texts[0]) or (size > 1 and start + len(sentence) <= len(joined) - len(texts[-1])):
                    continue
                return {"kind": gold["kind"], "party": gold.get("party"), "description": gold.get("description") or sentence,
                        "deadline_text": hit.raw, "due_date": gold["due_date"], "line_id": window[0].id,
                        "end_line_id": window[-1].id if size > 1 else None, "quote": sentence}
        return None


def _party_key(name: str | None) -> str:
    words = re.sub(r"[^\w\s]", " ", (name or "").casefold()).split()
    return " ".join(words[1:] if words[:1] == ["the"] else words)


def same_party(a: str | None, b: str | None, parties: Sequence[tuple[str, str]] = ()) -> bool:
    """Equal apart from case and punctuation, or two names the document itself defines as one party.

    "Client" and "Harborview Logistics LLC" are the same party only when the document says so; a shared word or a
    shorter form of a name is not enough.
    """
    ka, kb = _party_key(a), _party_key(b)
    if ka == kb:
        return True
    return bool(ka and kb) and any({ka, kb} <= {_party_key(term), _party_key(name)} for term, name in parties)


@dataclass(frozen=True)
class FoundObligation:
    kind: str
    due_date: str | None
    party: str | None


def match_obligations(expected: Sequence[Mapping], found: Sequence[FoundObligation],
                      parties: Sequence[tuple[str, str]] = ()) -> list[bool]:
    """For each found obligation, whether it matches a distinct expected one.

    The kind and the due date must be equal (both may be empty, as for "within 30 days"), and the party must be the
    same party. Descriptions and citations are not compared: two wordings of one duty are one obligation.
    """
    remaining = list(expected)
    out = []
    for f in found:
        hit = next((e for e in remaining if e["kind"] == f.kind and (e.get("due_date") or None) == (f.due_date or None)
                    and same_party(e.get("party"), f.party, parties)), None)
        if hit is not None:
            remaining.remove(hit)
        out.append(hit is not None)
    return out


@dataclass
class ObligationReport:
    """What the model proposed for one document and what became of it. Codes, counts, and line ids only."""

    expected: int = 0
    proposed: int = 0
    grounded: int = 0
    repeats: int = 0
    duplicates_of_rules: int = 0
    correct: int = 0
    rejected: Counter = field(default_factory=Counter)
    rejected_lines: list[tuple[str | None, str]] = field(default_factory=list)

    def to_json(self) -> dict:
        return {"expected": self.expected, "proposed": self.proposed, "grounded": self.grounded, "repeats": self.repeats,
                "duplicates_of_rules": self.duplicates_of_rules, "correct": self.correct,
                "rejected": sum(self.rejected.values()), "rejected_by_reason": dict(sorted(self.rejected.items())),
                "rejected_lines": [[line, reason] for line, reason in self.rejected_lines]}


def obligation_report(expected: Sequence[Mapping], analyses: Sequence[tuple[AnalyzeTask, object]], doc: Doc,
                      date_order: str, rule_keys: set[tuple[str, str]]) -> ObligationReport:
    """Score the model's own obligations, before VaultAI drops the ones the rules already found.

    Each reply is grounded again exactly as the pipeline grounds it. A grounded obligation that repeats a rules
    obligation (same kind and starting line) is not stored, but it is still a correct answer by the model if it
    matches an expected obligation.
    """
    from vaultai.model import grounding, output
    from vaultai.validation import Issue

    report = ObligationReport(expected=len(expected))
    grounded = []
    parties = tuple(dict.fromkeys(p for task, _ in analyses for p in task.parties))
    for task, raw in analyses:
        parsed = output.parse(task, raw)
        if parsed is None:
            continue
        for cited, outcome in grounding.obligation_outcomes(parsed.obligations, task, doc, date_order):
            report.proposed += 1
            if isinstance(outcome, Issue):
                report.rejected[outcome.reason or "other"] += 1
                report.rejected_lines.append((cited, outcome.reason or "other"))
            elif outcome is None:
                report.repeats += 1
            else:
                grounded.append(outcome)
    report.grounded = len(grounded)
    report.duplicates_of_rules = sum((o.kind.value, o.evidence.line_id if o.evidence else None) in rule_keys
                                     for o in grounded)
    found = [FoundObligation(o.kind.value, o.due_date.isoformat() if o.due_date else None, o.party) for o in grounded]
    report.correct = sum(match_obligations(expected, found, parties))
    return report
