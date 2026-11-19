"""Integration tests: page, CPU and default resource limits enforced by the real Docling worker subprocess."""

import os
from pathlib import Path

import pytest
from fpdf import FPDF

from crosscodex_ingestion.config import IngestionSettings
from crosscodex_ingestion.converter import DocumentLimitError, convert_document
from crosscodex_ingestion.html_profile import load_html_profiles

ARTIFACTS = os.environ.get("DOCLING_ARTIFACTS_PATH", "")
PROFILES = load_html_profiles("")
FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "documents"

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
        await convert_document(
            pdf_path, "pdf", IngestionSettings(max_pages=2, docling_artifacts_path=ARTIFACTS), PROFILES
        )


async def test_pdf_at_max_pages_converts(tmp_path):
    pdf_path = _make_pdf(tmp_path / "three.pdf", pages=3)

    result = await convert_document(
        pdf_path, "pdf", IngestionSettings(max_pages=3, docling_artifacts_path=ARTIFACTS), PROFILES
    )

    assert result["elements"]


async def test_real_worker_over_cpu_limit_reports_the_cpu_limit(tmp_path):
    """The kernel's SIGXCPU reaches the parent as an actionable CPU-limit error, not an exit code."""
    pdf_path = _make_pdf(tmp_path / "one.pdf", pages=1)

    # Importing Docling alone costs more than one CPU-second.
    with pytest.raises(DocumentLimitError, match="RLIMIT_CPU"):
        await convert_document(
            pdf_path, "pdf", IngestionSettings(rlimit_cpu=1, docling_artifacts_path=ARTIFACTS), PROFILES
        )


async def test_hundred_page_pdf_converts_with_default_limits(tmp_path):
    pdf_path = _make_pdf(tmp_path / "hundred.pdf", pages=100)

    result = await convert_document(pdf_path, "pdf", IngestionSettings(docling_artifacts_path=ARTIFACTS), PROFILES)

    assert result["metadata"]["page_count"] == 100


@pytest.mark.parametrize(("name", "format_hint"), [("sample.pdf", "pdf"), ("sample.docx", "docx")])
async def test_real_documents_convert_with_default_limits(name, format_hint):
    result = await convert_document(
        str(FIXTURES_DIR / name), format_hint, IngestionSettings(docling_artifacts_path=ARTIFACTS), PROFILES
    )

    assert result["elements"]
