"""
Fallback document structurer: a Python port of crosscodex's Go structurer (crosscodex commit 80f764fe),
used when a document has no element roles.

`structure_layout` is the entry point: it adapts ingestion layout elements to the inputs the Go tiers
consume (`adapt_layout`, a port of the no-roles path of `AdaptIngestionOutput` and its GFM table parser),
runs the heading, table and pattern tiers, and decomposes the winner's items into clauses
(`decompose_text`). `to_tree` turns the items into `structured.TreeNode`s.

Rules that keep this port byte-for-byte equal to Go (the goldens under tests/fixtures/fallback pin them):

- Every regex uses re.ASCII, so \\d and \\w do not match non-ASCII characters as RE2's do not.
- RE2's \\s is [\\t\\n\\f\\r ] (no \\v); write it that way.
- Python's `$` also matches before a trailing newline; use \\Z where Go's `$` has no (?m).
- Match per line. `^\\s*` under re.M can run across lines, which Go's does not.
- Whitespace in document text is Go's unicode.IsSpace, not Python's: use go_trim_space and go_fields,
  never str.strip() or str.split() on document text.
- Every pattern is listed in REGEXES so one test can check the flags.
"""

import re
import unicodedata
from dataclasses import dataclass
from typing import Any

from crosscodex_ingestion.structured import TreeNode

# Go's strings.TrimSpace/Fields use unicode.IsSpace: \t \n \v \f \r space U+0085 U+00A0, and for other
# code points the White_Space property, which is exactly general categories Zs, Zl and Zp. Python's
# str.isspace() differs: it also accepts U+001C-U+001F, which Go does not.
_GO_LATIN1_SPACES = frozenset("\t\n\v\f\r \u0085\u00a0")


def _is_go_space(ch: str) -> bool:
    if ch < "\u0100":
        return ch in _GO_LATIN1_SPACES
    return unicodedata.category(ch) in ("Zs", "Zl", "Zp")


def go_trim_space(s: str) -> str:
    """Go's strings.TrimSpace."""
    start, end = 0, len(s)
    while start < end and _is_go_space(s[start]):
        start += 1
    while end > start and _is_go_space(s[end - 1]):
        end -= 1
    return s[start:end]


def go_fields(s: str) -> list[str]:
    """Go's strings.Fields: the runs of non-space characters of s."""
    fields: list[str] = []
    start = -1
    for i, ch in enumerate(s):
        if _is_go_space(ch):
            if start >= 0:
                fields.append(s[start:i])
                start = -1
        elif start < 0:
            start = i
    if start >= 0:
        fields.append(s[start:])
    return fields


# One cell of a GFM table delimiter row. The cell is a single trimmed line, but \Z keeps Go's `$`.
_MARKDOWN_SEPARATOR_CELL = re.compile(r"^:?-+:?\Z", re.ASCII)

# The pattern tier's candidates, in Go's priority order (TierPattern). Each runs on one trimmed line, so
# there is no `$` and no multiline flag; \s is written [\t\n\f\r ] (RE2's, no \v).
_PATTERN_TIER_REGEXES: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.ASCII)
    for p in (
        r"^\d+\.\d+",
        r"^[A-Z]\.\d+",
        r"^Article[\t\n\f\r ]+\d+",
        r"^Section[\t\n\f\r ]+\d+",
        r"^Rule[\t\n\f\r ]+\d+",
        r"^§[\t\n\f\r ]*\d+",
        r"^\([a-z]\)",
        r"^\(\d+\)",
        r"^[ivxlcdm]+\.",
        r"^[a-z]\.",
        r"^[A-Z]\.",
        r"^\d+\.",
    )
)

# Decomposition's tiers, in Go's order (DecomposeText), with the words each clause needs (0: none). Go
# runs them under (?m) over one line at a time, so `$` is \Z; group 1 is the label, group 2 the clause.
_DECOMPOSE_TIERS: tuple[tuple[re.Pattern[str], int], ...] = tuple(
    (re.compile(p, re.ASCII), min_words)
    for p, min_words in (
        (r"^[\t\n\f\r ]*\(([a-zA-Z]{1,3}|\d+)\)[\t\n\f\r ]+(.+)\Z", 0),
        (r"^[\t\n\f\r ]*(\d{1,4})\.[\t\n\f\r ]+(.+)\Z", 8),
        (r"^[\t\n\f\r ]*(xiii|xii|xi|viii|vii|vi|iv|iii|ii|ix|x|v|i)\.[\t\n\f\r ]+(.+)\Z", 8),
        (r"^[\t\n\f\r ]*([a-z])\.[\t\n\f\r ]+(.+)\Z", 8),
        (r"^[\t\n\f\r ]*([A-Z])\.[\t\n\f\r ]+(.+)\Z", 8),
    )
)

REGEXES: tuple[re.Pattern[str], ...] = (
    _MARKDOWN_SEPARATOR_CELL,
    *_PATTERN_TIER_REGEXES,
    *(regex for regex, _ in _DECOMPOSE_TIERS),
)


class LayoutError(ValueError):
    """Layout elements break a rule the Go adapter enforces (ErrInvalidIngestionOutput)."""


@dataclass(frozen=True)
class Section:
    """A heading and the text of the elements under it (oscal.DocSection)."""

    level: int
    title: str
    text: str
    page: int | None


@dataclass(frozen=True)
class Table:
    """A parsed Markdown table (oscal.DocTable)."""

    headers: list[str]
    rows: list[list[str]]
    page: int | None


@dataclass(frozen=True)
class LayoutDoc:
    """What the Go tiers consume (oscal.StructuredDoc), plus the page of each raw_text line.

    line_pages[i] is the page of the element that line i of raw_text.split("\\n") came from. The blank
    line that joins two elements belongs to the element before it.
    """

    sections: list[Section]
    tables: list[Table]
    raw_text: str
    line_pages: list[int | None]


def adapt_layout(elements: list[dict[str, Any]]) -> LayoutDoc:
    """Build the structurer's input from layout elements without roles (Go: AdaptIngestionOutput).

    Section text is the content of the elements whose parent_id names the heading, joined by a blank
    line; raw_text is every element's content joined the same way. Elements without a parent_id reach
    raw_text only. A table element must hold a well-formed Markdown table (see _parse_markdown_table).
    """
    sections: list[Section] = []
    tables: list[Table] = []
    section_text: list[list[str]] = []
    section_of: dict[str, int] = {}
    raw: list[str] = []
    line_pages: list[int | None] = []

    for i, e in enumerate(elements):
        content: str = e["content"]
        page: int | None = e.get("page")
        # Go: checkIngestionElement. Only the rules whose violation would break this loop are ported;
        # the rest are enforced by ingestion's own schema validation.
        parent_id: str | None = e.get("parent_id")
        if parent_id is not None and parent_id not in section_of:
            raise LayoutError(
                f"elements[{i}] has a parent_id that is not an earlier heading; parent_id must name the "
                "heading the element sits under. Re-run the conversion"
            )
        if e["type"] == "heading" and e.get("level") is None:
            raise LayoutError(
                f"elements[{i}] is a heading with level null; a heading needs a level. Re-run the conversion"
            )

        if raw:
            line_pages.append(line_pages[-1])  # the blank line of the "\n\n" join
        raw.append(content)
        line_pages.extend([page] * (content.count("\n") + 1))

        if e["type"] == "heading":
            section_of[e["element_id"]] = len(sections)
            sections.append(Section(level=e["level"], title=content, text="", page=page))
            section_text.append([])
            continue
        if e["type"] == "table":
            tables.append(_parse_markdown_table(i, content, page))
        if parent_id is not None:
            section_text[section_of[parent_id]].append(content)

    with_text = [
        Section(level=s.level, title=s.title, text="\n\n".join(parts), page=s.page)
        for s, parts in zip(sections, section_text, strict=True)
    ]
    return LayoutDoc(sections=with_text, tables=tables, raw_text="\n\n".join(raw), line_pages=line_pages)


def _parse_markdown_table(i: int, content: str, page: int | None) -> Table:
    """Parse the first GFM table block of a table element (Go: parseMarkdownTable).

    The block is a header row, a delimiter row and body rows, each a line starting with "|", up to the
    first other line. Lines outside it are ignored. The delimiter and every body row must have as many
    cells as the header, else LayoutError: the adapter does not guess which cells are missing.
    """
    lines = content.split("\n")
    start = next(
        (j for j in range(len(lines) - 1) if _is_markdown_row(lines[j]) and _is_markdown_separator(lines[j + 1])), -1
    )
    if start < 0:
        raise LayoutError(
            f'elements[{i}] is a table with no Markdown header row (a "| ... |" line followed by a "|---|" '
            "line); schema v1 tables are Markdown. Re-run the conversion"
        )
    headers = _split_markdown_row(lines[start])
    delimiter = _split_markdown_row(lines[start + 1])
    if len(delimiter) != len(headers):
        raise LayoutError(
            f"elements[{i}] is a table whose delimiter row has {len(delimiter)} cells, but its header has "
            f"{len(headers)}. Re-run the conversion"
        )
    rows: list[list[str]] = []
    for line in lines[start + 2 :]:
        if not _is_markdown_row(line):
            break
        row = _split_markdown_row(line)
        if len(row) != len(headers):
            raise LayoutError(
                f"elements[{i}] is a table whose row {len(rows) + 1} has {len(row)} cells, but its header has "
                f"{len(headers)}; the adapter does not guess which cells are missing. Re-run the conversion"
            )
        rows.append(row)
    return Table(headers=headers, rows=rows, page=page)


def _is_markdown_row(line: str) -> bool:
    return go_trim_space(line).startswith("|")


def _is_markdown_separator(line: str) -> bool:
    return _is_markdown_row(line) and all(_MARKDOWN_SEPARATOR_CELL.match(c) for c in _split_markdown_row(line))


def _split_markdown_row(line: str) -> list[str]:
    """Split a "| a | b |" line into trimmed cells; "\\|" is a literal pipe inside a cell."""
    line = go_trim_space(line)
    line = line.removeprefix("|")
    if line.endswith("|") and not line.endswith("\\|"):
        line = line[:-1]
    cells: list[str] = []
    cell: list[str] = []
    j = 0
    while j < len(line):
        if line[j] == "\\" and j + 1 < len(line) and line[j + 1] == "|":
            cell.append("|")
            j += 1
        elif line[j] == "|":
            cells.append(go_trim_space("".join(cell)))
            cell = []
        else:
            cell.append(line[j])
        j += 1
    cells.append(go_trim_space("".join(cell)))
    return cells


@dataclass
class Item:
    """A control item a tier extracted (oscal.ControlItem). cls is "requirement" or "section".

    page is the layout page the item came from, None when unknown; Go has no such field.
    """

    id: str
    title: str
    text: str
    cls: str
    parent_id: str
    props: dict[str, str]
    page: int | None


# Go's TierHeading default for MaxHeadingRepeats; the fallback does not expose it.
_MAX_HEADING_REPEATS = 3
# Go's TierTable: a first cell of this many bytes or more is not used as the row's ID.
_MAX_TABLE_ID_BYTES = 50
# Go's TierPattern: a pattern must match at least this many lines to win.
_MIN_PATTERN_MATCHES = 3


def tier_heading(doc: LayoutDoc) -> list[Item] | None:
    """One item per heading, skipping headings whose title repeats more than 3 times (Go: TierHeading).

    The ID numbers the heading among all headings, filtered or not, so IDs can skip.
    """
    counts: dict[str, int] = {}
    for sec in doc.sections:
        counts[sec.title] = counts.get(sec.title, 0) + 1
    items = [
        Item(f"sec-{i + 1}", sec.title, sec.text, "requirement", "", {}, sec.page)
        for i, sec in enumerate(doc.sections)
        if counts[sec.title] <= _MAX_HEADING_REPEATS
    ]
    return items or None


def tier_table(doc: LayoutDoc) -> list[Item] | None:
    """One item per non-empty table row; the first cell is the ID when 1-49 bytes (Go: TierTable)."""
    items: list[Item] = []
    row_number = 0
    for table in doc.tables:
        for row in table.rows:
            if not row:
                continue
            row_number += 1
            first = go_trim_space(row[0])
            # Go's len() counts bytes, not characters.
            use_first = first != "" and len(first.encode("utf-8")) < _MAX_TABLE_ID_BYTES
            item_id = first if use_first else f"tbl-{row_number}"
            items.append(Item(item_id, "", " | ".join(row), "requirement", "", {}, table.page))
    return items or None


def tier_pattern(doc: LayoutDoc) -> list[Item] | None:
    """Items from the first numbering pattern that starts at least 3 non-blank lines (Go: TierPattern).

    A line is matched after trimming; the item ID is the matched prefix and the text the whole line.
    """
    lines = [(go_trim_space(line), page) for line, page in zip(doc.raw_text.split("\n"), doc.line_pages, strict=True)]
    for regex in _PATTERN_TIER_REGEXES:
        matched = [(m, line, page) for line, page in lines if line != "" and (m := regex.match(line))]
        if len(matched) >= _MIN_PATTERN_MATCHES:
            return [Item(go_trim_space(m.group(0)), "", line, "requirement", "", {}, page) for m, line, page in matched]
    return None


# Go's applyDecomposition default for MinDecomposeWords; the fallback does not expose it.
_MIN_DECOMPOSE_WORDS = 40


def decompose_text(base_id: str, text: str, min_words: int = _MIN_DECOMPOSE_WORDS) -> list[Item]:
    """Split text into labelled clauses with Go's 5-tier cascade (Go: DecomposeText), in pre-order.

    Text of fewer than min_words words, or that no tier splits into 2 or more clauses, is one requirement
    item with ID base_id and the trimmed text. Otherwise each clause is an item "base_id.label" (a repeated
    label's nth use gets "-n") with a label prop. A clause that splits again is a section holding its full
    text, followed by its own clauses, which name it as parent_id and parent-id prop. Go recurses; this
    walks an explicit stack.

    Deliberate departure from Go (decompose.go:197-203, spec "Fallback structurer" item 3): Go's outer loop
    overwrites the inner call's ParentID, so every item beneath a top-level clause names that clause and a
    nested section is left childless. Here each item names its nearest enclosing section.
    """
    clauses = _split_clauses(text, min_words)
    if clauses is None:
        return [Item(base_id, "", go_trim_space(text), "requirement", "", {}, None)]
    items: list[Item] = []
    # (clause ID, label, clause text, the enclosing section's ID or "" at the top)
    stack = [(clause_id, label, body, "") for clause_id, label, body in reversed(_clause_ids(base_id, clauses))]
    while stack:
        clause_id, label, body, parent = stack.pop()
        sub = _split_clauses(body, min_words)
        props = {"label": label}
        if parent:
            props["parent-id"] = parent
        items.append(Item(clause_id, "", body, "section" if sub else "requirement", parent, props, None))
        if sub:
            stack.extend((i, lbl, b, clause_id) for i, lbl, b in reversed(_clause_ids(clause_id, sub)))
    return items


def _clause_ids(base_id: str, clauses: list[tuple[str, str]]) -> list[tuple[str, str, str]]:
    """(ID, label, text) per clause: base_id.label, with "-n" on the nth use of a repeated label."""
    uses: dict[str, int] = {}
    out = []
    for label, body in clauses:
        uses[label] = uses.get(label, 0) + 1
        clause_id = f"{base_id}.{label}"
        if uses[label] > 1:
            clause_id = f"{clause_id}-{uses[label]}"
        out.append((clause_id, label, body))
    return out


def _split_clauses(text: str, min_words: int) -> list[tuple[str, str]] | None:
    """The (label, text) clauses of the first tier that yields at least 2, or None (Go: DecomposeText)."""
    if len(go_fields(text)) < min_words:
        return None
    for regex, clause_min_words in _DECOMPOSE_TIERS:
        clauses = _extract_clauses(text, regex, clause_min_words)
        if len(clauses) >= 2:
            return clauses
    return None


def _extract_clauses(text: str, regex: re.Pattern[str], min_words: int) -> list[tuple[str, str]]:
    """Group each matching line and the non-matching lines after it into a clause (Go: extractItems).

    The clause text is the line's text after the label, then the following lines, trimmed. A clause of
    fewer than min_words words (when min_words > 0) is dropped; lines before the first match are ignored.
    """
    clauses: list[tuple[str, str]] = []
    label = ""
    parts: list[str] = []

    def flush() -> None:
        if label:
            body = go_trim_space("\n".join(parts))
            if min_words == 0 or len(go_fields(body)) >= min_words:
                clauses.append((label, body))

    for line in text.split("\n"):
        m = regex.match(line)
        if m:
            flush()
            label, parts = m.group(1), [m.group(2)]
        elif label:
            parts.append(line)
    flush()
    return clauses


class NoStructure(Exception):
    """No tier of the cascade found controls in the layout (Go: ErrStructureFailed or the paragraph tier)."""


def structure_layout(elements: list[dict[str, Any]]) -> tuple[list[Item], str]:
    """Run the heading, table and pattern tiers and decompose the winner's items (Go: StructureWithTier).

    Returns the items in document order and the winning tier's name. A decomposed item is replaced by its
    clauses, which take its title and page; an item that does not split keeps its trimmed text. (Go also
    gives the clauses the item's ParentID; tier items have none, so that step is not ported.)
    Raises NoStructure when there are no elements or no tier finds items, and LayoutError for a malformed
    layout.
    """
    if not elements:
        # Go's adapter rejects an empty layout before the cascade; for ingestion that is no structure too.
        raise NoStructure("the layout has no elements")
    doc = adapt_layout(elements)
    for method, tier in (("heading", tier_heading), ("table", tier_table), ("pattern", tier_pattern)):
        tier_items = tier(doc)
        if tier_items:
            return _decompose_items(tier_items), method
    raise NoStructure("the layout has no headings, tables or numbered lines the fallback structurer can use")


def _decompose_items(tier_items: list[Item]) -> list[Item]:
    """Go's applyDecomposition at its default of 40 words."""
    result: list[Item] = []
    for item in tier_items:
        clauses = decompose_text(item.id, item.text)
        if len(clauses) == 1 and clauses[0].id == item.id:
            item.text = clauses[0].text
            result.append(item)
            continue
        for clause in clauses:
            clause.title = item.title
            clause.page = item.page
            result.append(clause)
    return result


def to_tree(items: list[Item]) -> list[TreeNode]:
    """The items as structured.TreeNode roots, children attached by parent_id, in item order.

    A parent_id names the latest earlier item with that ID (the pattern tier can repeat an ID). The
    parent-id prop is dropped: the tree carries it. Empty titles and texts become None.
    """
    roots: list[TreeNode] = []
    latest: dict[str, TreeNode] = {}
    for index, item in enumerate(items):
        node = TreeNode(
            location=f"items[{index}]",
            index=index,
            id=item.id,
            title=item.title or None,
            text=item.text or None,
            properties={k: v for k, v in item.props.items() if k != "parent-id"},
            role="section" if item.cls == "section" else "control",
            page=item.page,
        )
        if item.parent_id:
            latest[item.parent_id].children.append(node)
        else:
            roots.append(node)
        latest[item.id] = node
    return roots
