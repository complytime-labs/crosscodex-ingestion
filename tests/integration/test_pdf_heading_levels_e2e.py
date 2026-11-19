"""Integration tests: real NIST PDF pages convert to nested headings (levels from section numbering)."""

import os
from pathlib import Path

import pytest

from crosscodex_ingestion.config import IngestionSettings
from crosscodex_ingestion.converter import convert_document
from crosscodex_ingestion.html_profile import load_html_profiles

ARTIFACTS = os.environ.get("DOCLING_ARTIFACTS_PATH", "")
FIXTURES = Path(__file__).parent.parent / "fixtures" / "documents"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not ARTIFACTS or not Path(ARTIFACTS).is_dir(), reason="DOCLING_ARTIFACTS_PATH models directory not available"
    ),
]


async def _heading_tree(fixture: str) -> list[tuple[str, int, str | None]]:
    """Return (content, level, parent heading content) for each heading of the converted *fixture*."""
    result = await convert_document(
        str(FIXTURES / fixture), "pdf", IngestionSettings(docling_artifacts_path=ARTIFACTS), load_html_profiles("")
    )
    by_id = {e["element_id"]: e for e in result["elements"]}
    return [
        (e["content"], e["level"], by_id[e["parent_id"]]["content"] if e["parent_id"] else None)
        for e in result["elements"]
        if e["type"] == "heading"
    ]


async def test_sp800_171r3_requirements_nest_under_their_family_and_section():
    # Pages 13-17: section 2 with an example requirement (03.13.11) in 2.2, then section 3 and family 3.1.
    assert await _heading_tree("nist-sp800-171r3-excerpt.pdf") == [
        ("2. The Fundamentals", 1, None),
        ("2.1. Security Requirement Assumptions", 2, "2. The Fundamentals"),
        ("2.2. Security Requirement Development Methodology", 2, "2. The Fundamentals"),
        ("ORGANIZATION-DEFINED PARAMETERS", 3, "2.2. Security Requirement Development Methodology"),
        ("03.13.11 Cryptographic Protection", 3, "2.2. Security Requirement Development Methodology"),
        ("DISCUSSION", 4, "03.13.11 Cryptographic Protection"),
        ("REFERENCES", 4, "03.13.11 Cryptographic Protection"),
        ("ASSESSING SECURITY REQUIREMENTS", 4, "03.13.11 Cryptographic Protection"),
        ("3. The Security Requirements", 1, None),
        ("SCOPE AND APPLICABILITY OF SECURITY REQUIREMENTS", 2, "3. The Security Requirements"),
        ("3.1. Access Control", 2, "3. The Security Requirements"),
        ("03.01.01 Account Management", 3, "3.1. Access Control"),
        ("DISCUSSION", 4, "03.01.01 Account Management"),
    ]


async def test_sp800_53r5_controls_nest_under_their_chapter_and_family():
    # Pages 43, 45, 67, 68. "3. Include ..." is an item of AC-8's list that Docling labels a heading;
    # it repeats CHAPTER THREE's number, so it is not taken as a new chapter.
    tree = await _heading_tree("nist-sp800-53r5-excerpt.pdf")

    assert tree[:4] == [
        ("CHAPTER THREE", 1, None),
        ("THE CONTROLS", 2, "CHAPTER THREE"),
        ("3.1 ACCESS CONTROL", 2, "CHAPTER THREE"),
        ("Quick link to Access Control Summary Table", 3, "3.1 ACCESS CONTROL"),
    ]
    below_family = tree[4:]
    contents = [content for content, _level, _parent in below_family]
    assert {"AC-1 POLICY AND PROCEDURES", "AC-8 SYSTEM USE NOTIFICATION", "AC-9 PREVIOUS LOGON NOTIFICATION"} <= set(
        contents
    )
    assert any(content.startswith("3. Include a description") for content in contents)
    assert all((level, parent) == (3, "3.1 ACCESS CONTROL") for _content, level, parent in below_family)
