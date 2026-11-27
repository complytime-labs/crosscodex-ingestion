"""
Layout repair: the cell model of Docling table items, removal of unlabelled running headers and
footers, and the rejoin of tables continued across a page break.

The extractor calls this module around its walk of the Docling document. Docling-core is only imported for
type checking, so the module loads without it, as the extractor's unit tests need.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from crosscodex_ingestion.fallback import go_fields

if TYPE_CHECKING:
    from docling_core.types.doc import DoclingDocument, TableItem

# Frozen per ingestion release, like the extractor's other structural choices: not configuration.
FURNITURE_MIN_PAGES = 3  # a key must occur on this many distinct pages to be a running header or footer
FURNITURE_MARGIN = 0.10  # fraction of the page height, at the top and at the bottom, that counts as margin
FURNITURE_MAX_CHARS = 100  # a longer repeated text is more likely content than furniture

# Docling's TEXT and PARAGRAPH labels; DocItemLabel is a str enum, so the values compare equal.
_FURNITURE_LABELS = frozenset({"text", "paragraph"})
# Labels of the items that do not separate two table parts when their text is whitespace only.
_BLANK_TRANSPARENT_LABELS = frozenset({"text", "paragraph", "list_item"})


@dataclass(frozen=True)
class TableCells:
    """A table's grid as strings, and how many leading rows are column headers."""

    cells: list[list[str]]
    header_rows: int


def table_cells(item: "TableItem", doc: "DoclingDocument") -> TableCells:
    """Read a table's grid as-is: a spanned cell's text repeats in every position it covers.

    `header_rows` counts the leading rows in which at least one cell is flagged `column_header` and every
    non-empty cell is. A `RichTableCell` yields the text of the item its `ref` resolves to, or its own
    text when the `ref` does not resolve, so a cell is never dropped silently.
    """
    cells: list[list[str]] = []
    header_rows = 0
    counting_headers = True
    for row in item.data.grid:
        cells.append([_cell_text(cell, doc) for cell in row])
        if counting_headers:
            counting_headers = _is_header_row(row, cells[-1])
            header_rows += counting_headers
    return TableCells(cells=cells, header_rows=header_rows)


def _cell_text(cell: Any, doc: "DoclingDocument") -> str:
    """The text the Markdown export uses for *cell*."""
    ref = getattr(cell, "ref", None)
    if ref is not None:
        try:
            referenced = getattr(ref.resolve(doc), "text", None)
        except (AttributeError, IndexError, RuntimeError, TypeError, ValueError):
            referenced = None
        if isinstance(referenced, str):
            return referenced
    return str(cell.text)


def _is_header_row(row: list[Any], texts: list[str]) -> bool:
    """True when some cell is a column header and every non-empty cell is."""
    flagged = [bool(cell.column_header) for cell in row]
    non_empty = [" ".join(go_fields(text)) != "" for text in texts]
    return any(flagged) and all(f or not n for f, n in zip(flagged, non_empty, strict=True))


def find_furniture(doc: "DoclingDocument") -> dict[str, int]:
    """Find body-layer TEXT/PARAGRAPH items that are unlabelled running headers or footers.

    Returns `{item.self_ref: n}` for each item to remove, where n is the number of distinct pages the
    item's key (its text after Go-whitespace collapse) occurs on. A key is furniture when it is at most
    `FURNITURE_MAX_CHARS` long, occurs on at least `FURNITURE_MIN_PAGES` pages, and every occurrence
    lies in the top or bottom `FURNITURE_MARGIN` of its page. An occurrence with no bbox or no page size
    counts as outside the margin, so an unknown position never removes text.
    """
    occurrences: dict[str, list[Any]] = {}
    for item, _level in doc.iterate_items():
        if getattr(item, "label", None) not in _FURNITURE_LABELS:
            continue
        key = " ".join(go_fields(getattr(item, "text", "")))
        if key and len(key) <= FURNITURE_MAX_CHARS:
            occurrences.setdefault(key, []).append(item)

    furniture: dict[str, int] = {}
    for items in occurrences.values():
        pages = {prov.page_no for item in items for prov in item.prov}
        if len(pages) >= FURNITURE_MIN_PAGES and all(_in_margin(item, doc) for item in items):
            furniture.update({item.self_ref: len(pages) for item in items})
    return furniture


def _in_margin(item: Any, doc: "DoclingDocument") -> bool:
    """True when every prov bbox of *item* lies wholly in the top or bottom margin of its page."""
    if not item.prov:
        return False
    for prov in item.prov:
        page = doc.pages.get(prov.page_no)
        bbox = getattr(prov, "bbox", None)
        if page is None or page.size is None or page.size.height <= 0 or bbox is None:
            return False
        height = page.size.height
        box = bbox.to_top_left_origin(height)
        if not (box.b <= FURNITURE_MARGIN * height or box.t >= (1 - FURNITURE_MARGIN) * height):
            return False
    return True


@dataclass
class Chain:
    """A table continued across page breaks: its parts in order and the grid they form together.

    `cells`, `header_rows` and `row_pages` describe the merged grid: the first part's rows, then each later
    part's rows without its repeated header rows. `removed_header_pages` holds one `(part page, previous
    part page)` pair per removed header row.
    """

    parts: list["TableItem"]
    cells: list[list[str]]
    header_rows: int
    row_pages: list[int]
    removed_header_pages: list[tuple[int, int]]


def plan_rejoins(doc: "DoclingDocument", furniture: dict[str, int], format_hint: str) -> dict[str, Chain]:
    """Find the PDF tables continued on the next page, keyed by the first part's `self_ref`.

    Table B continues the chain ending in table A when both have exactly one prov page, B is the next
    table after A in `iterate_items()` order and on the next page, every item between them is removed
    furniture or a TEXT/PARAGRAPH/LIST_ITEM with whitespace-only text, B has the first part's column
    count, and either B's leading rows equal the first part's header rows (after Go-whitespace collapse,
    case-sensitive) or B has no header rows. Matching header rows are removed from B, and B must keep at
    least one row after that, so each join adds a page to `row_pages`.

    Only standalone items lie between parts: a part's descendants (Docling makes a table's captions and
    footnotes its children) and the items its `RichTableCell`s reference belong to the part. A table the
    walk drops (its cells or its Markdown cannot be read, or it has no rows) is in no chain and ends the
    one before it. Only chains of two or more parts are returned.
    """
    if format_hint != "pdf":
        return {}
    chains: list[Chain] = []
    chain: Chain | None = None  # the chain the next table may continue
    owned: set[str] = set()  # self_refs of the chain's parts and of the items their rich cells reference
    for node, _level in doc.iterate_items():
        if chain is not None and _belongs_to(node, owned, doc):
            continue
        label = getattr(node, "label", None)
        if label != "table":
            if chain is not None and not _between_parts(node, label, furniture):
                chain = None
            continue
        item = cast("TableItem", node)  # the TABLE label is only ever on a TableItem
        pages = {prov.page_no for prov in item.prov}
        if len(pages) != 1:
            chain = None
            continue
        try:
            cells = table_cells(item, doc)
        except Exception:
            # The walk drops this table with the exception type; here it only ends the chain.
            chain = None
            continue
        if not cells.cells:
            chain = None
            continue
        page = pages.pop()
        removed = _continuation(chain, item, cells, page) if chain is not None else None
        if chain is not None and removed is not None:
            # The walk drops a table it cannot export, so neither part of a join may be one.
            if not _exports(item, doc):
                chain = None
                continue
            if len(chain.parts) == 1 and not _exports(chain.parts[0], doc):
                chains.pop()
                removed = None
        if chain is None or removed is None:
            owned = set()
            chain = Chain(
                parts=[item],
                cells=list(cells.cells),
                header_rows=cells.header_rows,
                row_pages=[page] * len(cells.cells),
                removed_header_pages=[],
            )
            chains.append(chain)
            owned |= _owned_refs(item)
            continue
        owned |= _owned_refs(item)
        tail_page = chain.parts[-1].prov[0].page_no
        body = cells.cells[removed:]
        chain.parts.append(item)
        chain.cells.extend(body)
        chain.row_pages.extend([page] * len(body))
        chain.removed_header_pages.extend([(page, tail_page)] * removed)
    return {chain.parts[0].self_ref: chain for chain in chains if len(chain.parts) > 1}


def _between_parts(item: Any, label: Any, furniture: dict[str, int]) -> bool:
    """True when *item* may lie between two parts of one table: removed furniture or blank text."""
    if furniture and item.self_ref in furniture:
        return True
    return label in _BLANK_TRANSPARENT_LABELS and not go_fields(getattr(item, "text", ""))


def _belongs_to(node: Any, owned: set[str], doc: "DoclingDocument") -> bool:
    """True when *node* or one of its ancestors is in *owned*."""
    seen: set[str] = set()
    current = node
    while current is not None:
        ref = getattr(current, "self_ref", None)
        if ref is None or ref in seen:
            return False
        if ref in owned:
            return True
        seen.add(ref)
        parent = getattr(current, "parent", None)
        current = parent.resolve(doc) if parent is not None else None
    return False


def _owned_refs(item: "TableItem") -> set[str]:
    """The table's own self_ref and the refs of its rich cells."""
    refs = {item.self_ref}
    refs.update(ref.cref for cell in item.data.table_cells if (ref := getattr(cell, "ref", None)) is not None)
    return refs


def _exports(item: "TableItem", doc: "DoclingDocument") -> bool:
    """True when the walk would emit *item*: its Markdown export succeeds and is not blank."""
    try:
        return bool(item.export_to_markdown(doc=doc).strip())
    except Exception:
        return False


def _continuation(chain: Chain, item: "TableItem", cells: TableCells, page: int) -> int | None:
    """How many leading rows of *item* repeat the chain's header, or None when it does not continue *chain*."""
    if page != chain.parts[-1].prov[0].page_no + 1 or item.data.num_cols != chain.parts[0].data.num_cols:
        return None
    header = chain.header_rows
    if header and _collapsed(cells.cells[:header]) == _collapsed(chain.cells[:header]):
        # A part that is only the repeated header adds no rows: no join.
        return header if len(cells.cells) > header else None
    if cells.header_rows == 0:
        return 0
    return None


def _collapsed(rows: list[list[str]]) -> list[list[str]]:
    return [[" ".join(go_fields(text)) for text in row] for row in rows]


def render_chain(chain: Chain, doc: "DoclingDocument") -> str:
    """Render the merged table as Docling renders any one table: the first part with the merged data.

    The parts' cells are stacked with their row offsets shifted, and each later part's removed header rows
    are left out (a cell spanning out of them keeps its rows below). A failure is raised as a RuntimeError
    naming only pages and the exception type, without its cause: Docling's messages may quote cell text.
    """
    first = chain.parts[0]
    first_page = first.prov[0].page_no
    last_page = chain.parts[-1].prov[0].page_no
    try:
        merged_cells = []
        offset = 0
        for part in chain.parts:
            part_page = part.prov[0].page_no
            # Each part is on its own page (the previous part's page + 1, rule 3.3), so the page identifies the part.
            removed = 0 if part is first else sum(1 for page, _tail in chain.removed_header_pages if page == part_page)
            rows = part.data.num_rows
            for cell in part.data.table_cells:
                start = max(cell.start_row_offset_idx, removed)
                end = min(cell.end_row_offset_idx, rows)
                if end <= start:
                    continue
                update = {
                    "start_row_offset_idx": start - removed + offset,
                    "end_row_offset_idx": end - removed + offset,
                }
                if (start, end) != (cell.start_row_offset_idx, cell.end_row_offset_idx):
                    update["row_span"] = end - start
                merged_cells.append(cell.model_copy(update=update))
            offset += rows - removed
        merged = type(first.data)(
            table_cells=merged_cells,
            num_rows=offset,
            num_cols=first.data.num_cols,
            orientation=first.data.orientation,
        )
        return first.model_copy(update={"data": merged}).export_to_markdown(doc=doc).strip()
    except Exception as exc:
        raise RuntimeError(
            f"a table continued from page {first_page} to page {last_page} could not be rendered ({type(exc).__name__})"
        ) from None
