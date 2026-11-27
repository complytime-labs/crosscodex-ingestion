"""Unit tests for layout_repair: the cell model of Docling table items, furniture and table rejoin."""

import traceback

import pytest
from docling_core.types.doc import (
    BoundingBox,
    CoordOrigin,
    DocItemLabel,
    DoclingDocument,
    ProvenanceItem,
    RefItem,
    RichTableCell,
    Size,
    TableCell,
    TableData,
    TableItem,
)

from crosscodex_ingestion import layout_repair
from crosscodex_ingestion.layout_repair import (
    FURNITURE_MAX_CHARS,
    Chain,
    TableCells,
    find_furniture,
    plan_rejoins,
    render_chain,
    table_cells,
)


def _cell(text: str, row: int, col: int, *, col_span: int = 1, header: bool = False) -> TableCell:
    return TableCell(
        text=text,
        start_row_offset_idx=row,
        end_row_offset_idx=row + 1,
        start_col_offset_idx=col,
        end_col_offset_idx=col + col_span,
        col_span=col_span,
        column_header=header,
    )


def _doc_with_table(cells: list[TableCell], rows: int, cols: int) -> tuple[DoclingDocument, object]:
    doc = DoclingDocument(name="t")
    doc.add_page(page_no=1, size=Size(width=600, height=800))
    item = doc.add_table(
        data=TableData(table_cells=cells, num_rows=rows, num_cols=cols),
        prov=ProvenanceItem(page_no=1, bbox=BoundingBox(l=0, t=0, r=1, b=1), charspan=(0, 0)),
    )
    return doc, item


def test_cells_repeat_spanned_text():
    doc, item = _doc_with_table([_cell("Wide", 0, 0, col_span=2), _cell("a", 1, 0), _cell("b", 1, 1)], 2, 2)

    assert table_cells(item, doc) == TableCells(cells=[["Wide", "Wide"], ["a", "b"]], header_rows=0)


def test_header_rows_counts_leading_flagged_rows():
    cells = [
        _cell("A", 0, 0, header=True),
        _cell("B", 0, 1, header=True),
        _cell("C", 1, 0, header=True),
        _cell("D", 1, 1, header=True),
        _cell("x", 2, 0),
        _cell("y", 2, 1),
    ]
    doc, item = _doc_with_table(cells, 3, 2)

    assert table_cells(item, doc).header_rows == 2


def test_header_rows_zero_when_row0_unflagged():
    doc, item = _doc_with_table([_cell("A", 0, 0), _cell("B", 1, 0, header=True)], 2, 1)

    assert table_cells(item, doc).header_rows == 0


def test_header_row_with_empty_unflagged_cell_still_counts():
    cells = [_cell("A", 0, 0, header=True), _cell("  ", 0, 1), _cell("x", 1, 0), _cell("y", 1, 1)]
    doc, item = _doc_with_table(cells, 2, 2)

    assert table_cells(item, doc).header_rows == 1


def test_header_row_with_nonempty_unflagged_cell_does_not_count():
    cells = [_cell("A", 0, 0, header=True), _cell("B", 0, 1)]
    doc, item = _doc_with_table(cells, 1, 2)

    assert table_cells(item, doc).header_rows == 0


def test_header_row_with_only_empty_cells_does_not_count():
    doc, item = _doc_with_table([_cell("", 0, 0), _cell("x", 1, 0, header=True)], 2, 1)

    assert table_cells(item, doc).header_rows == 0


def _rich(ref: str, text: str) -> RichTableCell:
    return RichTableCell(
        text=text,
        ref=RefItem(cref=ref),
        start_row_offset_idx=0,
        end_row_offset_idx=1,
        start_col_offset_idx=0,
        end_col_offset_idx=1,
    )


def test_rich_cell_resolves_ref():
    doc = DoclingDocument(name="t")
    doc.add_text(label=DocItemLabel.TEXT, text="from the referenced item")

    item = doc.add_table(data=TableData(table_cells=[_rich("#/texts/0", "own")], num_rows=1, num_cols=1))

    assert table_cells(item, doc).cells == [["from the referenced item"]]


def test_rich_cell_unresolvable_ref_uses_own_text():
    doc = DoclingDocument(name="t")

    item = doc.add_table(data=TableData(table_cells=[_rich("#/texts/99", "own")], num_rows=1, num_cols=1))

    assert table_cells(item, doc).cells == [["own"]]


# Furniture: pages are 1000 high, so the margins are y <= 100 (top) and y >= 900 (bottom).
TOP = (10.0, 50.0)
BOTTOM = (950.0, 990.0)
MIDDLE = (400.0, 440.0)


def _furniture_doc(
    occurrences: list[tuple[int, str, tuple[float, float] | None]],
    *,
    label: DocItemLabel = DocItemLabel.TEXT,
    page_sizes: bool = True,
) -> tuple[DoclingDocument, list]:
    """A document with one *label* item per (page, text, (top, bottom) in top-left coordinates)."""
    doc = DoclingDocument(name="f")
    if page_sizes:
        for page_no in sorted({page for page, _, _ in occurrences}):
            doc.add_page(page_no=page_no, size=Size(width=600, height=1000))
    items = []
    for page_no, text, span in occurrences:
        prov = None
        if span is not None:
            bbox = BoundingBox(l=0, r=100, t=span[0], b=span[1], coord_origin=CoordOrigin.TOPLEFT)
            prov = ProvenanceItem(page_no=page_no, bbox=bbox, charspan=(0, len(text)))
        items.append(doc.add_text(label=label, text=text, prov=prov))
    return doc, items


def test_furniture_removed_on_three_pages():
    doc, items = _furniture_doc([(1, "Draft", TOP), (2, "Draft", BOTTOM), (3, "Draft", TOP)])

    assert find_furniture(doc) == {item.self_ref: 3 for item in items}


def test_furniture_kept_on_two_pages():
    doc, _ = _furniture_doc([(1, "Draft", TOP), (2, "Draft", TOP)])

    assert find_furniture(doc) == {}


def test_furniture_three_times_one_page_kept():
    doc, _ = _furniture_doc([(1, "Draft", TOP), (1, "Draft", TOP), (1, "Draft", TOP)])

    assert find_furniture(doc) == {}


def test_furniture_kept_when_one_occurrence_mid_page():
    doc, _ = _furniture_doc([(1, "Draft", TOP), (2, "Draft", TOP), (3, "Draft", TOP), (4, "Draft", MIDDLE)])

    assert find_furniture(doc) == {}


def test_furniture_counts_pages_of_all_occurrences_and_removes_every_margin_one():
    doc, items = _furniture_doc([(1, "Draft", TOP), (2, "Draft", TOP), (2, "Draft", BOTTOM), (3, "Draft", TOP)])

    assert find_furniture(doc) == {item.self_ref: 3 for item in items}


@pytest.mark.parametrize(
    ("span", "removed"),
    [
        pytest.param((50.0, 100.0), True, id="bottom-edge-on-top-margin"),
        pytest.param((50.0, 100.5), False, id="bottom-edge-just-below-top-margin"),
        pytest.param((900.0, 950.0), True, id="top-edge-on-bottom-margin"),
        pytest.param((899.5, 950.0), False, id="top-edge-just-above-bottom-margin"),
    ],
)
def test_furniture_bbox_margin_is_inclusive(span, removed):
    doc, items = _furniture_doc([(1, "Draft", span), (2, "Draft", span), (3, "Draft", span)])

    assert find_furniture(doc) == ({item.self_ref: 3 for item in items} if removed else {})


def test_furniture_bbox_just_inside_body_kept():
    doc, _ = _furniture_doc([(page, "Draft", (60.0, 101.0)) for page in (1, 2, 3)])

    assert find_furniture(doc) == {}


def test_furniture_kept_when_page_height_is_not_positive():
    doc = DoclingDocument(name="f")
    for page_no in (1, 2, 3):
        doc.add_page(page_no=page_no, size=Size(width=600, height=0))
        bbox = BoundingBox(l=0, r=100, t=400, b=440, coord_origin=CoordOrigin.TOPLEFT)
        prov = ProvenanceItem(page_no=page_no, bbox=bbox, charspan=(0, 5))
        doc.add_text(label=DocItemLabel.TEXT, text="Draft", prov=prov)

    assert find_furniture(doc) == {}


def test_furniture_bottom_left_origin_is_converted():
    doc = DoclingDocument(name="f")
    items = []
    for page_no in (1, 2, 3):
        doc.add_page(page_no=page_no, size=Size(width=600, height=1000))
        bbox = BoundingBox(l=0, r=100, t=990, b=950, coord_origin=CoordOrigin.BOTTOMLEFT)  # bottom of page
        prov = ProvenanceItem(page_no=page_no, bbox=bbox, charspan=(0, 5))
        items.append(doc.add_text(label=DocItemLabel.TEXT, text="Draft", prov=prov))

    assert find_furniture(doc) == {item.self_ref: 3 for item in items}


def test_furniture_multi_prov_item_needs_every_bbox_in_margin():
    doc, items = _furniture_doc([(1, "Draft", TOP), (2, "Draft", TOP), (3, "Draft", TOP)])
    straddling = ProvenanceItem(
        page_no=3,
        bbox=BoundingBox(l=0, r=1, t=400, b=440, coord_origin=CoordOrigin.TOPLEFT),
        charspan=(0, 5),
    )
    items[2].prov.append(straddling)

    assert find_furniture(doc) == {}


def test_furniture_without_bbox_or_page_size_kept():
    no_bbox, _ = _furniture_doc([(1, "Draft", TOP), (2, "Draft", TOP), (3, "Draft", None)])
    no_size, _ = _furniture_doc([(1, "Draft", TOP), (2, "Draft", TOP), (3, "Draft", TOP)], page_sizes=False)

    assert find_furniture(no_bbox) == {}
    assert find_furniture(no_size) == {}


@pytest.mark.parametrize(
    ("length", "removed"),
    [(FURNITURE_MAX_CHARS, True), (FURNITURE_MAX_CHARS + 1, False)],
    ids=["100-chars-removed", "101-chars-kept"],
)
def test_furniture_key_100_chars_removed_101_kept(length, removed):
    text = "x" * length
    doc, items = _furniture_doc([(page, text, TOP) for page in (1, 2, 3)])

    assert find_furniture(doc) == ({item.self_ref: 3 for item in items} if removed else {})


def test_furniture_key_length_is_measured_after_whitespace_collapse():
    padded = "  " + "x" * 50 + "\n\t" + "y" * 49 + "  "  # 101 characters collapse to 100
    doc, items = _furniture_doc([(page, padded, TOP) for page in (1, 2, 3)])

    assert find_furniture(doc) == {item.self_ref: 3 for item in items}


def test_furniture_keys_match_after_collapse_but_stay_case_and_digit_exact():
    collapsed, items = _furniture_doc([(1, "Draft  v1", TOP), (2, "Draft v1", TOP), (3, "Draft\tv1", TOP)])
    other, _ = _furniture_doc([(1, "Page 1", TOP), (2, "Page 2", TOP), (3, "Page 3", TOP)])
    cased, _ = _furniture_doc([(1, "Draft", TOP), (2, "DRAFT", TOP), (3, "draft", TOP)])

    assert find_furniture(collapsed) == {item.self_ref: 3 for item in items}
    assert find_furniture(other) == {}
    assert find_furniture(cased) == {}


@pytest.mark.parametrize("label", [DocItemLabel.SECTION_HEADER, DocItemLabel.LIST_ITEM])
def test_furniture_ignores_headings_and_list_items(label):
    doc, _ = _furniture_doc([(page, "Draft", TOP) for page in (1, 2, 3)], label=label)

    assert find_furniture(doc) == {}


def test_furniture_ignores_page_headers_in_furniture_layer():
    doc, _ = _furniture_doc([(page, "Draft", TOP) for page in (1, 2, 3)], label=DocItemLabel.PAGE_HEADER)

    assert find_furniture(doc) == {}


# Rejoin: tables continued across a page break.
HEADER = ["ID", "Text"]


def _pdf(pages: int = 4) -> DoclingDocument:
    doc = DoclingDocument(name="r")
    for page_no in range(1, pages + 1):
        doc.add_page(page_no=page_no, size=Size(width=600, height=1000))
    return doc


def _prov(page_no: int) -> ProvenanceItem:
    bbox = BoundingBox(l=0, r=100, t=400, b=440, coord_origin=CoordOrigin.TOPLEFT)
    return ProvenanceItem(page_no=page_no, bbox=bbox, charspan=(0, 0))


def _table(doc: DoclingDocument, pages: int | list[int], rows: list[list[str]], header_rows: int = 0) -> TableItem:
    """Add a table on *pages* (one prov per page) whose first *header_rows* rows are flagged column headers."""
    page_list = [pages] if isinstance(pages, int) else pages
    cells = [_cell(text, r, c, header=r < header_rows) for r, row in enumerate(rows) for c, text in enumerate(row)]
    data = TableData(table_cells=cells, num_rows=len(rows), num_cols=len(rows[0]) if rows else 0)
    item = doc.add_table(data=data, prov=_prov(page_list[0]))
    item.prov.extend(_prov(page_no) for page_no in page_list[1:])
    return item


def test_join_repeated_header():
    doc = _pdf()
    first = _table(doc, 1, [HEADER, ["AC-1", "a"]], header_rows=1)
    second = _table(doc, 2, [HEADER, ["AC-2", "b"]], header_rows=1)

    chains = plan_rejoins(doc, {}, "pdf")

    assert chains == {
        first.self_ref: Chain(
            parts=[first, second],
            cells=[HEADER, ["AC-1", "a"], ["AC-2", "b"]],
            header_rows=1,
            row_pages=[1, 1, 2],
            removed_header_pages=[(2, 1)],
        )
    }


@pytest.mark.parametrize("label", [DocItemLabel.TEXT, DocItemLabel.PARAGRAPH, DocItemLabel.LIST_ITEM])
def test_join_whitespace_paragraph_between(label):
    doc = _pdf()
    first = _table(doc, 1, [HEADER, ["AC-1", "a"]], header_rows=1)
    doc.add_text(label=label, text=" \t\n\u00a0 ")
    _table(doc, 2, [HEADER, ["AC-2", "b"]], header_rows=1)

    assert plan_rejoins(doc, {}, "pdf")[first.self_ref].cells == [HEADER, ["AC-1", "a"], ["AC-2", "b"]]


def test_join_header_repeated_without_flag():
    doc = _pdf()
    first = _table(doc, 1, [HEADER, ["AC-1", "a"]], header_rows=1)
    _table(doc, 2, [["ID ", " Text"], ["AC-2", "b"]])

    chain = plan_rejoins(doc, {}, "pdf")[first.self_ref]

    assert chain.cells == [HEADER, ["AC-1", "a"], ["AC-2", "b"]]
    assert chain.removed_header_pages == [(2, 1)]


def test_join_both_parts_headerless():
    doc = _pdf()
    first = _table(doc, 1, [["AC-1", "a"]])
    _table(doc, 2, [["AC-2", "b"]])

    chain = plan_rejoins(doc, {}, "pdf")[first.self_ref]

    assert (chain.cells, chain.header_rows, chain.row_pages) == ([["AC-1", "a"], ["AC-2", "b"]], 0, [1, 2])
    assert chain.removed_header_pages == []


def test_join_with_removed_furniture_between():
    doc = _pdf()
    first = _table(doc, 1, [HEADER, ["AC-1", "a"]], header_rows=1)
    running = doc.add_text(label=DocItemLabel.TEXT, text="Draft", prov=_prov(2))
    _table(doc, 2, [HEADER, ["AC-2", "b"]], header_rows=1)

    assert first.self_ref in plan_rejoins(doc, {running.self_ref: 3}, "pdf")


def test_no_join_page_gap_two():
    doc = _pdf()
    _table(doc, 1, [HEADER, ["AC-1", "a"]], header_rows=1)
    _table(doc, 3, [HEADER, ["AC-2", "b"]], header_rows=1)

    assert plan_rejoins(doc, {}, "pdf") == {}


@pytest.mark.parametrize(
    ("first_pages", "second_pages"),
    [([1, 2], 2), ([1, 2], 3), (1, [2, 3])],
    ids=["tail-two-pages-next-on-its-last", "tail-two-pages-next-after", "continuation-two-pages"],
)
def test_no_join_part_with_two_prov_pages(first_pages, second_pages):
    doc = _pdf()
    _table(doc, first_pages, [HEADER, ["AC-1", "a"]], header_rows=1)
    _table(doc, second_pages, [HEADER, ["AC-2", "b"]], header_rows=1)

    assert plan_rejoins(doc, {}, "pdf") == {}


def test_no_join_header_differs_one_cell():
    doc = _pdf()
    _table(doc, 1, [HEADER, ["AC-1", "a"]], header_rows=1)
    _table(doc, 2, [["ID", "text"], ["AC-2", "b"]], header_rows=1)

    assert plan_rejoins(doc, {}, "pdf") == {}


def test_no_join_width_differs():
    doc = _pdf()
    _table(doc, 1, [HEADER, ["AC-1", "a"]], header_rows=1)
    _table(doc, 2, [[*HEADER, "Notes"], ["AC-2", "b", "c"]], header_rows=1)

    assert plan_rejoins(doc, {}, "pdf") == {}


def _blocked_by(add_between) -> dict:
    doc = _pdf()
    _table(doc, 1, [HEADER, ["AC-1", "a"]], header_rows=1)
    add_between(doc)
    _table(doc, 2, [HEADER, ["AC-2", "b"]], header_rows=1)
    return plan_rejoins(doc, {}, "pdf")


def test_no_join_caption_between():
    """A standalone caption, a sibling of the tables rather than a child of one, blocks the join."""
    assert _blocked_by(lambda doc: doc.add_text(label=DocItemLabel.CAPTION, text="Table 1 (continued)")) == {}


def test_no_join_heading_between():
    assert _blocked_by(lambda doc: doc.add_heading(text="Next section")) == {}


def test_no_join_blank_heading_between():
    assert _blocked_by(lambda doc: doc.add_heading(text=" ")) == {}


def test_no_join_kept_repeated_text_between():
    assert _blocked_by(lambda doc: doc.add_text(label=DocItemLabel.TEXT, text="Draft", prov=_prov(2))) == {}


def test_no_join_other_table_between():
    doc = _pdf()
    _table(doc, 1, [HEADER, ["AC-1", "a"]], header_rows=1)
    _table(doc, 2, [["Other", "table", "here"]])
    _table(doc, 2, [HEADER, ["AC-2", "b"]], header_rows=1)

    assert plan_rejoins(doc, {}, "pdf") == {}


@pytest.mark.parametrize("format_hint", ["docx", "html", "txt"])
def test_no_join_outside_pdf(format_hint):
    doc = _pdf()
    _table(doc, 1, [HEADER, ["AC-1", "a"]], header_rows=1)
    _table(doc, 2, [HEADER, ["AC-2", "b"]], header_rows=1)

    assert plan_rejoins(doc, {}, format_hint) == {}


def test_failed_part_breaks_chain(monkeypatch):
    doc = _pdf()
    _table(doc, 1, [HEADER, ["AC-1", "a"]], header_rows=1)
    failing = _table(doc, 2, [HEADER, ["AC-2", "b"]], header_rows=1)
    _table(doc, 2, [HEADER, ["AC-3", "c"]], header_rows=1)
    _table(doc, 3, [HEADER, ["AC-4", "d"]], header_rows=1)
    real = layout_repair.table_cells

    def table_cells_failing_on_one(item, document):
        if item.self_ref == failing.self_ref:
            raise ValueError("unreadable")
        return real(item, document)

    monkeypatch.setattr(layout_repair, "table_cells", table_cells_failing_on_one)

    chains = plan_rejoins(doc, {}, "pdf")

    assert [[part.self_ref for part in chain.parts] for chain in chains.values()] == [["#/tables/2", "#/tables/3"]]


def test_table_without_rows_breaks_chain():
    """The walk drops a table that exports nothing, so it is a different item between its neighbours."""
    doc = _pdf()
    _table(doc, 1, [["AC-1", "a"]])
    empty = doc.add_table(data=TableData(num_rows=0, num_cols=2), prov=_prov(2))
    _table(doc, 3, [["AC-2", "b"]])

    assert plan_rejoins(doc, {}, "pdf") == {}
    assert empty.export_to_markdown(doc=doc) == ""


def test_render_chain_is_docling_render_of_merged_data():
    doc = _pdf()
    first = _table(doc, 1, [HEADER, ["AC-1", "a"]], header_rows=1)
    _table(doc, 2, [HEADER, ["AC-2", "b"]], header_rows=1)
    _table(doc, 3, [["AC-3", "c"]])
    reference_doc = _pdf()
    reference = _table(reference_doc, 1, [HEADER, ["AC-1", "a"], ["AC-2", "b"], ["AC-3", "c"]], header_rows=1)

    rendered = render_chain(plan_rejoins(doc, {}, "pdf")[first.self_ref], doc)

    assert rendered == reference.export_to_markdown(doc=reference_doc).strip()


def test_render_chain_keeps_body_part_of_a_row_spanning_header_cell():
    doc = _pdf()
    first = _table(doc, 1, [HEADER, ["AC-1", "a"]], header_rows=1)
    spanning = TableCell(
        text="ID",
        start_row_offset_idx=0,
        end_row_offset_idx=2,
        start_col_offset_idx=0,
        end_col_offset_idx=1,
        row_span=2,
        column_header=True,
    )
    cells = [spanning, _cell("Text", 0, 1, header=True), _cell("b", 1, 1)]
    doc.add_table(data=TableData(table_cells=cells, num_rows=2, num_cols=2), prov=_prov(2))
    reference_doc = _pdf()
    reference = _table(reference_doc, 1, [HEADER, ["AC-1", "a"], ["ID", "b"]], header_rows=1)

    chain = plan_rejoins(doc, {}, "pdf")[first.self_ref]

    assert chain.cells == [HEADER, ["AC-1", "a"], ["ID", "b"]]
    assert render_chain(chain, doc) == reference.export_to_markdown(doc=reference_doc).strip()


def test_render_failure_raises_an_internal_error_without_document_text(monkeypatch):
    doc = _pdf()
    first = _table(doc, 1, [HEADER, ["AC-1", "a"]], header_rows=1)
    _table(doc, 2, [HEADER, ["AC-2", "b"]], header_rows=1)
    chain = plan_rejoins(doc, {}, "pdf")[first.self_ref]

    def failing_copy(self, **kwargs):
        raise ValueError("AC-1 secret cell text")

    monkeypatch.setattr(TableItem, "model_copy", failing_copy)

    with pytest.raises(RuntimeError) as raised:
        render_chain(chain, doc)

    assert str(raised.value) == "a table continued from page 1 to page 2 could not be rendered (ValueError)"
    assert "secret" not in "".join(traceback.format_exception(raised.value))


def test_join_keeps_parts_own_caption_and_footnote():
    """Docling's reading-order model makes a table's caption and footnotes its children, yielded right after it."""
    doc = _pdf()
    first = _table(doc, 1, [HEADER, ["AC-1", "a"]], header_rows=1)
    caption = doc.add_text(label=DocItemLabel.CAPTION, text="Table 1. Controls", parent=first, prov=_prov(1))
    first.captions.append(caption.get_ref())
    middle = _table(doc, 2, [HEADER, ["AC-2", "b"]], header_rows=1)
    footnote = doc.add_text(label=DocItemLabel.FOOTNOTE, text="* Withdrawn.", parent=middle, prov=_prov(2))
    middle.footnotes.append(footnote.get_ref())
    _table(doc, 3, [HEADER, ["AC-3", "c"]], header_rows=1)

    chain = plan_rejoins(doc, {}, "pdf")[first.self_ref]

    assert chain.cells == [HEADER, ["AC-1", "a"], ["AC-2", "b"], ["AC-3", "c"]]


def test_join_keeps_items_referenced_by_rich_cells():
    doc = _pdf()
    first = _table(doc, 1, [HEADER, ["AC-1", "a"]], header_rows=1)
    referenced = doc.add_text(label=DocItemLabel.TEXT, text="rich cell body", parent=first, prov=_prov(1))
    rich = RichTableCell(
        text="",
        ref=referenced.get_ref(),
        start_row_offset_idx=1,
        end_row_offset_idx=2,
        start_col_offset_idx=1,
        end_col_offset_idx=2,
    )
    first.data.table_cells[-1] = rich
    _table(doc, 2, [HEADER, ["AC-2", "b"]], header_rows=1)

    chain = plan_rejoins(doc, {}, "pdf")[first.self_ref]

    assert chain.cells == [HEADER, ["AC-1", "rich cell body"], ["AC-2", "b"]]


def test_no_join_when_continuation_has_only_the_repeated_header():
    """Each join adds a page with rows, so table_rejoin_count (distinct row pages - 1) counts every join."""
    doc = _pdf()
    _table(doc, 1, [HEADER, ["AC-1", "a"]], header_rows=1)
    _table(doc, 2, [HEADER], header_rows=1)

    assert plan_rejoins(doc, {}, "pdf") == {}


def test_no_join_when_unflagged_continuation_has_only_the_repeated_header():
    doc = _pdf()
    _table(doc, 1, [HEADER, ["AC-1", "a"]], header_rows=1)
    _table(doc, 2, [HEADER])

    assert plan_rejoins(doc, {}, "pdf") == {}


@pytest.mark.parametrize("failing_index", [0, 1], ids=["first-part", "would-be-tail"])
def test_part_that_fails_to_export_is_in_no_chain(monkeypatch, failing_index):
    doc = _pdf()
    parts = [
        _table(doc, 1, [HEADER, ["AC-1", "a"]], header_rows=1),
        _table(doc, 2, [HEADER, ["AC-2", "b"]], header_rows=1),
        _table(doc, 3, [HEADER, ["AC-3", "c"]], header_rows=1),
    ]
    failing_ref = parts[failing_index].self_ref
    real_export = TableItem.export_to_markdown

    def export_failing_on_one(self, *args, **kwargs):
        if self.self_ref == failing_ref:
            raise ValueError("unexportable")
        return real_export(self, *args, **kwargs)

    monkeypatch.setattr(TableItem, "export_to_markdown", export_failing_on_one)

    chains = plan_rejoins(doc, {}, "pdf")

    # A failing first part leaves the next two to join; a failing middle part leaves nothing to join.
    expected = [["#/tables/1", "#/tables/2"]] if failing_index == 0 else []
    assert [[part.self_ref for part in chain.parts] for chain in chains.values()] == expected
