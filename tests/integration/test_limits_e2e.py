"""Integration tests: page, CPU and default resource limits enforced by the real Docling worker subprocess."""

import os
from pathlib import Path

import pytest
from fpdf import FPDF

from crosscodex_ingestion.config import IngestionSettings
from crosscodex_ingestion.converter import DocumentLimitError, convert_document
from crosscodex_ingestion.html_profile import load_html_profiles
from crosscodex_ingestion.mapping import load_registry

ARTIFACTS = os.environ.get("DOCLING_ARTIFACTS_PATH", "")
PROFILES = load_html_profiles("")
MAPPINGS = load_registry("")
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
        # A heading per page gives the fallback structurer a control per page; plain prose has no structure.
        pdf.set_font("Helvetica", "B", 20)
        pdf.cell(0, 12, f"{i + 1}. Heading number {i + 1}", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", size=12)
        pdf.multi_cell(0, 8, f"Body text of page {i + 1} of the limits test document, long enough to be a paragraph.")
    pdf.output(str(path))
    return str(path)


async def test_pdf_over_max_pages_fails_instead_of_truncating(tmp_path):
    pdf_path = _make_pdf(tmp_path / "three.pdf", pages=3)

    with pytest.raises(DocumentLimitError, match="2 pages"):
        await convert_document(
            pdf_path, "pdf", IngestionSettings(max_pages=2, docling_artifacts_path=ARTIFACTS), PROFILES, MAPPINGS
        )


async def test_pdf_at_max_pages_converts(tmp_path):
    pdf_path = _make_pdf(tmp_path / "three.pdf", pages=3)

    result = await convert_document(
        pdf_path, "pdf", IngestionSettings(max_pages=3, docling_artifacts_path=ARTIFACTS), PROFILES, MAPPINGS
    )

    assert result["elements"]
    assert result["metadata"]["structure_method"] == "heading"


async def test_real_worker_over_cpu_limit_reports_the_cpu_limit(tmp_path):
    """The kernel's SIGXCPU reaches the parent as an actionable CPU-limit error, not an exit code."""
    pdf_path = _make_pdf(tmp_path / "one.pdf", pages=1)

    # Importing Docling alone costs more than one CPU-second.
    with pytest.raises(DocumentLimitError, match="RLIMIT_CPU"):
        await convert_document(
            pdf_path, "pdf", IngestionSettings(rlimit_cpu=1, docling_artifacts_path=ARTIFACTS), PROFILES, MAPPINGS
        )


async def test_hundred_page_pdf_converts_with_default_limits(tmp_path):
    pdf_path = _make_pdf(tmp_path / "hundred.pdf", pages=100)

    result = await convert_document(
        pdf_path, "pdf", IngestionSettings(docling_artifacts_path=ARTIFACTS), PROFILES, MAPPINGS
    )

    assert result["metadata"]["page_count"] == 100


@pytest.mark.parametrize(("name", "format_hint"), [("nist-sp800-171r3-excerpt.pdf", "pdf"), ("sample.docx", "docx")])
async def test_real_documents_convert_with_default_limits(name, format_hint):
    result = await convert_document(
        str(FIXTURES_DIR / name), format_hint, IngestionSettings(docling_artifacts_path=ARTIFACTS), PROFILES, MAPPINGS
    )

    assert result["elements"]
