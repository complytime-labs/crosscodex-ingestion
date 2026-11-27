"""Layout repair on captured real documents (tests/fixtures/layout, see its PROVENANCE.md).

Each case is a DoclingDocument captured from an excerpt PDF in tests/fixtures/documents, so these specs need
docling-core but no converter or models. The counts are pinned from the live conversions (the feasibility
gate in PROVENANCE.md); a difference after a Docling upgrade means the layouts must be re-captured.
"""

import functools
import itertools
import json
from pathlib import Path
from typing import Any

import pytest
from docling_core.types.doc import DoclingDocument

from crosscodex_ingestion.extractor import ExtractionResult, extract_elements
from crosscodex_ingestion.fallback import go_fields, structure_layout
from crosscodex_ingestion.layout_repair import find_furniture
from crosscodex_ingestion.quality import measure_structure
from tests.fixtures.layout.expectations import (
    ASVS,
    CASES,
    CPG,
    DROPPED,
    NIST_53B,
    NIST_171R3,
    TABLE_COUNT,
    TABLE_REJOIN_COUNT,
    WARNINGS,
)

LAYOUTS = Path(__file__).parent.parent / "fixtures" / "layout"
# The ASVS PDF prints a requirement's ID without the "V" its Shortcode has in the JSON release (1.2.3 is V1.2.3).
ASVS_SHORTCODE_PREFIX = "V"


@functools.cache
def _doc(case: str) -> DoclingDocument:
    return DoclingDocument.model_validate(json.loads((LAYOUTS / case / "docling.json").read_text(encoding="utf-8")))


@functools.cache
def _extracted(case: str) -> ExtractionResult:
    return extract_elements(_doc(case), None, "pdf")


def _tables(case: str) -> list[dict[str, Any]]:
    return [e for e in _extracted(case).elements if e["type"] == "table"]


def _joined(case: str) -> list[dict[str, Any]]:
    return [e for e in _tables(case) if len(set(e["row_pages"])) > 1]


def _asvs_oracle() -> dict[str, str]:
    oracle: dict[str, str] = json.loads((LAYOUTS / ASVS / "oracle.json").read_text(encoding="utf-8"))
    return oracle


def _asvs_parents(table: dict[str, Any], oracle: dict[str, str]) -> set[str]:
    """The oracle parents of the requirement IDs in the table's first column."""
    ids = (ASVS_SHORTCODE_PREFIX + row[0].strip() for row in table["cells"][table["header_rows"] :])
    return {oracle[rid] for rid in ids if rid in oracle}


@pytest.mark.parametrize("case", CASES)
def test_roundtrip_markdown_matches_capture(case: str) -> None:
    captured = (LAYOUTS / case / "export.md").read_text(encoding="utf-8")
    assert _doc(case).export_to_markdown() == captured


def test_asvs_join_count() -> None:
    # Excerpt pages 1-3 are one requirement table (V10.4), 3-4 another (V10.5), and 9-10 the header-less
    # contributor list. Pages 6-8 (differing widths) and 4-5 (a heading between) stay apart.
    joined = _joined(ASVS)
    assert [(e["page"], e["header_rows"], e["row_pages"]) for e in joined] == [
        (1, 1, [1] * 5 + [2] * 9 + [3] * 3),
        (3, 1, [3] * 4 + [4] * 2),
        (9, 0, [9] * 8 + [10] * 18),
    ]
    assert all(len(e["cells"]) == len(e["row_pages"]) for e in joined)
    assert measure_structure(_extracted(ASVS).elements)["table_rejoin_count"] == TABLE_REJOIN_COUNT[ASVS]
    assert measure_structure(_extracted(ASVS).elements)["table_count"] == TABLE_COUNT[ASVS]


def test_sp800_53b_join_count() -> None:
    # Excerpt pages 1-3 are one baseline table (SI family) with a 2-row header repeated on each page. Page 4
    # continues it too, but Docling reads that page's header text differently, so it stays apart.
    joined = _joined(NIST_53B)
    assert [(e["page"], e["header_rows"], e["row_pages"]) for e in joined] == [
        (1, 2, [1] * 34 + [2] * 42 + [3] * 41),
    ]
    assert len(joined[0]["cells"]) == len(joined[0]["row_pages"])
    assert measure_structure(_extracted(NIST_53B).elements)["table_rejoin_count"] == TABLE_REJOIN_COUNT[NIST_53B]
    assert measure_structure(_extracted(NIST_53B).elements)["table_count"] == TABLE_COUNT[NIST_53B]


def test_sp800_53b_dropped_and_warnings() -> None:
    assert _extracted(NIST_53B).metadata["dropped"] == DROPPED[NIST_53B]
    assert measure_structure(_extracted(NIST_53B).elements)["warnings"] == WARNINGS[NIST_53B]


@pytest.mark.parametrize("case", [CPG, NIST_171R3])
def test_unsplit_case_join_count(case: str) -> None:
    assert _joined(case) == []
    assert measure_structure(_extracted(case).elements)["table_rejoin_count"] == TABLE_REJOIN_COUNT[case]


def test_asvs_dropped_and_warnings() -> None:
    assert _extracted(ASVS).metadata["dropped"] == DROPPED[ASVS]
    assert measure_structure(_extracted(ASVS).elements)["warnings"] == WARNINGS[ASVS]


@pytest.mark.parametrize("case", [CPG, NIST_171R3])
def test_unsplit_case_dropped_and_warnings(case: str) -> None:
    assert _extracted(case).metadata["dropped"] == DROPPED[case]
    assert measure_structure(_extracted(case).elements)["warnings"] == WARNINGS[case]


def test_asvs_joins_share_parent() -> None:
    oracle = _asvs_oracle()
    parents = [_asvs_parents(e, oracle) for e in _joined(ASVS)]
    # The contributor list holds no requirements; each requirement join holds one parent's requirements.
    assert parents == [{"V10.4"}, {"V10.5"}, set()]
    for table, table_parents in zip(_joined(ASVS), parents, strict=True):
        if table_parents:
            ids = [ASVS_SHORTCODE_PREFIX + row[0].strip() for row in table["cells"][table["header_rows"] :]]
            assert all(rid in oracle for rid in ids)


def test_asvs_no_parent_split_across_adjacent_tables() -> None:
    oracle = _asvs_oracle()
    tables = _tables(ASVS)
    printed = {ASVS_SHORTCODE_PREFIX + row[0].strip() for e in tables for row in e["cells"][e["header_rows"] :]}
    assert set(oracle) <= printed
    adjacent = [(a, b) for a, b in itertools.pairwise(tables) if b["page"] == max(a["row_pages"]) + 1]
    assert adjacent, "the excerpt must hold tables on consecutive pages for this spec to bind"
    for a, b in adjacent:
        assert not _asvs_parents(a, oracle) & _asvs_parents(b, oracle)
    for parent in set(oracle.values()):
        assert sum(parent in _asvs_parents(e, oracle) for e in tables) == 1


def test_cpg_removes_no_furniture_and_cisa_never_reaches_the_layout() -> None:
    doc = _doc(CPG)
    # CPG's "CISA" is text inside the logo picture on each page, which the body walk does not traverse.
    cisa = [t for t in doc.texts if " ".join(go_fields(t.text)) == "CISA"]
    assert sorted(t.prov[0].page_no for t in cisa) == [6, 7, 8]
    assert all(t.parent is not None and t.parent.cref.startswith("#/pictures/") for t in cisa)
    assert find_furniture(doc) == {}
    lines = [line.strip() for e in _extracted(CPG).elements for line in e["content"].splitlines()]
    assert "CISA" not in lines
    # OUTCOME heads a block in the top margin on 3 pages but is also mid-page, so every one is kept.
    outcome = [e for e in _extracted(CPG).elements if e["content"].strip() == "OUTCOME"]
    assert len(outcome) == sum(1 for item, _ in doc.iterate_items() if getattr(item, "text", None) == "OUTCOME")
    assert len({e["page"] for e in outcome}) >= 3


def test_171r3_has_no_furniture() -> None:
    assert find_furniture(_doc(NIST_171R3)) == {}
    assert [d for d in _extracted(NIST_171R3).metadata["dropped"] if d["type"] == "furniture"] == []


def test_structure_after_repair_asvs() -> None:
    elements = _extracted(ASVS).elements
    items, method = structure_layout(elements)
    assert method == "heading"
    table = _joined(ASVS)[0]
    section = next(item for item in items if item.title.startswith("V10.4 "))
    lines = table["content"].splitlines()
    assert section.text.count(lines[0]) == 1
    # Markdown line k (from 2) renders grid row k - 1; take the first body row of each part's page.
    first_body_row_of_page: dict[int, int] = {}
    for row, page in enumerate(table["row_pages"]):
        if row >= table["header_rows"]:
            first_body_row_of_page.setdefault(page, row)
    assert first_body_row_of_page == {1: 1, 2: 5, 3: 14}
    for row in first_body_row_of_page.values():
        assert lines[row + 1] in section.text
