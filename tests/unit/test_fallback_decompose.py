"""Tests for clause decomposition (Go DecomposeText) and for to_tree."""

from typing import Any

import pytest

from crosscodex_ingestion.fallback import Item, adapt_layout, decompose_text, to_tree
from crosscodex_ingestion.structured import TreeNode
from tests.unit.fallback_golden import load_case, nearest_section_parents

_GO_CLASS = {"compliance-requirement": "requirement", "compliance-section": "section"}


def _decompose_sections(case: str) -> list[dict[str, Any]]:
    """decompose_text over each heading section of a heading-tier case, as Go's post-processing calls it."""
    doc = adapt_layout(load_case(case)[0])
    return [
        {"id": item.id, "text": item.text, "cls": item.cls, "parent": item.parent_id, "props": item.props}
        for i, sec in enumerate(doc.sections)
        for item in decompose_text(f"sec-{i + 1}", sec.text)
    ]


def _golden_items(case: str) -> list[dict[str, Any]]:
    """The golden's items without Title, which Go's post-processing sets and DecomposeText does not.

    Parents (and the parent-id prop) are the nearest enclosing section, where the port departs from Go.
    """
    golden = load_case(case)[1]
    items = []
    for g, parent in zip(golden["items"], nearest_section_parents(golden), strict=True):
        props = dict(g["Props"] or {})
        if parent:
            props["parent-id"] = parent
        items.append({"id": g["ID"], "text": g["Text"], "cls": _GO_CLASS[g["Class"]], "parent": parent, "props": props})
    return items


@pytest.mark.parametrize(
    "case",
    [
        "decompose-39-words",
        "decompose-40-words",
        "decompose-41-words",
        "decompose-nested",
        "decompose-repeated-labels",
        "decompose-each-tier",
        "decompose-item-min-words",
    ],
)
def test_decompose_text_matches_the_go_golden(case: str) -> None:
    assert _decompose_sections(case) == _golden_items(case)


def test_repeated_labels_get_a_numbered_suffix() -> None:
    ids = [item["id"] for item in _decompose_sections("decompose-repeated-labels")]
    assert any(i.endswith("-2") for i in ids)
    assert len(ids) == len(set(ids))


def test_text_under_the_minimum_word_count_is_one_trimmed_item() -> None:
    text = "  (a) one two\n(b) three four  \n"
    assert decompose_text("x", text) == [Item("x", "", "(a) one two\n(b) three four", "requirement", "", {}, None)]


def test_a_minimum_of_zero_still_needs_two_clauses() -> None:
    assert decompose_text("x", "(a) only one", min_words=0) == [
        Item("x", "", "(a) only one", "requirement", "", {}, None)
    ]


def test_a_clause_names_its_nearest_enclosing_section_as_parent() -> None:
    # Go would give x.a.1.i and x.a.1.ii the parent x.a, leaving the section x.a.1 childless.
    text = (
        "(a) lead\n1. one one one one one one one one\ni. p p p p p p p p\nii. q q q q q q q q\n"
        "2. two two two two two two two two\n(b) tail"
    )
    items = decompose_text("x", text, min_words=0)
    assert [(i.id, i.cls, i.parent_id, i.props) for i in items] == [
        ("x.a", "section", "", {"label": "a"}),
        ("x.a.1", "section", "x.a", {"label": "1", "parent-id": "x.a"}),
        ("x.a.1.i", "requirement", "x.a.1", {"label": "i", "parent-id": "x.a.1"}),
        ("x.a.1.ii", "requirement", "x.a.1", {"label": "ii", "parent-id": "x.a.1"}),
        ("x.a.2", "requirement", "x.a", {"label": "2", "parent-id": "x.a"}),
        ("x.b", "requirement", "", {"label": "b"}),
    ]


def test_a_line_of_two_thousand_repeated_labels_decomposes_without_recursion_error() -> None:
    # Labels are matched one per line, so 2000 labels on one line are text, not 2000 levels: it splits once.
    # Nesting depth is capped at 5 by the tiers; this guards only against recursing per label.
    first = "(a) " * 2000 + "word " * 40
    items = decompose_text("x", first + "\n(b) end")
    assert [(i.id, i.text) for i in items] == [("x.a", first[4:].rstrip(" ")), ("x.b", "end")]


def _item(item_id: str, parent: str = "", cls: str = "requirement", **props: str) -> Item:
    return Item(item_id, "T", f"text of {item_id}", cls, parent, props, 4)


def test_to_tree_attaches_children_by_parent_and_drops_the_parent_id_prop() -> None:
    items = [
        _item("x.a", cls="section", label="a"),
        _item("x.a.1", "x.a", label="1", **{"parent-id": "x.a"}),
        _item("x.b", label="b"),
    ]
    child = TreeNode("items[1]", 1, "x.a.1", "T", "text of x.a.1", {"label": "1"}, [], "control", 4)
    assert to_tree(items) == [
        TreeNode("items[0]", 0, "x.a", "T", "text of x.a", {"label": "a"}, [child], "section", 4),
        TreeNode("items[2]", 2, "x.b", "T", "text of x.b", {"label": "b"}, [], "control", 4),
    ]


def test_to_tree_maps_an_empty_title_and_text_to_none() -> None:
    (node,) = to_tree([Item("sec-1", "", "", "requirement", "", {}, None)])
    assert (node.title, node.text, node.page) == (None, None, None)


def test_to_tree_attaches_a_child_to_the_latest_item_with_its_parent_id() -> None:
    # The pattern tier can repeat an ID (two "1." lines); a child belongs to the item it follows.
    items = [_item("1."), _item("1.", cls="section"), _item("1..a", "1.")]
    roots = to_tree(items)
    assert [(r.index, [c.index for c in r.children]) for r in roots] == [(0, []), (1, [2])]
