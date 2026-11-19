"""Integration tests: real Docling conversions of the fixture documents conform to the output schema."""

import os
from pathlib import Path

import pytest

from crosscodex_ingestion.config import IngestionSettings
from crosscodex_ingestion.converter import convert_document
from crosscodex_ingestion.html_profile import load_html_profiles
from crosscodex_ingestion.output_schema import SCHEMA_VERSION, validate_output
from crosscodex_ingestion.quality import measure_structure

ARTIFACTS = os.environ.get("DOCLING_ARTIFACTS_PATH", "")
PROFILES = load_html_profiles("")
FIXTURES = Path(__file__).parent.parent / "fixtures" / "documents"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not ARTIFACTS or not Path(ARTIFACTS).is_dir(), reason="DOCLING_ARTIFACTS_PATH models directory not available"
    ),
]


@pytest.mark.parametrize(
    ("fixture", "fmt"),
    [("sample.pdf", "pdf"), ("sample.html", "html"), ("sample.docx", "docx"), ("sample.txt", "txt")],
)
async def test_fixture_conversion_conforms_to_the_output_schema(fixture, fmt):
    result = await convert_document(
        str(FIXTURES / fixture), fmt, IngestionSettings(docling_artifacts_path=ARTIFACTS), PROFILES
    )

    # run_conversion adds metadata.structure after conversion; here the real elements must measure and validate.
    result["metadata"]["structure"] = measure_structure(result["elements"])

    validate_output(result)
    assert result["schema_version"] == SCHEMA_VERSION
    assert result["elements"]
