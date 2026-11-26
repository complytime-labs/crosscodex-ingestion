"""Parity of the fallback structurer (cascade plus decomposition) with the Go goldens, item for item."""

from typing import Any

import pytest

from crosscodex_ingestion.fallback import Item, NoStructure, structure_layout, to_tree
from crosscodex_ingestion.output_schema import validate_output
from crosscodex_ingestion.structured import emit_elements
from tests.unit.fallback_golden import FIXTURES, expected_nodes, load_case
from tests.unit.output_samples import conforming_output


def _cases(*, structured: bool) -> list[str]:
    return [
        case.parent.name
        for case in sorted(FIXTURES.glob("*/golden.json"))
        if (load_case(case.parent.name)[1]["tier"] != "none") == structured
    ]


def _as_nodes(items: list[Item]) -> list[dict[str, Any]]:
    """The fields expected_nodes gives a golden item, read from the port's items."""
    return [
        {
            "id": item.id,
            "title": item.title or None,
            "text": item.text or None,
            "parent": item.parent_id or None,
            "role": "section" if item.cls == "section" else "control",
            "properties": {k: v for k, v in item.props.items() if k != "parent-id"},
        }
        for item in items
    ]


def _diff(got: list[dict[str, Any]], want: list[dict[str, Any]]) -> str:
    lines = [f"{len(got)} items, golden has {len(want)}"]
    for i in range(max(len(got), len(want))):
        g = got[i] if i < len(got) else None
        w = want[i] if i < len(want) else None
        if g != w:
            lines.append(f"item {i}:\n  port:   {g!r}\n  golden: {w!r}")
    return "\n".join(lines)


@pytest.mark.parametrize("case", _cases(structured=True))
def test_structure_layout_matches_the_go_golden(case: str) -> None:
    elements, golden = load_case(case)
    items, method = structure_layout(elements)
    got, want = _as_nodes(items), expected_nodes(golden)
    assert got == want, _diff(got, want)
    assert method == golden["tier"]


@pytest.mark.parametrize(("case", "count"), [("nist-sp800-171r3-excerpt", 13), ("eurlex-nis2-32022L2555", 36)])
def test_real_documents_give_the_frozen_crosscodex_counts(case: str, count: int) -> None:
    items, method = structure_layout(load_case(case)[0])
    assert (len(items), method) == (count, "heading")


@pytest.mark.parametrize("case", _cases(structured=False))
def test_a_document_go_cannot_structure_raises_no_structure(case: str) -> None:
    with pytest.raises(NoStructure):
        structure_layout(load_case(case)[0])


def test_decomposed_items_take_the_page_of_their_tier_item() -> None:
    elements, _ = load_case("decompose-nested")
    for e in elements:
        e["page"] = 7
    items, _ = structure_layout(elements)
    assert len(items) > 1
    assert {item.page for item in items} == {7}


def test_a_three_level_clause_tree_nests_each_clause_under_its_nearest_section_and_validates() -> None:
    # Go parents i. and ii. to (a), leaving section 1. childless, which the 1.4 schema rejects.
    words = " ".join(f"w{n}" for n in range(40))
    text = f"(a) lead\n1. {words}\ni. {words[:30]}\nii. {words[:30]}\n2. {words[:30]}\n(b) tail"
    elements = [
        {"element_id": "h", "type": "heading", "content": "Scope", "page": 1, "level": 1, "parent_id": None},
        {"element_id": "p", "type": "paragraph", "content": text, "page": 1, "level": None, "parent_id": "h"},
    ]
    items, method = structure_layout(elements)
    roots = to_tree(items)
    output = conforming_output()
    output["elements"] = emit_elements(roots)
    output["metadata"].update(element_count=len(output["elements"]), structure_method=method)
    validate_output(output)
    middle = roots[0].children[0]
    assert (middle.id, [c.id for c in middle.children]) == ("sec-1.a.1", ["sec-1.a.1.i", "sec-1.a.1.ii"])
    assert [(i.id, i.cls, i.parent_id) for i in items] == [
        ("sec-1.a", "section", ""),
        ("sec-1.a.1", "section", "sec-1.a"),
        ("sec-1.a.1.i", "requirement", "sec-1.a.1"),
        ("sec-1.a.1.ii", "requirement", "sec-1.a.1"),
        ("sec-1.a.2", "requirement", "sec-1.a"),
        ("sec-1.b", "requirement", ""),
    ]
