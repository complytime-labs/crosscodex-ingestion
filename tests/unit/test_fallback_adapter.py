"""Tests for the layout adapter and Go string helpers of the fallback structurer."""

import json
from pathlib import Path
from typing import Any

import pytest

from crosscodex_ingestion import fallback
from crosscodex_ingestion.fallback import LayoutDoc, Section, Table, adapt_layout, go_fields, go_trim_space

FIXTURES = Path(__file__).parent.parent / "fixtures" / "fallback"


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


GO_SPACES = [
    "\t",
    "\n",
    "\v",
    "\f",
    "\r",
    " ",
    "\u0085",
    "\u00a0",
    "\u1680",
    "\u2000",
    "\u200a",
    "\u2028",
    "\u2029",
    "\u202f",
    "\u205f",
    "\u3000",
]


@pytest.mark.parametrize("space", GO_SPACES)
def test_go_whitespace_is_trimmed_and_split(space: str) -> None:
    assert go_trim_space(f"{space}{space}a b{space}") == "a b"
    assert go_fields(f"a{space}b{space}{space}c") == ["a", "b", "c"]


@pytest.mark.parametrize("not_space", ["\u200b", "\u180e", "\ufeff", "\x00", "\x1c", "\x1f"])
def test_non_go_whitespace_is_kept(not_space: str) -> None:
    # str.strip()/split() treat U+001C and friends as whitespace; Go's unicode.IsSpace does not.
    assert go_trim_space(f"{not_space}a{not_space}") == f"{not_space}a{not_space}"
    assert go_fields(f"a{not_space}b") == [f"a{not_space}b"]


def test_go_trim_space_and_fields_on_blank_input() -> None:
    assert go_trim_space(" \u00a0\n") == ""
    assert go_trim_space("") == ""
    assert go_fields(" \u2028 ") == []
    assert go_fields("") == []


def test_heading_with_two_paragraphs_is_one_section() -> None:
    doc = adapt_layout(
        [
            element("e1", "heading", "Scope", level=2, page=3),
            element("e2", "paragraph", "First.", parent="e1", page=3),
            element("e3", "list_item", "(a) Second.", parent="e1", page=4),
        ]
    )
    assert doc.sections == [Section(level=2, title="Scope", text="First.\n\n(a) Second.", page=3)]
    assert doc.tables == []
    assert doc.raw_text == "Scope\n\nFirst.\n\n(a) Second."


def test_elements_without_parent_join_raw_text_only() -> None:
    doc = adapt_layout(
        [
            element("e1", "paragraph", "Preamble."),
            element("e2", "heading", "H", level=1),
            element("e3", "paragraph", "Body.", parent="e2"),
        ]
    )
    assert doc.sections == [Section(level=1, title="H", text="Body.", page=None)]
    assert doc.raw_text == "Preamble.\n\nH\n\nBody."


def test_section_without_children_has_empty_text() -> None:
    assert adapt_layout([element("e1", "heading", "H", level=1)]).sections[0].text == ""


def test_gfm_table_gives_headers_and_rows() -> None:
    content = "| ID | Title |\n| --- | :---: |\n| 1 | a \\| b |\n| 2 | c |\n"
    doc = adapt_layout(
        [
            element("e1", "heading", "H", level=1),
            element("e2", "table", content, parent="e1", page=7),
        ]
    )
    assert doc.tables == [Table(headers=["ID", "Title"], rows=[["1", "a | b"], ["2", "c"]], page=7)]
    assert doc.sections[0].text == content


def test_table_block_ends_at_first_non_row_line() -> None:
    content = "intro\n| a | b |\n|---|---|\n| 1 | 2 |\nnot a row\n| 3 | 4 |"
    table = adapt_layout([element("e1", "table", content)]).tables[0]
    assert table.rows == [["1", "2"]]


def test_header_only_table_has_no_rows() -> None:
    table = adapt_layout([element("e1", "table", "| a |\n| - |")]).tables[0]
    assert table.headers == ["a"]
    assert table.rows == []


@pytest.mark.parametrize(
    "content",
    [
        "| a | b |\n|---|\n",  # delimiter cell count differs
        "| a | b |\n|---|---|\n| 1 |\n",  # row cell count differs
        "| a | b |\n| x | y |\n",  # delimiter is not a delimiter row
        "| a | b |\n|--x|---|\n",  # bad delimiter cell
        "no table here",
        "| only header |",
    ],
)
def test_malformed_table_is_an_error(content: str) -> None:
    with pytest.raises(fallback.LayoutError, match="elements\\[0\\]"):
        adapt_layout([element("e1", "table", content)])


def test_unknown_parent_is_an_error() -> None:
    with pytest.raises(fallback.LayoutError, match="parent_id"):
        adapt_layout([element("e1", "paragraph", "x", parent="missing")])


def test_heading_without_level_is_an_error() -> None:
    with pytest.raises(fallback.LayoutError, match="level"):
        adapt_layout([element("e1", "heading", "x")])


def test_line_pages_follow_source_elements() -> None:
    doc = adapt_layout(
        [
            element("e1", "heading", "H", level=1, page=1),
            element("e2", "paragraph", "a\nb", parent="e1", page=2),
            element("e3", "paragraph", "c", parent="e1", page=None),
        ]
    )
    lines = doc.raw_text.split("\n")
    assert len(doc.line_pages) == len(lines)
    # A separator blank line belongs to the element before it.
    assert list(zip(lines, doc.line_pages, strict=True)) == [
        ("H", 1),
        ("", 1),
        ("a", 2),
        ("b", 2),
        ("", 2),
        ("c", None),
    ]


@pytest.mark.parametrize(
    ("case", "sections", "tables"),
    [("nist-sp800-171r3-excerpt", 13, 1), ("eurlex-nis2-32022L2555", 5, 2)],
)
def test_real_layouts(case: str, sections: int, tables: int) -> None:
    elements = json.loads((FIXTURES / case / "input.json").read_text(encoding="utf-8"))["elements"]
    doc = adapt_layout(elements)
    assert isinstance(doc, LayoutDoc)
    assert (len(doc.sections), len(doc.tables)) == (sections, tables)
    assert len(doc.line_pages) == len(doc.raw_text.split("\n"))


def test_every_regex_is_ascii_and_registered() -> None:
    import re

    assert fallback.REGEXES
    assert all(p.flags & re.ASCII for p in fallback.REGEXES)
