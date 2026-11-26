"""Integration tests: the real Docling worker gives reproducible output and accurate metadata."""

import os
import time
from pathlib import Path

import pytest

from crosscodex_ingestion.config import IngestionSettings
from crosscodex_ingestion.converter import convert_document
from crosscodex_ingestion.html_profile import load_html_profiles
from crosscodex_ingestion.mapping import load_registry

ARTIFACTS = os.environ.get("DOCLING_ARTIFACTS_PATH", "")
PROFILES = load_html_profiles("")
MAPPINGS = load_registry("")
NIST_EXCERPT_PDF = str(Path(__file__).parent.parent / "fixtures" / "documents" / "nist-sp800-171r3-excerpt.pdf")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not ARTIFACTS or not Path(ARTIFACTS).is_dir(), reason="DOCLING_ARTIFACTS_PATH models directory not available"
    ),
]


async def test_same_pdf_converts_to_identical_elements():
    settings = IngestionSettings(docling_artifacts_path=ARTIFACTS)

    first = await convert_document(NIST_EXCERPT_PDF, "pdf", settings, PROFILES, MAPPINGS)
    second = await convert_document(NIST_EXCERPT_PDF, "pdf", settings, PROFILES, MAPPINGS)

    assert first["elements"]
    assert first["elements"] == second["elements"]


async def test_pdf_metadata_reports_pages_and_conversion_time():
    started = time.monotonic()
    result = await convert_document(
        NIST_EXCERPT_PDF, "pdf", IngestionSettings(docling_artifacts_path=ARTIFACTS), PROFILES, MAPPINGS
    )
    elapsed_ms = (time.monotonic() - started) * 1000

    assert result["metadata"]["page_count"] >= 1
    # The worker is most of the call: allow for the temp copy and cleanup around it.
    assert result["metadata"]["processing_time_ms"] >= elapsed_ms * 0.9
