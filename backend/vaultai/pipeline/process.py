"""Processing shell: parse, read, classify, extract, analyze, validate, route, persist.

Every step that understands documents is a pure function elsewhere; this
module sequences them, talks to the database, and enforces the review policy.
process_document converges: running it twice leaves the same records, and
human edits and obligation decisions survive a reprocess.
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
import sqlite3
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import date
from decimal import Decimal
from pathlib import Path

from vaultai import db
from vaultai.analysis import ClauseRec, ObligationRec
from vaultai.analysis.contracts import analyze
from vaultai.classify import MODEL_LINES, Classification, classify_rules, from_hint
from vaultai.context import AppContext
from vaultai.domain import (
    LEGAL_DOC_TYPES,
    SCHEMA_VERSION,
    ApprovalKind,
    AuditAction,
    ClassificationMethod,
    ClauseKind,
    ClauseRisk,
    DocStatus,
    DocType,
    FieldOrigin,
    InvalidTransition,
    IssueCode,
    ModelKind,
    ObligationStatus,
    Route,
    Severity,
    Summary,
    SummaryClaim,
    TextMethod,
    ValueType,
    check_transition,
)
from vaultai.extraction import Found, Item
from vaultai.extraction.rules import extract_fields, kv_label
from vaultai.logging_utils import get_logger
from vaultai.model import grounding, prompts
from vaultai.model.base import AnalyzeTask, ClassifyTask, DocLine, ExtractTask, ModelUnavailable, Task, TranscribeTask
from vaultai.parsing import isolate, ocr
from vaultai.parsing.layout import Doc, Line, Page, Word, merge_words, text_spans
from vaultai.parsing.tables import line_items
from vaultai.pipeline import cache
from vaultai.pipeline.projection import project
from vaultai.schemas import KV_PREFIX, SCHEMAS
from vaultai.validation import PROCESS_CODES, Issue
from vaultai.validation.injection import flag_lines
from vaultai.validation.rules import FieldView, Peer, Snapshot, validate

log = get_logger("vaultai.pipeline")
ACTOR = "pipeline"
AUTO_APPROVER = "policy:auto-approve"
MAX_FACTS = 8
MODEL_UNAVAILABLE = "The local model could not be reached; rules-only results were kept for review."


# ---------------------------------------------------------------- requesting work


def request_processing(ctx: AppContext, doc_ids: list[str], *, actor: str, reprocess: bool = False) -> list[str]:
    queued: list[str] = []
    with ctx.db() as conn:
        for doc_id in dict.fromkeys(doc_ids):
            with db.transaction(conn):
                row = conn.execute("SELECT status FROM documents WHERE id = ?", (doc_id,)).fetchone()
                if row is None:
                    continue
                current = DocStatus(row["status"])
                if not reprocess and current not in (DocStatus.IMPORTED, DocStatus.FAILED):
                    continue
                try:
                    check_transition(current, DocStatus.PROCESSING)
                except InvalidTransition:
                    continue
                conn.execute(
                    "UPDATE documents SET status = ?, stage = ?, error = NULL, approved_at = NULL, approved_by = NULL, "
                    "approval_kind = NULL, deferred_at = NULL, updated_at = ? WHERE id = ?",
                    (DocStatus.PROCESSING.value, "Queued", db.iso(), doc_id),
                )
                db.audit(conn, AuditAction.REPROCESS if reprocess else AuditAction.PROCESS, actor, doc_id=doc_id,
                         before=current.value, after=DocStatus.PROCESSING.value)
            queued.append(doc_id)
    if ctx.queue is not None:
        for doc_id in queued:
            ctx.queue.enqueue(doc_id)
    return queued


# ---------------------------------------------------------------- one document


@dataclass
class Run:
    """What one processing run learned; persisted in a single transaction at the end."""

    pages: list[Page] = field(default_factory=list)
    page_count: int = 0
    image_dir: Path | None = None
    classification: Classification | None = None
    fields: dict[str, Found] = field(default_factory=dict)
    items: list[Item] = field(default_factory=list)
    clauses: list[ClauseRec] = field(default_factory=list)
    obligations: list[ObligationRec] = field(default_factory=list)
    summary: Summary | None = None
    issues: list[Issue] = field(default_factory=list)
    route: Route = Route.FAST
    ocr_engine: str | None = None
    model_unavailable: bool = False


class _Model:
    """Model calls for one run: cached, grounded by the caller, and stopped after the first transport failure."""

    def __init__(self, ctx: AppContext, run: Run, use_cache: bool) -> None:
        self.ctx = ctx
        self.run = run
        # The mock is deterministic and instant, so only a real model's answers are cached.
        self.llm = ctx.model.kind is not ModelKind.MOCK
        self.use_cache = use_cache

    def ask(self, task: Task) -> object | None:
        if self.run.model_unavailable:
            return None
        key = None
        if self.llm:
            body = prompts.messages(task) if not isinstance(task, TranscribeTask) else task.image_png.hex()
            key = cache.key(type(task).__name__, cache.content_hash(body), self.ctx.model.name)
            if self.use_cache:
                with self.ctx.db() as conn:
                    hit = cache.get(conn, key)
                if hit is not None:
                    return hit
        try:
            out = self.ctx.model.run(task)
        except ModelUnavailable:
            self.run.model_unavailable = True
            self.run.issues.append(Issue(IssueCode.MODEL_UNAVAILABLE, MODEL_UNAVAILABLE))
            log.warning("model unavailable model=%s", self.ctx.model.name)
            return None
        if key is not None:
            with self.ctx.db() as conn:
                cache.put(conn, key, out)
        return out


def _stage(ctx: AppContext, doc_id: str, stage: str) -> None:
    with ctx.db() as conn:
        conn.execute("UPDATE documents SET stage = ?, updated_at = ? WHERE id = ? AND status = ?",
                     (stage, db.iso(), doc_id, DocStatus.PROCESSING.value))


def _read_pages(ctx: AppContext, doc_id: str, row: sqlite3.Row, run: Run, model: _Model) -> None:
    settings = ctx.settings
    ext = row["stored_name"].rsplit(".", 1)[-1]
    out_dir = settings.tmp_dir / f"{doc_id}-{secrets.token_hex(4)}"
    out_dir.mkdir(mode=0o700, parents=True)
    run.image_dir = out_dir
    parsed = isolate.parse(settings, ctx.storage.raw_path(row["stored_name"]), ext, out_dir)
    run.page_count = parsed["page_count"]
    pages = [
        Page(p["page_no"], p["width"], p["height"], p["text_method"], tuple(Line.from_json(ln) for ln in p["lines"]),
             p["image"], p["image_sha256"], None, tuple(tuple(m) for m in p.get("marks", ())))
        for p in parsed["pages"]
    ]
    needs_text = [p for p in pages if p.text_method == TextMethod.NONE.value and p.image]
    engine = ocr.engine_for(settings) if needs_text else None
    for i, page in enumerate(needs_text, start=1):
        png = (out_dir / page.image).read_bytes()
        hidden = [ln for ln in page.lines if ln.flag]
        if engine is not None:
            _stage(ctx, doc_id, f"OCR page {i}/{len(needs_text)}")
            run.ocr_engine = engine.name
            words = _ocr_words(ctx, engine, page, png)
            lines = merge_words(words, page.page_no, page.height / page.width)
            confs = [w.conf for w in words if w.conf is not None]
            page = replace(page, text_method=TextMethod.OCR.value if lines else TextMethod.NONE.value,
                           ocr_confidence=round(sum(confs) / len(confs), 4) if confs else None, lines=())
        elif ctx.model.vision:
            _stage(ctx, doc_id, f"Reading page {i}/{len(needs_text)} with the model")
            raw = model.ask(TranscribeTask(png))
            texts, issues = grounding.ground_transcribe(raw) if raw is not None else ([], [])
            run.issues.extend(issues)
            run.route = Route.VISION
            # Transcribed text is model output: it carries the model confidence cap, so it can reach review but never auto-approval.
            lines = [Line(f"p{page.page_no}.l{n}", t, None, grounding.MODEL_CONFIDENCE_CAP, text_spans(t))
                     for n, t in enumerate(texts, start=1)]
            page = replace(page, text_method=TextMethod.VISION.value if lines else TextMethod.NONE.value, lines=())
        else:
            lines = []
        page = replace(page, lines=_renumber(page.page_no, [*lines, *hidden]))
        pages[page.page_no - 1] = page
    run.pages = pages


def _renumber(page_no: int, lines: list[Line]) -> tuple[Line, ...]:
    ordered = sorted(lines, key=lambda ln: ln.bbox[1] if ln.bbox else 0.0) if all(ln.bbox for ln in lines) else lines
    return tuple(replace(ln, id=f"p{page_no}.l{n}") for n, ln in enumerate(ordered, start=1))


def _ocr_words(ctx: AppContext, engine: ocr.OcrEngine, page: Page, png: bytes) -> list[Word]:
    key = cache.key("ocr", page.image_sha256 or "", engine.name)
    with ctx.db() as conn:
        hit = cache.get(conn, key)
    if hit is not None:
        return [Word(w[0], tuple(w[1]), w[2]) for w in hit]
    words = engine.read(png)
    with ctx.db() as conn:
        cache.put(conn, key, [[w.text, list(w.box), w.conf] for w in words])
    return words


def _model_lines(doc: Doc) -> tuple[DocLine, ...]:
    """Quarantined lines (hidden or carrying instructions) never reach a model."""
    return tuple(DocLine(ln.id, ln.text) for ln in doc.visible)


def _classify(ctx: AppContext, doc: Doc, hint: str | None, model: _Model, run: Run) -> Classification:
    if hint:
        return from_hint(DocType(hint))
    rules = classify_rules(doc)
    if not (rules.uncertain and model.llm):
        return rules
    run.route = Route.MODEL if run.route is Route.FAST else run.route
    task = ClassifyTask(_model_lines(doc)[:MODEL_LINES], tuple(DocType))
    raw = model.ask(task)
    if raw is None:
        return rules
    doc_type, confidence, issues = grounding.ground_classify(raw, task)
    run.issues.extend(issues)
    if doc_type is None:
        return rules
    return Classification(doc_type, confidence, ClassificationMethod.MODEL)


def _extract(ctx: AppContext, doc: Doc, doc_type: DocType, model: _Model, run: Run) -> dict[str, Found]:
    settings = ctx.settings
    found = extract_fields(doc, doc_type, settings.date_order)
    schema = SCHEMAS[doc_type]
    wanted = tuple(f for f in schema.fields if f.required and (
        f.key not in found or found[f.key].value is None or found[f.key].confidence < settings.review_confidence_threshold))
    if not wanted or not model.llm:
        return found
    run.route = Route.MODEL if run.route is Route.FAST else run.route
    aliases = [a for f in wanted for a in (*f.aliases, f.label)]
    lines = prompts.lines_for_fields(_model_lines(doc), aliases, prompts.budget_chars(settings))
    task = ExtractTask(doc_type, wanted, lines)
    raw = model.ask(task)
    if raw is None:
        return found
    kept, issues = grounding.ground_extract(raw, task, doc, found, settings.date_order, ctx.model.name)
    run.issues.extend(issues)
    return {**found, **{f.key: f for f in kept}}


def _display(f: Found, value_type: ValueType) -> str:
    if value_type is ValueType.MONEY and f.value:
        return f"{f.currency or ''} {Decimal(f.value):,.2f}".strip()
    return f.value or ""


def _summarize(ctx: AppContext, doc: Doc, doc_type: DocType, fields: Mapping[str, Found], model: _Model, run: Run) -> None:
    schema = SCHEMAS[doc_type]
    facts: dict[str, str] = {}
    for spec in schema.fields:
        f = fields.get(spec.key)
        if f and f.value and f.evidence and f.evidence.line_id and spec.value_type is not ValueType.LONG_TEXT and spec.key != "currency":
            facts[f"{spec.label} [{f.evidence.line_id}]"] = _display(f, spec.value_type)
        if len(facts) >= MAX_FACTS:
            break
    claims: dict[str, SummaryClaim] = {}
    obligations: list[ObligationRec] = []
    budget = prompts.budget_chars(ctx.settings)
    for chunk in prompts.chunks(_model_lines(doc), budget) or [()]:
        task = AnalyzeTask(doc_type, chunk, facts)
        raw = model.ask(task)
        if raw is None:
            break
        got, obs, issues = grounding.ground_analyze(raw, task, doc, ctx.settings.date_order)
        run.issues.extend(issues)
        for c in got:
            claims.setdefault(c.text, SummaryClaim(text=c.text, evidence=list(c.evidence)))
        obligations.extend(obs)
    known = {(o.kind, o.evidence.line_id if o.evidence else None) for o in run.obligations}
    run.obligations.extend(o for o in obligations if (o.kind, o.evidence.line_id if o.evidence else None) not in known)
    if claims:
        mock = ctx.model.kind is ModelKind.MOCK
        run.summary = Summary(claims=list(claims.values()), generator="template" if mock else "model",
                              model_name=ctx.model.name, generated_at=db.now())


def _work(ctx: AppContext, doc_id: str, row: sqlite3.Row, run: Run) -> None:
    model = _Model(ctx, run, use_cache=row["processed_at"] is None)
    _stage(ctx, doc_id, "Reading pages")
    _read_pages(ctx, doc_id, row, run, model)
    doc = flag_lines(Doc(tuple(run.pages)))
    run.pages = list(doc.pages)

    _stage(ctx, doc_id, "Classifying")
    run.classification = _classify(ctx, doc, row["doc_type_hint"], model, run)
    doc_type = run.classification.doc_type

    _stage(ctx, doc_id, "Extracting fields")
    run.fields = _extract(ctx, doc, doc_type, model, run)
    if SCHEMAS[doc_type].has_line_items:
        run.items = line_items(doc)

    if doc_type in LEGAL_DOC_TYPES or doc_type is DocType.CORRESPONDENCE:
        _stage(ctx, doc_id, "Analyzing clauses")
    analysis = analyze(doc, doc_type, run.fields, ctx.settings.date_order)
    run.clauses, run.obligations = list(analysis.clauses), list(analysis.obligations)
    _summarize(ctx, doc, doc_type, run.fields, model, run)
    _stage(ctx, doc_id, "Validating")


# ---------------------------------------------------------------- persistence


def _field_row(doc_id: str, key: str, label: str, value_type: ValueType, required: bool, f: Found | None,
               extracted_at: str) -> tuple:
    page, bbox, line_id, ev_text = db.evidence_columns(f.evidence if f else None)
    return (
        doc_id, key, label, value_type.value, int(required), f.value if f else None, f.raw_text if f else None,
        f.currency if f else None, f.confidence if f else 0.0, (f.origin if f else FieldOrigin.RULES).value,
        page, bbox, line_id, ev_text, f.model_name if f else None, SCHEMA_VERSION, extracted_at,
    )


_FIELD_INSERT = (
    "INSERT INTO fields (doc_id, key, label, value_type, required, ai_value, raw_text, currency, confidence, ai_origin, "
    "page, bbox_json, line_id, evidence_text, model_name, schema_version, extracted_at) "
    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
)


def _write_records(conn: sqlite3.Connection, doc_id: str, run: Run) -> None:
    edits = {r["key"]: r for r in conn.execute("SELECT * FROM fields WHERE doc_id = ? AND edited_at IS NOT NULL", (doc_id,))}
    decided = {(r["kind"], r["description"]): r["status"] for r in conn.execute(
        "SELECT kind, description, status FROM obligations WHERE doc_id = ? AND status != ?",
        (doc_id, ObligationStatus.OPEN.value))}
    for table in ("pages", "fields", "line_items", "clauses", "obligations", "issues"):
        conn.execute(f"DELETE FROM {table} WHERE doc_id = ?", (doc_id,))  # noqa: S608 - fixed table names

    for p in run.pages:
        conn.execute(
            "INSERT INTO pages (doc_id, page_no, width, height, text_method, ocr_confidence, page_hash, image_name, lines_json) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (doc_id, p.page_no, p.width, p.height, p.text_method, p.ocr_confidence, p.image_sha256 or "",
             p.image, json.dumps([ln.page_line() for ln in p.lines])),
        )

    schema = SCHEMAS[run.classification.doc_type]
    now = db.iso()
    rows = [_field_row(doc_id, s.key, s.label, s.value_type, s.required, run.fields.get(s.key), now) for s in schema.fields]
    rows += [_field_row(doc_id, k, kv_label(k), ValueType.TEXT, False, f, now)
             for k, f in run.fields.items() if k.startswith(KV_PREFIX)]
    present = {r[1] for r in rows}
    rows += [(doc_id, k, e["label"], e["value_type"], e["required"], None, None, None, 0.0, FieldOrigin.RULES.value,
              None, None, None, None, None, SCHEMA_VERSION, now) for k, e in edits.items() if k not in present]
    conn.executemany(_FIELD_INSERT, rows)
    for key, e in edits.items():
        conn.execute("UPDATE fields SET human_value = ?, edited_by = ?, edited_at = ? WHERE doc_id = ? AND key = ?",
                     (e["human_value"], e["edited_by"], e["edited_at"], doc_id, key))

    for idx, item in enumerate(run.items):
        conn.execute(
            "INSERT INTO line_items (doc_id, idx, description, quantity, unit_price, amount, page, bbox_json, line_id, evidence_text) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (doc_id, idx, item.description, *(str(v) if v is not None else None for v in (item.quantity, item.unit_price, item.amount)),
             *db.evidence_columns(item.evidence)),
        )
    for c in run.clauses:
        conn.execute(
            "INSERT INTO clauses (doc_id, kind, heading, text, risk, risk_reason, interpretation, origin, page, bbox_json, line_id, evidence_text) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (doc_id, c.kind.value, c.heading, c.text, c.risk.value, c.risk_reason, c.interpretation, c.origin.value,
             *db.evidence_columns(c.evidence)),
        )
    for o in run.obligations:
        status = decided.get((o.kind.value, o.description), ObligationStatus.OPEN.value)
        conn.execute(
            "INSERT INTO obligations (doc_id, kind, party, description, due_date, status, origin, page, bbox_json, line_id, "
            "evidence_text, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (doc_id, o.kind.value, o.party, o.description, o.due_date.isoformat() if o.due_date else None, status,
             o.origin.value, *db.evidence_columns(o.evidence), now, now),
        )
    _insert_issues(conn, doc_id, run.issues)


def _insert_issues(conn: sqlite3.Connection, doc_id: str, issues: Iterable[Issue]) -> None:
    now = db.iso()
    conn.executemany(
        "INSERT INTO issues (doc_id, code, severity, message, field_key, page, bbox_json, line_id, evidence_text, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        [(doc_id, i.code.value, i.severity.value, i.message, i.field_key, *db.evidence_columns(i.evidence), now)
         for i in issues],
    )


def _move_images(ctx: AppContext, doc_id: str, run: Run) -> None:
    target = ctx.storage.derived_doc_dir(doc_id)
    shutil.rmtree(target, ignore_errors=True)
    if run.image_dir is not None and any(p.image for p in run.pages):
        os.replace(run.image_dir, target)
        run.image_dir = None


# ---------------------------------------------------------------- validation snapshot (shared with revalidate)


def _snapshot(ctx: AppContext, conn: sqlite3.Connection, doc_id: str, doc_type: DocType) -> Snapshot:
    fields: dict[str, FieldView] = {}
    for r in conn.execute("SELECT * FROM fields WHERE doc_id = ? ORDER BY id", (doc_id,)):
        edited = r["edited_at"] is not None
        fields[r["key"]] = FieldView(
            key=r["key"], label=r["label"], value_type=ValueType(r["value_type"]), required=bool(r["required"]),
            value=r["human_value"] if edited else r["ai_value"], raw_text=None if edited else r["raw_text"],
            currency=r["currency"], confidence=1.0 if edited else r["confidence"],
            origin=FieldOrigin.HUMAN if edited else FieldOrigin(r["ai_origin"]), evidence=db.evidence_from_row(r),
        )
    items = tuple(
        Item(r["description"], *(Decimal(r[k]) if r[k] is not None else None for k in ("quantity", "unit_price", "amount")),
             db.evidence_from_row(r))
        for r in conn.execute("SELECT * FROM line_items WHERE doc_id = ? ORDER BY idx", (doc_id,))
    )
    pages, ocr_conf = [], {}
    for r in conn.execute("SELECT * FROM pages WHERE doc_id = ? ORDER BY page_no", (doc_id,)):
        lines = tuple(Line(d["id"], d["text"], tuple(d["bbox"]) if d.get("bbox") else None, d.get("confidence"), (),
                           d.get("flag")) for d in json.loads(r["lines_json"]))
        pages.append(Page(r["page_no"], r["width"], r["height"], r["text_method"], lines, r["image_name"], r["page_hash"],
                          r["ocr_confidence"]))
        if r["text_method"] in (TextMethod.OCR.value, TextMethod.VISION.value):
            ocr_conf[r["page_no"]] = r["ocr_confidence"]
    clauses = tuple(
        ClauseRec(ClauseKind(r["kind"]), r["heading"], r["text"], ClauseRisk(r["risk"]), r["risk_reason"],
                  db.evidence_from_row(r), r["interpretation"], FieldOrigin(r["origin"]))
        for r in conn.execute("SELECT * FROM clauses WHERE doc_id = ? ORDER BY id", (doc_id,))
    )
    peers = tuple(
        Peer(r["id"], DocType(r["doc_type"]) if r["doc_type"] else None, r["counterparty"], r["primary_id"], r["doc_date"],
             Decimal(r["total_amount"]) if r["total_amount"] else None)
        for r in conn.execute(
            "SELECT id, doc_type, counterparty, primary_id, doc_date, total_amount FROM documents "
            "WHERE id != ? AND doc_type = ? AND status NOT IN (?, ?, ?)",
            (doc_id, doc_type.value, DocStatus.REJECTED.value, DocStatus.FAILED.value, DocStatus.IMPORTED.value))
    )
    schema = SCHEMAS[doc_type]
    counterparty = fields.get(schema.counterparty or "")
    history = tuple(
        Decimal(r["total_amount"]) for r in conn.execute(
            "SELECT total_amount FROM documents WHERE id != ? AND doc_type = ? AND lower(counterparty) = lower(?) "
            "AND status IN (?, ?) AND total_amount IS NOT NULL ORDER BY created_at",
            (doc_id, doc_type.value, counterparty.value if counterparty and counterparty.value else "",
             DocStatus.APPROVED.value, DocStatus.EXPORTED.value))
    )
    return Snapshot(doc_type, fields, items, Doc(tuple(pages)), ocr_conf, clauses, peers, history,
                    ctx.settings.review_confidence_threshold, ctx.settings.unusual_amount)


def revalidate(ctx: AppContext, conn: sqlite3.Connection, doc_id: str) -> None:
    row = conn.execute("SELECT doc_type, filename FROM documents WHERE id = ?", (doc_id,)).fetchone()
    if row is None or row["doc_type"] is None:
        return
    doc_type = DocType(row["doc_type"])
    snap = _snapshot(ctx, conn, doc_id, doc_type)
    conn.execute(f"DELETE FROM issues WHERE doc_id = ? AND code NOT IN ({','.join('?' * len(PROCESS_CODES))})",  # noqa: S608
                 (doc_id, *(c.value for c in PROCESS_CODES)))
    _insert_issues(conn, doc_id, validate(snap))

    issues = [(IssueCode(r["code"]), Severity(r["severity"]))
              for r in conn.execute("SELECT code, severity FROM issues WHERE doc_id = ?", (doc_id,))]
    deadlines = [date.fromisoformat(r["due_date"]) for r in conn.execute(
        "SELECT due_date FROM obligations WHERE doc_id = ? AND status = ? AND due_date IS NOT NULL",
        (doc_id, ObligationStatus.OPEN.value))]
    p = project(doc_type, snap.fields, deadlines, (c.risk for c in snap.clauses), issues)
    injected = any(code in (IssueCode.PROMPT_INJECTION, IssueCode.HIDDEN_TEXT) for code, _ in issues)
    conn.execute(
        "UPDATE documents SET title = ?, counterparty = ?, doc_date = ?, total_amount = ?, currency = ?, primary_id = ?, "
        "next_deadline = ?, confidence = ?, risk_level = ?, injection_suspected = ?, updated_at = ? WHERE id = ?",
        (p.title, p.counterparty, p.doc_date, p.total_amount, p.currency, p.primary_id, p.next_deadline, p.confidence,
         p.risk_level.value, int(injected), db.iso(), doc_id),
    )
    body = "\n".join([*(ln.text for ln in snap.doc.visible), *(f.value for f in snap.fields.values() if f.value)])
    conn.execute("DELETE FROM doc_fts WHERE doc_id = ?", (doc_id,))
    conn.execute("INSERT INTO doc_fts (doc_id, filename, body) VALUES (?, ?, ?)", (doc_id, row["filename"], body))


# ---------------------------------------------------------------- routing


def decide(doc_type: DocType, issues: list[tuple[IssueCode, Severity]], required: list[FieldView],
           auto_types: frozenset[DocType], auto_min: float, review_min: float) -> tuple[DocStatus, str | None]:
    """(final status, the auto-approve rule that applied, if any). Pure: the review policy in one place."""
    blocking = [code for code, sev in issues if sev in (Severity.WARNING, Severity.ERROR)]
    injected = any(code in (IssueCode.PROMPT_INJECTION, IssueCode.HIDDEN_TEXT) for code in blocking)
    if blocking or injected:
        return DocStatus.NEEDS_REVIEW, None
    if any(f.value is None or f.confidence < review_min for f in required):
        return DocStatus.NEEDS_REVIEW, None
    confident = all(f.confidence >= auto_min for f in required)
    if doc_type in auto_types and doc_type not in LEGAL_DOC_TYPES and confident:
        rule = (f"{doc_type.value} auto-approve policy: no warnings or errors, no injection, and every required field "
                f"read with confidence of at least {auto_min:.2f}")
        return DocStatus.APPROVED, rule
    return DocStatus.READY, None


def _finish(ctx: AppContext, conn: sqlite3.Connection, doc_id: str, run: Run, ms: int) -> DocStatus:
    c = run.classification
    route = run.route
    conn.execute(
        "UPDATE documents SET doc_type = ?, doc_type_confidence = ?, classification_method = ?, page_count = ?, route = ?, "
        "model_kind = ?, model_name = ?, schema_version = ?, ocr_engine = ?, summary_json = ?, updated_at = ? WHERE id = ?",
        (c.doc_type.value, c.confidence, c.method.value, run.page_count, route.value, ctx.model.kind.value,
         ctx.model.name, SCHEMA_VERSION, run.ocr_engine, run.summary.model_dump_json() if run.summary else None,
         db.iso(), doc_id),
    )
    status = DocStatus.PROCESSING
    for nxt, stage in ((DocStatus.EXTRACTED, "Validating"), (DocStatus.VALIDATED, "Routing")):
        check_transition(status, nxt)
        conn.execute("UPDATE documents SET status = ?, stage = ? WHERE id = ?", (nxt.value, stage, doc_id))
        status = nxt
        if nxt is DocStatus.EXTRACTED:
            revalidate(ctx, conn, doc_id)

    issues = [(IssueCode(r["code"]), Severity(r["severity"]))
              for r in conn.execute("SELECT code, severity FROM issues WHERE doc_id = ?", (doc_id,))]
    required = [f for f in _snapshot(ctx, conn, doc_id, c.doc_type).fields.values() if f.required]
    s = ctx.settings
    final, rule = decide(c.doc_type, issues, required, s.auto_approve_types, s.auto_approve_min_confidence,
                         s.review_confidence_threshold)
    check_transition(status, final)
    now = db.iso()
    conn.execute(
        "UPDATE documents SET status = ?, stage = NULL, error = NULL, processing_ms = ?, processed_at = ?, updated_at = ?, "
        "approved_at = ?, approved_by = ?, approval_kind = ? WHERE id = ?",
        (final.value, ms, now, now, now if rule else None, AUTO_APPROVER if rule else None,
         ApprovalKind.POLICY.value if rule else None, doc_id),
    )
    db.audit(conn, AuditAction.PROCESS, ACTOR, doc_id=doc_id, before=DocStatus.PROCESSING.value, after=final.value,
             note=f"route={route.value} pages={run.page_count}")
    if rule:
        db.audit(conn, AuditAction.AUTO_APPROVE, AUTO_APPROVER, doc_id=doc_id, before=DocStatus.VALIDATED.value,
                 after=DocStatus.APPROVED.value, note=rule)
    return final


def _fail(ctx: AppContext, doc_id: str, exc: Exception) -> None:
    reason = f" ({exc.reason})" if isinstance(exc, isolate.ParseFailed) else ""
    message = f"{type(exc).__name__}{reason}: the document could not be processed."
    with ctx.db() as conn, db.transaction(conn):
        row = conn.execute("SELECT status FROM documents WHERE id = ?", (doc_id,)).fetchone()
        if row is None or DocStatus(row["status"]) is not DocStatus.PROCESSING:
            return
        conn.execute("UPDATE documents SET status = ?, stage = NULL, error = ?, updated_at = ? WHERE id = ?",
                     (DocStatus.FAILED.value, message, db.iso(), doc_id))
        db.audit(conn, AuditAction.PROCESS, ACTOR, doc_id=doc_id, before=DocStatus.PROCESSING.value,
                 after=DocStatus.FAILED.value, note=f"failed: {type(exc).__name__}{reason}")


def process_document(ctx: AppContext, doc_id: str) -> None:
    started = time.monotonic()
    with ctx.db() as conn:
        row = conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
    if row is None or row["status"] != DocStatus.PROCESSING.value:
        return
    run = Run()
    try:
        _work(ctx, doc_id, row, run)
        ms = int((time.monotonic() - started) * 1000)
        with ctx.db() as conn, db.transaction(conn):
            current = conn.execute("SELECT status FROM documents WHERE id = ?", (doc_id,)).fetchone()
            if current is None or current["status"] != DocStatus.PROCESSING.value:
                return
            _move_images(ctx, doc_id, run)
            _write_records(conn, doc_id, run)
            final = _finish(ctx, conn, doc_id, run, ms)
        log.info("processed doc=%s pages=%d ms=%d status=%s route=%s", doc_id, run.page_count, ms, final.value, run.route.value)
    except Exception as exc:  # noqa: BLE001 - any failure is recorded on the document, never raised into the worker
        log.warning("processing failed doc=%s error=%s", doc_id, type(exc).__name__)
        _fail(ctx, doc_id, exc)
    finally:
        if run.image_dir is not None:
            shutil.rmtree(run.image_dir, ignore_errors=True)
