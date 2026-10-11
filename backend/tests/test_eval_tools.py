from __future__ import annotations

import pypdfium2 as pdfium
import pytest

from vaultai.config import REPO_ROOT
from vaultai.devtools.evaluation import STRESS_LABELS, OracleModel, relabel_pdf
from vaultai.domain import DocType
from vaultai.model.base import DocLine, ExtractTask
from vaultai.schemas import SCHEMAS


@pytest.mark.parametrize("path", sorted(STRESS_LABELS))
def test_every_stress_substitution_applies_and_keeps_the_values(path: str) -> None:
    out = relabel_pdf((REPO_ROOT / "samples" / path).read_bytes(), STRESS_LABELS[path])
    pdf = pdfium.PdfDocument(out)
    text = " ".join(pdf[i].get_textpage().get_text_bounded() for i in range(len(pdf)))
    assert all(new in text for _, new in STRESS_LABELS[path])
    assert not any(old in text for old, _ in STRESS_LABELS[path])


def test_oracle_answers_only_from_lines_it_was_shown() -> None:
    expected = {"x.pdf": {"doc_type": "invoice", "fields": {"total": "20.00", "invoice_number": "ZT-1"}}}
    oracle = OracleModel(expected, current="x.pdf")
    schema = SCHEMAS[DocType.INVOICE]
    task = ExtractTask(DocType.INVOICE, (schema.field("total"), schema.field("invoice_number")),
                       (DocLine("p1.l4", "Balance Payable: $20.00"),))
    assert oracle.run(task) == {"fields": [
        {"key": "total", "value": "20.00", "line_id": "p1.l4", "quote": "Balance Payable: $20.00", "confidence": 0.9}]}
