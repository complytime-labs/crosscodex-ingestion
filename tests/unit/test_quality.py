"""Unit tests for structure measurement of extracted elements."""

from typing import Any

from crosscodex_ingestion.quality import measure_structure


def _el(kind: str, content: str = "text", level: int | None = None, parent_id: str | None = "h") -> dict[str, Any]:
    return {
        "element_id": "e",
        "type": kind,
        "content": content,
        "page": 1,
        "level": level,
        "parent_id": parent_id,
        "style": None,
    }


def test_empty_input() -> None:
    assert measure_structure([]) == {
        "heading_count": 0,
        "table_count": 0,
        "max_heading_depth": 0,
        "unparented_count": 0,
        "fragment_count": 0,
        "warnings": [],
    }


def test_no_headings_boundary() -> None:
    below = [_el("paragraph", parent_id=None) for _ in range(49)]
    at = [_el("paragraph", parent_id=None) for _ in range(50)]
    assert measure_structure(below)["warnings"] == []
    assert measure_structure(at)["warnings"] == ["no_headings"]


def test_no_headings_not_raised_when_heading_present() -> None:
    elements = [_el("heading", level=1)] + [_el("paragraph") for _ in range(60)]
    assert measure_structure(elements)["warnings"] == []


def test_fragment_length_boundary() -> None:
    elements = [
        _el("paragraph", " (12) "),
        _el("list_item", "(a)"),
        _el("paragraph", "(123)"),
        _el("heading", "(1)", level=1),
    ]
    assert measure_structure(elements)["fragment_count"] == 2


def test_max_heading_depth() -> None:
    elements = [_el("heading", level=lv) for lv in (1, 3, 2)]
    result = measure_structure(elements)
    assert result["max_heading_depth"] == 3
    assert result["heading_count"] == 3


def test_unparented_excludes_headings() -> None:
    elements = [
        _el("heading", level=1, parent_id=None),
        _el("paragraph", parent_id=None),
        _el("table", parent_id="e-1"),
    ]
    result = measure_structure(elements)
    assert result["unparented_count"] == 1
    assert result["table_count"] == 1
