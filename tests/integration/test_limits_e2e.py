"""Integration tests: page limit enforced by the real Docling worker subprocess."""

import os
from pathlib import Path

import pytest
from fpdf import FPDF

from crosscodex_ingestion.config import IngestionSettings
from crosscodex_ingestion.converter import DocumentLimitError, convert_document

ARTIFACTS = os.environ.get("DOCLING_ARTIFACTS_PATH", "")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not ARTIFACTS or not Path(ARTIFACTS).is_dir(), reason="DOCLING_ARTIFACTS_PATH models directory not available"
    ),
]


def _make_pdf(path: Path, pages: int) -> str:
    pdf = FPDF()
    for i in range(pages):
        pdf.add_page()
        pdf.set_font("Helvetica", size=12)
        pdf.cell(0, 10, f"Page {i + 1} of the limits test document")
    pdf.output(str(path))
    return str(path)


async def test_pdf_over_max_pages_fails_instead_of_truncating(tmp_path):
    pdf_path = _make_pdf(tmp_path / "three.pdf", pages=3)

    with pytest.raises(DocumentLimitError, match="2 pages"):
        await convert_document(pdf_path, "pdf", IngestionSettings(max_pages=2, docling_artifacts_path=ARTIFACTS))


async def test_pdf_at_max_pages_converts(tmp_path):
    pdf_path = _make_pdf(tmp_path / "three.pdf", pages=3)

    result = await convert_document(pdf_path, "pdf", IngestionSettings(max_pages=3, docling_artifacts_path=ARTIFACTS))

    assert result["elements"]
