"""Tests for the heading, table and pattern tiers of the fallback structurer, against the Go goldens.

Go's decomposition pass can split items and rewrite their ids, so only cases it left untouched are
compared here, and only on id, title, order, count and tier; full parity belongs to the decomposition tests.
"""

from typing import Any

import pytest

from crosscodex_ingestion.fallback import Item, adapt_layout, tier_heading, tier_pattern, tier_table
from tests.unit.fallback_golden import FIXTURES, load_case

TIERS = {"heading": tier_heading, "table": tier_table, "pattern": tier_pattern}
# Go's cascade order after the (unported) custom-regex tier.
CASCADE = ("heading", "table", "pattern")


def _was_decomposed(golden: dict[str, Any]) -> bool:
    """Decomposition gives items a label prop, a parent or the section class; undecomposed items have none."""
    return any(i["Props"] or i["ParentID"] or i["Class"] != "compliance-requirement" for i in golden["items"])


def _cases() -> list[str]:
    names = []
    for case in sorted(FIXTURES.glob("*/golden.json")):
        golden = load_case(case.parent.name)[1]
        if golden["tier"] in TIERS and not _was_decomposed(golden):
            names.append(case.parent.name)
    return names


def element(
    element_id: str,
    kind: str,
    content: str,
    *,
    level: int | None = None,
    parent: str | None = None,
    page: int | None = None,
) -> dict[str, Any]:
    return {
        "element_id": element_id,
        "type": kind,
        "content": content,
        "page": page,
        "level": level,
        "parent_id": parent,
        "style": {},
    }


def run_cascade(elements: list[dict[str, Any]]) -> tuple[str, list[Item]]:
    doc = adapt_layout(elements)
    for name in CASCADE:
        items = TIERS[name](doc)
        if items is not None:
            return name, items
    return "none", []


@pytest.mark.parametrize("case", _cases())
def test_matches_go_golden(case: str) -> None:
    elements, golden = load_case(case)
    tier, items = run_cascade(elements)
    assert tier == golden["tier"]
    assert [(i.id, i.title) for i in items] == [(g["ID"], g["Title"]) for g in golden["items"]]


def test_heading_all_furniture_does_not_win() -> None:
    elements, _ = load_case("heading-all-furniture")
    assert tier_heading(adapt_layout(elements)) is None


def test_pattern_two_lines_does_not_win() -> None:
    elements, _ = load_case("pattern-two-lines")
    assert tier_pattern(adapt_layout(elements)) is None


def test_heading_id_counts_filtered_headings() -> None:
    elements = [element(f"h{i}", "heading", t, level=1) for i, t in enumerate(["A", "Dup", "Dup", "Dup", "Dup", "B"])]
    items = tier_heading(adapt_layout(elements))
    assert items is not None
    assert [i.id for i in items] == ["sec-1", "sec-6"]


def test_heading_item_takes_the_heading_page() -> None:
    elements = [
        element("h1", "heading", "First", level=1, page=2),
        element("p1", "paragraph", "body", parent="h1", page=3),
    ]
    items = tier_heading(adapt_layout(elements))
    assert items is not None
    assert (items[0].page, items[0].text, items[0].cls) == (2, "body", "requirement")


def test_table_item_takes_the_table_page_and_byte_length_id_rule() -> None:
    long_id = "é" * 25  # 25 characters, 50 bytes: too long for Go's len() < 50
    table = f"| a | b |\n|---|---|\n| R-1 | x |\n| {long_id} | y |\n"
    items = tier_table(adapt_layout([element("t", "table", table, page=4)]))
    assert items is not None
    assert [(i.id, i.page) for i in items] == [("R-1", 4), ("tbl-2", 4)]


def test_pattern_item_takes_the_page_of_its_line() -> None:
    elements = [
        element("p1", "paragraph", "1.1 one\n1.2 two", page=1),
        element("p2", "paragraph", "intro\n1.3 three", page=2),
    ]
    items = tier_pattern(adapt_layout(elements))
    assert items is not None
    assert [(i.id, i.page) for i in items] == [("1.1", 1), ("1.2", 1), ("1.3", 2)]


def test_pattern_does_not_treat_vertical_tab_as_regex_space() -> None:
    elements = [element("p", "paragraph", "\n".join(f"Article\v{n} x" for n in range(5)))]
    assert tier_pattern(adapt_layout(elements)) is None
