from __future__ import annotations

import io
import json
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest
from PIL import Image

from conftest import make_ctx
from vaultai.config import Settings
from vaultai.context import AppContext
from vaultai.devtools.pdfgen import WHITE, PdfDoc
from vaultai.domain import DocType, ModelKind, ModelStatus, Source
from vaultai.intake.ingest import ingest_bytes
from vaultai.model.base import AnalyzeTask, ClassifyTask, ExtractTask, Task
from vaultai.model.mock import MockModel
from vaultai.parsing.layout import Word
from vaultai.pipeline.process import process_document, request_processing

Row = tuple[tuple[float, str] | tuple[float, str, float], ...]


def pdf(rows: list[str | Row], *, size: tuple[float, float] = (612, 792), top: float = 72, step: float = 18,
        signatures: tuple[tuple[float, float], ...] = (), white: tuple[tuple[float, float, str], ...] = ()) -> bytes:
    """A one-page text PDF: each row is a string at x=72 or a tuple of (x, text[, size]) cells on one baseline."""
    doc = PdfDoc()
    page = doc.page(size)
    y = top
    for row in rows:
        cells = ((72, row),) if isinstance(row, str) else row
        for cell in cells:
            page.text(cell[0], y, cell[1], cell[2] if len(cell) > 2 else 10)
        y += step
    for x, sy in signatures:
        page.signature(x, sy)
    for x, wy, text in white:
        page.text(x, wy, text, 8, color=WHITE)
    return doc.to_bytes()


def invoice_pdf(*, vendor: str = "Zephyr Tools LLC", number: str = "ZT-1001", number_label: str = "Invoice Number:",
                invoice_date: str = "September 28, 2026",
                due: str = "October 28, 2026", items: tuple[tuple[str, str, str, str], ...] = (("Widget", "2", "10.00", "20.00"),),
                subtotal: str = "$20.00", tax: str = "$0.00", tax_label: str = "Tax:", total: str = "$20.00",
                extra: tuple[str, ...] = (),
                white: tuple[tuple[float, float, str], ...] = ()) -> bytes:
    rows: list[str | Row] = [
        ((72, vendor, 16), (450, "INVOICE", 18)),
        "100 Main Street, Springfield, IL 62701",
        ((72, "Bill To:"), (340, number_label), (450, number)),
        ((72, "Harborview Logistics LLC"), (340, "Invoice Date:"), (450, invoice_date)),
        ((340, "Due Date:"), (450, due)),
        ((80, "Description"), (300, "Qty"), (370, "Unit Price"), (480, "Amount")),
        *(((80, d), (300, q), (370, p), (480, a)) for d, q, p, a in items),
        ((370, "Subtotal:"), (480, subtotal)),
        ((370, tax_label), (480, tax)),
        ((370, "Total Due:"), (480, total)),
        *extra,
    ]
    return pdf(rows, white=white)


def receipt_pdf(*, extra: tuple[str, ...] = ()) -> bytes:
    rows: list[str | Row] = [
        ((90, "MAPLE CORNER HARDWARE", 14),),
        ((80, "42 Elm Street, Portland, OR 97201"),),
        "Date: 10/03/2026 2:41 PM",
        "Receipt #: MC-5521",
        ((72, "ITEM"), (300, "QTY"), (380, "PRICE"), (480, "AMOUNT")),
        ((72, "Hammer"), (300, "1"), (380, "18.00"), (480, "18.00")),
        ((72, "Nails box"), (300, "2"), (380, "4.00"), (480, "8.00")),
        ((380, "Subtotal"), (480, "26.00")),
        ((380, "Tax"), (480, "0.00")),
        ((380, "TOTAL"), (480, "$26.00")),
        "Paid: VISA ****1234",
        "Thank you for shopping with us!",
        *extra,
    ]
    return pdf(rows)


def check_pdf(*, number: str = "1043", amount: str = "$1,250.00",
              words: str = "One thousand two hundred fifty and 00/100 DOLLARS",
              micr: str = "A021000021A 000123456789C 001043", signed: bool = True) -> bytes:
    rows: list[str | Row] = [
        ((30, "Harborview Logistics LLC", 11), (300, "First Coastal Bank"), (540, number)),
        ((420, "Date: October 2, 2026"),),
        ((30, "PAY TO THE ORDER OF Zephyr Tools LLC"), (480, amount)),
        ((30, words),),
        ((30, "Memo: Invoice ZT-1001"),),
        ((420, "AUTHORIZED SIGNATURE"),),
        ((120, micr),),
    ]
    # Rows sit at y = 40 + 26 * index; the stroke is drawn just above the signature label (row 5).
    return pdf(rows, size=(612, 260), top=40, step=26, signatures=((420, 160),) if signed else ())


def contract_pdf(*, until: str = "December 31, 2026", services: str = "Provider shall perform the services listed in Exhibit A.",
                 signed: tuple[bool, bool] = (True, False)) -> bytes:
    rows: list[str | Row] = [
        ((200, "SERVICES AGREEMENT", 16),),
        ((230, "Effective Date: January 1, 2026"),),
        'This Services Agreement is entered into by and between Zephyr Tools LLC ("Provider") and',
        'Harborview Logistics LLC ("Client").',
        "1. Term",
        f"This Agreement begins on the Effective Date and continues until {until}.",
        "2. Services",
        services,
        "3. Governing Law",
        "This Agreement is governed by the laws of the State of Delaware.",
        "IN WITNESS WHEREOF, the parties have executed this Agreement.",
        ((72, "ZEPHYR TOOLS LLC"), (330, "HARBORVIEW LOGISTICS LLC")),
        ((72, "By:"), (330, "By: ____________")),
        ((72, "Name: Ana Ruiz"), (330, "Name: Jordan Ellis")),
    ]
    by_y = 72 + 18 * 12
    strokes = tuple(pos for pos, on in (((100, by_y), signed[0]), ((360, by_y), signed[1])) if on)
    return pdf(rows, signatures=strokes)


def png_bytes(size: tuple[int, int] = (200, 100), fmt: str = "PNG", frames: int = 1) -> bytes:
    buf = io.BytesIO()
    images = [Image.new("RGB", size, (255, 255, 255)) for _ in range(frames)]
    images[0].save(buf, format=fmt, save_all=frames > 1, append_images=images[1:])
    return buf.getvalue()


class FakeOcr:
    """Stands in for Tesseract: returns fixed words at a fixed confidence for any page."""

    def __init__(self, lines: list[str], conf: float) -> None:
        self.name = "fake-ocr 1.0"
        self.lines = lines
        self.conf = conf

    def read(self, png: bytes) -> list[Word]:
        return [Word(text, (0.1, 0.05 + 0.05 * i, 0.6, 0.08 + 0.05 * i), self.conf) for i, text in enumerate(self.lines)]


class ScriptedModel:
    """A non-mock adapter whose answers the test chooses; records every task it was given."""

    kind = ModelKind.OPENAI_COMPAT
    name = "scripted"
    vision = False

    def __init__(self, answer: Callable[[Task], object]) -> None:
        self.answer = answer
        self.tasks: list[Task] = []

    def status(self) -> ModelStatus:
        return ModelStatus(kind=self.kind, name=self.name, reachable=True, vision=False, detail="test")

    def run(self, task: Task) -> object:
        self.tasks.append(task)
        return self.answer(task)


def empty_answers(task: Task) -> object:
    if isinstance(task, ClassifyTask):
        return {"doc_type": None, "confidence": 0}
    if isinstance(task, ExtractTask):
        return {"fields": []}
    assert isinstance(task, AnalyzeTask)
    return {"claims": [], "obligations": []}


@pytest.fixture
def mock_ctx(settings: Settings) -> AppContext:
    return make_ctx(settings, MockModel())


def ctx_with(settings: Settings, model: object | None = None, **changes: object) -> AppContext:
    return make_ctx(replace(settings, **changes), model or MockModel())


def ingest(ctx: AppContext, name: str, data: bytes, hint: DocType | None = None):
    return ingest_bytes(ctx, filename=name, data=data, source=Source.UPLOAD, batch_id="test", doc_type_hint=hint)


def run(ctx: AppContext, name: str, data: bytes, hint: DocType | None = None) -> str:
    item = ingest(ctx, name, data, hint)
    assert item.document_id, item.error
    request_processing(ctx, [item.document_id], actor="test")
    process_document(ctx, item.document_id)
    return item.document_id


def doc(ctx: AppContext, doc_id: str) -> dict:
    with ctx.db() as conn:
        return dict(conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone())


def issue_codes(ctx: AppContext, doc_id: str) -> set[str]:
    with ctx.db() as conn:
        return {r["code"] for r in conn.execute("SELECT code FROM issues WHERE doc_id = ?", (doc_id,))}


def field(ctx: AppContext, doc_id: str, key: str) -> dict:
    with ctx.db() as conn:
        return dict(conn.execute("SELECT * FROM fields WHERE doc_id = ? AND key = ?", (doc_id, key)).fetchone())


def lines(ctx: AppContext, doc_id: str) -> list[dict]:
    with ctx.db() as conn:
        return [ln for r in conn.execute("SELECT lines_json FROM pages WHERE doc_id = ? ORDER BY page_no", (doc_id,))
                for ln in json.loads(r["lines_json"])]


@pytest.fixture
def write_worker(tmp_path: Path) -> Callable[[str], Path]:
    def write(body: str) -> Path:
        path = tmp_path / "fake_worker.py"
        path.write_text(body)
        return path

    return write
