"""
Structured sources — JSON, CSV and XLSX read through a mapping into the element output.

Runs in the structured worker subprocess (converter.convert_structured), which has its own
resource limits and never imports Docling or torch. The pipeline:

  1. A reader parses the untrusted bytes defensively (depth scan and strict JSON parsing; ZIP
     inspection before openpyxl; csv without dialect sniffing) and returns SourceNodes in source
     order, each with its location ("Requirements[5].Items[1]", "row 37") and raw fields.
  2. build_tree applies the mapping: coerces the ID, title, text and properties of every node,
     resolves parents (the JSON nesting, a parent column, or the ID-prefix rule) and checks IDs,
     titles, parents, cycles and depth.
  3. emit_elements walks the tree depth-first in source order, so a parent_id always names an
     earlier element.

Problems in the source are collected (up to MAX_LISTED_PROBLEMS listed, all counted) and raised
together as StructuredSourceError; nothing is dropped silently. A limit from config.py is
fatal at once (StructuredLimitError). Messages quote source values, which are the caller's own
data, through _echo: truncated and escaped.

`python -m crosscodex_ingestion.structured --mapping m.yaml input` runs steps 1-3 locally for
mapping authors (without the worker's resource limits).
"""

import argparse
import csv
import datetime as dt
import heapq
import io
import json
import math
import re
import sys
import time
import warnings
import zipfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from crosscodex_ingestion.config import IngestionSettings
from crosscodex_ingestion.mapping import Mapping, MappingError, load_mapping_file
from crosscodex_ingestion.zipdir import ZipDirectoryError, ZipDirectoryTooLarge, read_zip_directory

# Exit code of the structured worker when the input is at fault; the error file says how.
INPUT_ERROR_EXIT_CODE = 4
ERROR_FILE_NAME = "error.json"
MAX_LISTED_PROBLEMS = 20
# Heading levels in the output schema run from 1 to 100.
MAX_DEPTH = 100
_ECHO_CHARS = 64
_ECHO_ITEMS = 10


@dataclass(frozen=True)
class StructuredLimits:
    """Limits on untrusted structured input (config.py; the env var is the upper-cased name)."""

    max_json_depth: int
    max_structured_nodes: int
    max_table_rows: int
    max_table_columns: int
    max_cell_length: int
    max_id_length: int
    max_xlsx_entries: int
    max_xlsx_uncompressed_bytes: int
    max_xlsx_compression_ratio: int

    @classmethod
    def from_settings(cls, settings: IngestionSettings) -> "StructuredLimits":
        return cls(**{name: getattr(settings, name) for name in cls.__dataclass_fields__})


class StructuredSourceError(ValueError):
    """The source does not fit its mapping. A ValueError: the message is safe and actionable for the client."""

    def __init__(self, mapping_id: str, problems: list[str], locations: list[str], count: int) -> None:
        self.mapping_id = mapping_id
        self.problems = problems
        # Where each listed problem is: a JSON path, "row 37", 'sheet "x" row 37', "header row" or "source".
        # Built only from the mapping and positions, never from source values.
        self.locations = locations
        self.count = count
        if count == 1:
            message = f"Problem in source (mapping {mapping_id}): {problems[0]}"
        else:
            shown = f"; the first {len(problems)}:" if count > len(problems) else ":"
            listed = " ".join(f"{n}) {p}" for n, p in enumerate(problems, start=1))
            message = f"{count} problems in source (mapping {mapping_id}){shown} {listed}"
        super().__init__(message)

    @property
    def details(self) -> dict[str, str]:
        """Error.details for the client: mapping, count, problem.1 .. problem.N and location.1 .. location.N."""
        details = {"mapping": self.mapping_id, "count": str(self.count)}
        details.update({f"problem.{n}": p for n, p in enumerate(self.problems, start=1)})
        details.update({f"location.{n}": loc for n, loc in enumerate(self.locations, start=1)})
        return details

    def redacted(self) -> tuple[str, dict[str, str]]:
        """Message and details without source values, for the audit stream and logs.

        The problems quote the caller's data; only the caller (through GetDocument) gets them.
        """
        what = "Problem" if self.count == 1 else f"{self.count} problems"
        message = (
            f"{what} in source (mapping {self.mapping_id}) at {', '.join(self.locations)}; source values are "
            "withheld here, the document's error has the full message."
        )
        details = {"mapping": self.mapping_id, "count": str(self.count)}
        details.update({f"location.{n}": loc for n, loc in enumerate(self.locations, start=1)})
        return message, details


class StructuredLimitError(ValueError):
    """The source exceeds a configured limit; the message names the setting.

    *redacted_message* is the message without source values, for the audit stream and logs; it is
    the message itself when that quotes none.
    """

    def __init__(self, message: str, redacted_message: str | None = None) -> None:
        super().__init__(message)
        self.redacted_message = redacted_message or message


class _Problems:
    """Collects problems and reports them in source order: the first MAX_LISTED_PROBLEMS listed, all counted.

    Checks run in phases (reading, then IDs, parents, titles), so each problem is filed under the
    node being checked when it was found (at()): its index, for ordering, and its location.
    """

    def __init__(self, mapping_id: str) -> None:
        self.mapping_id = mapping_id
        self.position = -1
        self.location = "source"
        self.count = 0
        # Max-heap (negated keys) of the MAX_LISTED_PROBLEMS earliest problems, so memory stays bounded.
        self._earliest: list[tuple[int, int, str, str]] = []

    def at(self, position: int, location: str) -> None:
        """File the next problems under node *position* (-1: before every node) at *location*."""
        self.position = position
        self.location = location

    def add(self, problem: str) -> None:
        self.count += 1
        heapq.heappush(self._earliest, (-self.position, -self.count, problem, self.location))
        if len(self._earliest) > MAX_LISTED_PROBLEMS:
            heapq.heappop(self._earliest)

    def raise_if_any(self) -> None:
        if self.count:
            raise self._error()

    def fail(self, problem: str) -> StructuredSourceError:
        """Record *problem*, about the source as a whole, and return the error to raise now."""
        self.at(-1, "source")
        self.add(problem)
        return self._error()

    def _error(self) -> StructuredSourceError:
        listed = sorted(self._earliest, reverse=True)
        return StructuredSourceError(
            self.mapping_id, [p for _, _, p, _ in listed], [loc for _, _, _, loc in listed], self.count
        )


def _echo(value: str) -> str:
    """Quote a source value for a message: at most 64 characters, control characters escaped."""
    return repr(value if len(value) <= _ECHO_CHARS else value[:_ECHO_CHARS] + "…")


def _echo_list(values: list[str]) -> str:
    shown = ", ".join(_echo(v) for v in values[:_ECHO_ITEMS])
    more = len(values) - _ECHO_ITEMS
    return shown + (f", and {more} more" if more > 0 else "")


# Checked in order: bool is an int subclass.
_KINDS: tuple[tuple[type | tuple[type, ...], str], ...] = (
    (bool, "a boolean"),
    (dict, "an object"),
    (list, "an array"),
    (str, "a string"),
    ((int, float), "a number"),
    (type(None), "null"),
    ((dt.date, dt.time, dt.timedelta), "a date/time"),
)


def _kind(value: Any) -> str:
    """Name a value's type the way the messages do ("an object", "a boolean")."""
    return next((name for types, name in _KINDS if isinstance(value, types)), f"a {type(value).__name__}")


def _capitalized(location: str) -> str:
    return location[:1].upper() + location[1:]


@dataclass
class SourceNode:
    """One node (JSON object) or row as read: where it is, its raw fields, and its JSON parent's index."""

    location: str
    fields: dict[str, Any]
    parent: int | None = None
    # Set on nodes built from level columns or level cells, whose fields are not the mapping's id/title/text.
    id_field: str | None = None
    title_field: str | None = None
    text_field: str | None = None


@dataclass
class TreeNode:
    """A node after the mapping is applied."""

    location: str
    index: int  # of its SourceNode
    id: str
    title: str | None
    text: str | None
    properties: dict[str, str]
    children: list["TreeNode"] = field(default_factory=list)


class _Invalid:
    """A field whose value has the wrong type (already reported)."""


_INVALID = _Invalid()


def _decode_utf8(data: bytes, problems: _Problems) -> str:
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise problems.fail(f"The input is not valid UTF-8 (at byte {exc.start}).") from exc


# ---------------------------------------------------------------------------------------------
# Readers: untrusted bytes -> SourceNodes in source order, plus field names in first-seen order
# ---------------------------------------------------------------------------------------------

_NODE_LIMIT = "The source has more nodes than MAX_STRUCTURED_NODES ({})."
# One token per match: a complete string, a bracket, a run of other text, or a quote that opens no
# complete string. Tokenizing in one left-to-right pass keeps the scan linear.
_JSON_TOKEN = re.compile(r'"[^"\\]*(?:\\.[^"\\]*)*"|[\[\]{}]|[^"\[\]{}]+|"', re.DOTALL)


class _DuplicateKeyError(ValueError):
    def __init__(self, key: str) -> None:
        self.key = key


class _NonFiniteError(ValueError):
    def __init__(self, constant: str) -> None:
        self.constant = constant


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKeyError(key)
        result[key] = value
    return result


def _reject_constant(constant: str) -> Any:
    raise _NonFiniteError(constant)


def _check_json_depth(text: str, max_depth: int) -> None:
    """Refuse nesting deeper than *max_depth* before json parses (and recurses into) it.

    Brackets inside strings don't count. An unterminated string ends the scan: json rejects the input
    there, and retrying the string pattern from every later quote would make the scan quadratic.
    """
    depth = 0
    for match in _JSON_TOKEN.finditer(text):
        token = match.group()
        if token == '"':  # noqa: S105 (a JSON token, not a password)
            return
        if token in ("[", "{"):
            depth += 1
            if depth > max_depth:
                raise StructuredLimitError(f"The JSON input is nested deeper than MAX_JSON_DEPTH ({max_depth}) levels.")
        elif token in ("]", "}"):
            depth -= 1


def read_json(
    data: bytes, mapping: Mapping, limits: StructuredLimits, problems: _Problems
) -> tuple[list[SourceNode], list[str]]:
    spec = mapping.document["nodes"]
    text = _decode_utf8(data, problems)
    _check_json_depth(text, limits.max_json_depth)
    try:
        document = json.loads(text, object_pairs_hook=_no_duplicate_keys, parse_constant=_reject_constant)
    except _DuplicateKeyError as exc:
        raise problems.fail(
            f"The input has a JSON object with the key {_echo(exc.key)} twice; duplicate keys are ambiguous, "
            "so they are rejected."
        ) from exc
    except _NonFiniteError as exc:
        raise problems.fail(f"The input contains {exc.constant}, which is not valid JSON.") from exc
    except json.JSONDecodeError as exc:
        raise problems.fail(
            f"The input is not valid JSON: {exc.msg} at line {exc.lineno}, column {exc.colno}."
        ) from exc
    except ValueError as exc:  # e.g. an integer longer than Python's int-to-str digit limit
        raise problems.fail(f"The input is not valid JSON: {exc}.") from exc
    except RecursionError as exc:  # backstop: the depth scan above should make this unreachable
        raise StructuredLimitError(
            f"The JSON input is nested deeper than MAX_JSON_DEPTH ({limits.max_json_depth}) levels."
        ) from exc

    select: str = spec["select"]
    current = document
    walked: list[str] = []
    for segment in select.split(".") if select else []:
        where = _echo(".".join(walked)) if walked else "the root"
        if not isinstance(current, dict):
            raise problems.fail(f"Path {_echo(select)} not found: {where} is {_kind(current)}, not an object.")
        if segment not in current:
            owner = f"the object at {where}" if walked else "the root object"
            raise problems.fail(
                f"Path {_echo(select)} not found: {owner} has no key {_echo(segment)}. "
                f"Its keys: {_echo_list(list(current))}."
            )
        current = current[segment]
        walked.append(segment)
    if not isinstance(current, list):
        what = f"Path {_echo(select)} selects" if select else 'The root (select "") is'
        raise problems.fail(f"{what} {_kind(current)}, not an array of nodes.")

    children_key: str | None = spec.get("children")
    nodes: list[SourceNode] = []
    items = 0  # array items seen, nodes or not
    field_names: dict[str, None] = {}
    prefix = select
    stack: list[tuple[Any, str, int | None]] = [
        (item, f"{prefix}[{i}]", None) for i, item in reversed(list(enumerate(current)))
    ]
    while stack:
        item, location, parent = stack.pop()
        problems.at(len(nodes), location)
        items += 1
        if items > limits.max_structured_nodes:
            raise StructuredLimitError(_NODE_LIMIT.format(limits.max_structured_nodes))
        if not isinstance(item, dict):
            problems.add(f"Node at {location} is {_kind(item)}, not an object.")
            continue
        index = len(nodes)
        nodes.append(SourceNode(location, item, parent))
        field_names.update(dict.fromkeys(item))
        if children_key is None or item.get(children_key) is None:
            continue
        children = item[children_key]
        if not isinstance(children, list):
            problems.add(
                f"Field {_echo(children_key)} at {location} is {_kind(children)}; a node's children must be an array."
            )
            continue
        stack.extend(
            (child, f"{location}.{children_key}[{j}]", index) for j, child in reversed(list(enumerate(children)))
        )
    return nodes, list(field_names)


def _read_table(
    rows: Iterator[tuple[Any, ...]],
    first_row: int,
    locate: Callable[[int], str],
    mapping: Mapping,
    limits: StructuredLimits,
    problems: _Problems,
) -> tuple[list[SourceNode], list[str]]:
    """Turn table rows (the first is the header) into SourceNodes; *first_row* is the header's row number."""
    spec = mapping.document["rows"]
    try:
        header_cells = list(next(rows))
    except StopIteration:
        raise problems.fail("The table has no header row.") from None
    while header_cells and _coerce(header_cells[-1]) is None:
        header_cells.pop()
    if len(header_cells) > limits.max_table_columns:
        raise StructuredLimitError(
            f"The header row has {len(header_cells)} columns, more than MAX_TABLE_COLUMNS ({limits.max_table_columns})."
        )
    problems.at(-1, "header row")
    header: list[str] = []
    for number, cell in enumerate(header_cells, start=1):
        name = _coerce(cell)
        if isinstance(name, _Invalid):
            problems.add(f"Column {number} of the header row is {_kind(cell)}; column names must be text.")
        elif name is None:
            problems.add(f"Column {number} of the header row is blank; every column needs a name.")
        elif name in header:
            problems.add(f"Column {number} of the header row repeats the name {_echo(name)}.")
        header.append(name if isinstance(name, str) else "")
    mapped = _mapped_fields(spec)
    for column in mapped:
        if column not in header:
            problems.add(
                f"Column {_echo(column)} not found in the header row. Columns: {_echo_list([h for h in header if h])}."
            )
    problems.raise_if_any()

    nodes: list[SourceNode] = []
    width = len(header)
    for offset, row in enumerate(rows, start=1):
        if offset > limits.max_table_rows:
            raise StructuredLimitError(f"The table has more data rows than MAX_TABLE_ROWS ({limits.max_table_rows}).")
        location = locate(first_row + offset)
        problems.at(len(nodes), location)
        stray = next((n for n in range(len(row) - 1, width - 1, -1) if _coerce(row[n]) is not None), None)
        if stray is not None:
            problems.add(f"{_capitalized(location)} has a value in column {stray + 1}, which has no header.")
            continue
        cells = row[:width]
        if all(_coerce(cell) is None for cell in cells):
            continue  # a blank row carries nothing
        if len(nodes) >= limits.max_structured_nodes:
            raise StructuredLimitError(_NODE_LIMIT.format(limits.max_structured_nodes))
        nodes.append(
            SourceNode(location, {name: cells[i] if i < len(cells) else None for i, name in enumerate(header)})
        )
    return nodes, header


def read_csv(
    data: bytes, mapping: Mapping, limits: StructuredLimits, problems: _Problems
) -> tuple[list[SourceNode], list[str]]:
    text = _decode_utf8(data, problems)
    if "\0" in text:
        raise problems.fail("The input contains a NUL character, so it is not CSV text.")
    # Process-wide; the worker reads one source per process.
    csv.field_size_limit(limits.max_cell_length)
    reader = csv.reader(
        io.StringIO(text, newline=""), delimiter=mapping.document["source"].get("delimiter", ","), strict=True
    )

    def rows() -> Iterator[tuple[Any, ...]]:
        try:
            for row in reader:
                yield tuple(row)
        except csv.Error as exc:
            if "field larger than field limit" in str(exc):
                raise StructuredLimitError(
                    f"A field near line {reader.line_num} is longer than MAX_CELL_LENGTH ({limits.max_cell_length})."
                ) from exc
            raise problems.fail(f"The input is not valid CSV near line {reader.line_num}: {exc}.") from exc

    return _read_table(rows(), 1, lambda n: f"row {n}", mapping, limits, problems)


def _inspect_zip(data: bytes, limits: StructuredLimits, problems: _Problems) -> None:
    """Check the archive's directory before openpyxl decompresses anything.

    zipfile never returns more than an entry's declared size, so the declared sizes bound the work.
    """
    # zipfile walks every record within the declared directory size; count them first, bounded.
    try:
        directory = read_zip_directory(data, limits.max_xlsx_entries)
    except ZipDirectoryTooLarge as exc:
        raise StructuredLimitError(
            f"The workbook has more than {limits.max_xlsx_entries} ZIP entries "
            f"(MAX_XLSX_ENTRIES ({limits.max_xlsx_entries}))."
        ) from exc
    except ZipDirectoryError as exc:
        raise problems.fail(f"The input is not a valid XLSX file: {exc}.") from exc
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise problems.fail(f"The input is not a valid XLSX file: {exc}.") from exc
    except NotImplementedError as exc:  # e.g. an extract version zipfile doesn't support
        raise problems.fail(f"The input is not a valid XLSX file: it uses an unsupported ZIP feature ({exc}).") from exc
    with archive:
        entries = archive.infolist()
        # The cap above holds only if zipfile read the directory zipdir counted.
        if len(entries) != directory.entries:
            raise problems.fail(
                "The input is not a valid XLSX file: its ZIP directory reads differently to different readers."
            )
        if any(entry.flag_bits & 0x1 for entry in entries):
            raise problems.fail("The workbook has encrypted entries; encrypted workbooks are not supported.")
        total = sum(entry.file_size for entry in entries)
        if total > limits.max_xlsx_uncompressed_bytes:
            raise StructuredLimitError(
                f"The workbook expands to {total} bytes, more than MAX_XLSX_UNCOMPRESSED_BYTES "
                f"({limits.max_xlsx_uncompressed_bytes})."
            )
        for entry in entries:
            if entry.file_size > limits.max_xlsx_compression_ratio * max(entry.compress_size, 1):
                expands = (
                    f"expands {entry.file_size} bytes from {entry.compress_size}, more than "
                    f"MAX_XLSX_COMPRESSION_RATIO ({limits.max_xlsx_compression_ratio}) times."
                )
                raise StructuredLimitError(f"ZIP entry {_echo(entry.filename)} {expands}", f"A ZIP entry {expands}")


_ENTITY_PROBE = b'<!DOCTYPE r [<!ENTITY e "expanded">]><r>&e;</r>'


def _require_defused_openpyxl() -> None:
    """Fail closed unless openpyxl parses workbook XML without expanding entities.

    openpyxl uses defusedxml when it is importable (openpyxl.DEFUSEDXML) for iterparse (worksheets,
    shared strings); fromstring is defusedxml's, or lxml's with resolve_entities=False when lxml is
    installed. Both are probed rather than trusted.
    """
    import openpyxl
    import openpyxl.xml.functions as xml_functions

    if openpyxl.DEFUSEDXML is not True:
        raise RuntimeError("openpyxl is not using defusedxml (openpyxl.DEFUSEDXML is False); refusing to read XLSX.")
    try:
        root = xml_functions.fromstring(_ENTITY_PROBE)
    except ValueError:  # defusedxml.EntitiesForbidden
        root = None
    expanded_by_fromstring = root is not None and root.text == "expanded"
    try:
        expanded_by_iterparse = any(
            element.text == "expanded" for _, element in xml_functions.iterparse(io.BytesIO(_ENTITY_PROBE))
        )
    except ValueError:
        expanded_by_iterparse = False
    if expanded_by_fromstring or expanded_by_iterparse:
        raise RuntimeError("openpyxl's XML parser expands entities (defusedxml not in effect); refusing to read XLSX.")


def _unreadable(exc: BaseException, problems: _Problems) -> StructuredSourceError:
    """The problem for an exception openpyxl raised; it wraps parser errors, so the whole chain is checked."""
    from defusedxml import DefusedXmlException

    cause: BaseException | None = exc
    while cause is not None:
        if isinstance(cause, DefusedXmlException):
            return problems.fail("The workbook contains XML entity declarations, which are not allowed.")
        cause = cause.__cause__ or cause.__context__
    return problems.fail(f"The workbook could not be read ({type(exc).__name__}).")


def read_xlsx(
    data: bytes, mapping: Mapping, limits: StructuredLimits, problems: _Problems
) -> tuple[list[SourceNode], list[str]]:
    _inspect_zip(data, limits, problems)
    _require_defused_openpyxl()
    import openpyxl

    source = mapping.document["source"]
    sheet: str = source["sheet"]
    header_row: int = source.get("header_row", 1)
    try:
        with warnings.catch_warnings():
            # Harmless for reading values (NIST CSF 2.0 has no named styles); it only clutters output.
            warnings.filterwarnings("ignore", message="Workbook contains no default style", category=UserWarning)
            workbook = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True, keep_links=False)
    except Exception as exc:  # openpyxl raises many types for malformed workbooks; report the type only
        raise _unreadable(exc, problems) from exc
    try:
        worksheets = [worksheet.title for worksheet in workbook.worksheets]
        if sheet not in worksheets:
            what = "is a chart sheet, not a worksheet" if sheet in workbook.sheetnames else "not found"
            raise problems.fail(f"Sheet {_echo(sheet)} {what}. Worksheets: {_echo_list(worksheets)}.")
        worksheet = workbook[sheet]
        # Read-only openpyxl trusts the sheet's <dimension>: a stale one truncates rows and columns, a
        # huge one pads every row. Without it, rows are as wide as their last cell.
        worksheet.reset_dimensions()
        rows = worksheet.iter_rows(min_row=header_row, values_only=True)
        try:
            return _read_table(rows, header_row, lambda n: f'sheet "{sheet}" row {n}', mapping, limits, problems)
        except (StructuredSourceError, StructuredLimitError):
            raise
        except Exception as exc:
            raise _unreadable(exc, problems) from exc
    finally:
        workbook.close()


_READERS = {"json": read_json, "csv": read_csv, "xlsx": read_xlsx}


# ---------------------------------------------------------------------------------------------
# Mapping application: SourceNodes -> tree
# ---------------------------------------------------------------------------------------------


def _mapped_fields(spec: dict[str, Any]) -> list[str]:
    """Source fields the mapping uses, in a stable order."""
    names = [spec[role] for role in ("id", "title", "text", "children") if role in spec]
    names += [column for level in spec.get("levels", []) for column in (level["id"], level["title"])]
    names += spec.get("level_cells", {}).get("columns", [])
    names += [column for column in [spec.get("parent", {}).get("column")] if column]
    names += list(spec.get("properties", {}).values())
    return list(dict.fromkeys(names))


def _coerce(value: Any) -> str | _Invalid | None:
    """str or number -> trimmed string (integral float -> integer string); blank or None -> None."""
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, float) and math.isfinite(value):
        return str(int(value)) if value.is_integer() else repr(value)
    return _INVALID


def _field(node: SourceNode, name: str | None, limits: StructuredLimits, problems: _Problems) -> str | _Invalid | None:
    if name is None:
        return None
    value = node.fields.get(name)
    coerced = _coerce(value)
    if isinstance(coerced, _Invalid):
        kind = "a non-finite number" if isinstance(value, float) else _kind(value)
        problems.add(
            f"Field {_echo(name)} at {node.location} is {kind}; IDs, titles, text and properties must be "
            "strings or numbers."
        )
    elif coerced is not None and len(coerced) > limits.max_cell_length:
        raise StructuredLimitError(
            f"Field {_echo(name)} at {node.location} has {len(coerced)} characters, more than MAX_CELL_LENGTH "
            f"({limits.max_cell_length})."
        )
    return coerced


def expand_levels(
    source_nodes: list[SourceNode], spec: dict[str, Any], limits: StructuredLimits, problems: _Problems
) -> list[SourceNode]:
    """Insert, before the first row that uses it, one node per distinct level ID; rows nest under their last level.

    Level nodes are keyed by ID alone, so one ID must always have the same title, parent and level.
    """
    levels: list[dict[str, str]] = spec["levels"]
    out: list[SourceNode] = []
    seen: dict[str, tuple[int, int | None, str | None, int]] = {}  # ID -> (level, parent index, title, node index)
    for row in source_nodes:
        parent: int | None = None
        parent_id: str | None = None
        complete = True
        for depth, level in enumerate(levels, start=1):
            problems.at(len(out), row.location)
            level_id = _field(row, level["id"], limits, problems)
            title = _field(row, level["title"], limits, problems)
            for column, value in ((level["id"], level_id), (level["title"], title)):
                if value is None:
                    problems.add(
                        f"{_capitalized(row.location)} has no value in level column {_echo(column)}; "
                        "every row must fill every level."
                    )
            if not isinstance(level_id, str) or not isinstance(title, str):
                complete = False
                break
            known = seen.get(level_id)
            if known is None:
                if len(out) >= limits.max_structured_nodes:
                    raise StructuredLimitError(_NODE_LIMIT.format(limits.max_structured_nodes))
                seen[level_id] = (depth, parent, title, len(out))
                out.append(
                    SourceNode(
                        row.location,
                        {level["id"]: level_id, level["title"]: title},
                        parent,
                        level["id"],
                        level["title"],
                    )
                )
                parent = len(out) - 1
            else:
                known_depth, known_parent, known_title, known_index = known
                where = out[known_index].location
                prefix = f"Conflicting level nodes: ID {_echo(level_id)} "
                if known_depth != depth:
                    problems.add(
                        f"{prefix}is at level {known_depth} at {where} and at level {depth} at {row.location}. "
                        "A level ID must have one level."
                    )
                elif known_title != title:
                    problems.add(
                        f"{prefix}is titled {_echo(known_title or '')} at {where} and {_echo(title)} at "
                        f"{row.location}. A level ID must have one title."
                    )
                elif known_parent != parent:
                    before = out[known_parent].fields[levels[depth - 2]["id"]] if known_parent is not None else None
                    problems.add(
                        f"{prefix}is under {_echo(before or '')} at {where} and under {_echo(parent_id or '')} at "
                        f"{row.location}. A level ID must have one parent."
                    )
                parent = known_index
            parent_id = level_id
        problems.at(len(out), row.location)
        if len(out) >= limits.max_structured_nodes:
            raise StructuredLimitError(_NODE_LIMIT.format(limits.max_structured_nodes))
        out.append(SourceNode(row.location, row.fields, parent if complete else None))
    return out


def _split_cell(label_and_text: str, cells: dict[str, Any]) -> tuple[str, str | None, str | None] | str:
    """Cut one level cell into (ID, title, text); return a problem message (no location) if it has no ID."""
    separator: str = cells["separator"]
    label, found, rest = label_and_text.partition(separator)
    label = label.strip()
    text = (rest.strip() or None) if found else None
    title: str | None = None
    brackets = cells.get("id_brackets")
    if brackets and label.endswith(brackets["close"]) and brackets["open"] in label:
        head, _, tail = label[: -len(brackets["close"])].rpartition(brackets["open"])
        if not tail.strip():
            return f"the ID between {_echo(brackets['open'])} and {_echo(brackets['close'])} is empty."
        title = head.strip() or None
        label = tail.strip()
    if not label:
        return f"the cell has no ID before {_echo(separator)}."
    return label, title, text


def expand_level_cells(
    source_nodes: list[SourceNode], spec: dict[str, Any], limits: StructuredLimits, problems: _Problems
) -> list[SourceNode]:
    """Turn each row into the node its filled level column names, nested under the nearest earlier row above it.

    A row that only repeats an earlier level node (same ID, same title, no text) re-enters that node instead
    of adding one: some publishers close each group with its bare label.
    """
    cells: dict[str, Any] = spec["level_cells"]
    columns: list[str] = cells["columns"]
    out: list[SourceNode] = []
    latest: list[int | None] = [None] * len(columns)  # newest node index at each level
    seen: dict[str, tuple[int, int]] = {}  # ID -> (node index, level index) of its first node
    for row in source_nodes:
        problems.at(len(out), row.location)
        values = [_field(row, column, limits, problems) for column in columns]
        if any(isinstance(value, _Invalid) for value in values):
            continue  # reported by _field
        filled = [depth for depth, value in enumerate(values) if value is not None]
        if len(filled) != 1:
            names = ", ".join(_echo(columns[depth]) for depth in filled)
            problems.add(
                f"{_capitalized(row.location)} fills {len(filled)} level columns ({names}); exactly one is allowed."
                if filled
                else f"{_capitalized(row.location)} fills none of the level columns "
                f"({', '.join(_echo(c) for c in columns)})."
            )
            continue
        depth = filled[0]
        parsed = _split_cell(str(values[depth]), cells)
        if isinstance(parsed, str):
            problems.add(f"{_capitalized(row.location)}: {parsed}")
            continue
        node_id, title, text = parsed
        parent = latest[depth - 1] if depth else None
        if depth and parent is None:
            problems.add(
                f"{_capitalized(row.location)} fills level column {_echo(columns[depth])} (level {depth + 1}), "
                f"but no row at level {depth} precedes it."
            )
            continue
        known = seen.get(node_id)
        if known is not None and text is None and known[1] == depth and out[known[0]].parent == parent:
            known_index = known[0]
            known_title = out[known_index].fields[f"{columns[depth]} (title)"]
            if known_title != title:
                problems.add(
                    f"Conflicting level nodes: ID {_echo(node_id)} is titled {_echo(known_title or '')} at "
                    f"{out[known_index].location} and {_echo(title or '')} at {row.location}. "
                    "A level ID must have one title."
                )
                continue
            latest[depth] = known_index
            latest[depth + 1 :] = [None] * (len(columns) - depth - 1)
            continue
        if len(out) >= limits.max_structured_nodes:
            raise StructuredLimitError(_NODE_LIMIT.format(limits.max_structured_nodes))
        id_key, title_key, text_key = (f"{columns[depth]} ({part})" for part in ("ID", "title", "text"))
        node_fields = {**row.fields, id_key: node_id, title_key: title, text_key: text}
        out.append(SourceNode(row.location, node_fields, parent, id_key, title_key, text_key))
        seen.setdefault(node_id, (len(out) - 1, depth))
        latest[depth] = len(out) - 1
        latest[depth + 1 :] = [None] * (len(columns) - depth - 1)
    return out


def build_tree(
    source_nodes: list[SourceNode], mapping: Mapping, limits: StructuredLimits, problems: _Problems
) -> list[TreeNode]:
    """Apply the mapping to *source_nodes*; return the roots in source order. Records every problem found."""
    spec = mapping.document["nodes" if mapping.format == "json" else "rows"]
    id_field: str = spec.get("id", "")  # absent only with level_cells, where every node names its own field
    title_field: str | None = spec.get("title")
    text_field: str | None = spec.get("text")

    tree: list[TreeNode | None] = []  # parallel to source_nodes; None where the node has no usable ID
    by_id: dict[str, int] = {}
    for index, source in enumerate(source_nodes):
        problems.at(index, source.location)
        node_id_field = source.id_field or id_field
        node_id = _field(source, node_id_field, limits, problems)
        title = _field(source, source.title_field or title_field, limits, problems)
        text = _field(source, source.text_field or text_field, limits, problems)
        properties: dict[str, str] = {}
        for name, source_field in spec.get("properties", {}).items():
            value = _field(source, source_field, limits, problems)
            if isinstance(value, str):
                properties[name] = value
        if node_id is None:
            problems.add(f"Node at {source.location} has no ID: field {_echo(node_id_field)} is missing or blank.")
        if not isinstance(node_id, str):
            tree.append(None)
            continue
        if len(node_id) > limits.max_id_length:
            raise StructuredLimitError(
                f"The ID at {source.location} has {len(node_id)} characters, more than MAX_ID_LENGTH "
                f"({limits.max_id_length})."
            )
        if node_id in by_id:
            problems.add(
                f"Duplicate ID {_echo(node_id)} at {source_nodes[by_id[node_id]].location} and {source.location}. "
                "IDs must be unique across the source."
            )
        else:
            by_id[node_id] = index
        tree.append(
            TreeNode(
                source.location,
                index,
                node_id,
                title if isinstance(title, str) else None,
                text if isinstance(text, str) else None,
                properties,
            )
        )

    parents = _resolve_parents(source_nodes, tree, by_id, spec, problems)
    _report_cycles(tree, parents, problems)

    roots: list[TreeNode] = []
    for node, parent in zip(tree, parents, strict=True):
        if node is None:
            continue
        if parent is None:
            roots.append(node)
        else:
            parent_node = tree[parent]
            if parent_node is not None:
                parent_node.children.append(node)

    for node in tree:
        if node is None:
            continue
        problems.at(node.index, node.location)
        values = source_nodes[node.index].fields
        node_title_field = source_nodes[node.index].title_field or title_field
        # A title or text of the wrong type is already reported; don't also call it missing.
        node_text_field = source_nodes[node.index].text_field or text_field
        wrong_type = {
            f for f in (node_title_field, node_text_field) if f and isinstance(_coerce(values.get(f)), _Invalid)
        }
        if node.children and node.title is None and node_title_field not in wrong_type:
            count = len(node.children)
            reason = (
                f"field {_echo(node_title_field)} is missing or blank"
                if node_title_field
                else "the mapping names no title field"
            )
            problems.add(
                f"Node {_echo(node.id)} (at {node.location}) has {count} {'child' if count == 1 else 'children'} "
                f"but no title: {reason}. A node with children becomes a heading, and headings need a title."
            )
        elif not node.children and node.title is None and node.text is None and not wrong_type:
            problems.add(
                f"Node {_echo(node.id)} (at {node.location}) has neither a title "
                f"({_echo(node_title_field) if node_title_field else 'not mapped'}) "
                f"nor text ({_echo(node_text_field) if node_text_field else 'not mapped'})."
            )

    stack = [(root, 1) for root in reversed(roots)]
    while stack:
        node, depth = stack.pop()
        problems.at(node.index, node.location)
        if depth > MAX_DEPTH:
            problems.add(
                f"Node {_echo(node.id)} (at {node.location}) is nested {depth} levels deep; at most {MAX_DEPTH} "
                f"levels are supported (heading levels 1-{MAX_DEPTH})."
            )
            continue
        stack.extend((child, depth + 1) for child in reversed(node.children))
    return roots


def _resolve_parents(
    source_nodes: list[SourceNode],
    tree: list[TreeNode | None],
    by_id: dict[str, int],
    spec: dict[str, Any],
    problems: _Problems,
) -> list[int | None]:
    """Index of each node's parent in *tree* (None for a root); report parents that name no node."""
    parent_rule: dict[str, Any] = spec.get("parent", {})
    if "id_prefix" in parent_rule:
        prefix_separator: str = parent_rule["id_prefix"]["separator"]
        ids = [node.id for node in tree if node is not None]
        if ids and not any(prefix_separator in node_id for node_id in ids):
            problems.at(-1, "source")
            problems.add(
                f"The ID-prefix rule (separator {_echo(prefix_separator)}) matches none of the {len(ids)} IDs, so "
                f"nothing would nest. Check the separator; sample IDs: {_echo_list(ids[:5])}."
            )
    parents: list[int | None] = []
    for index, node in enumerate(tree):
        source = source_nodes[index]
        problems.at(index, source.location)
        if source.parent is not None or node is None or not parent_rule:
            parents.append(source.parent)
            continue
        if "column" in parent_rule:
            column: str = parent_rule["column"]
            parent_id = _coerce(source.fields.get(column))
            if isinstance(parent_id, _Invalid):
                problems.add(
                    f"Field {_echo(column)} at {node.location} is {_kind(source.fields.get(column))}; IDs, titles, "
                    "text and properties must be strings or numbers."
                )
                parents.append(None)
                continue
            if parent_id is not None and parent_id not in by_id:
                problems.add(
                    f"{_capitalized(node.location)} (ID {_echo(node.id)}) names parent {_echo(parent_id)} in column "
                    f"{_echo(column)}, but no row has that ID."
                )
        else:
            separator: str = parent_rule["id_prefix"]["separator"]
            parent_id = node.id.rpartition(separator)[0] if separator in node.id else None
            if parent_id is not None and parent_id not in by_id:
                problems.add(
                    f"{_capitalized(node.location)} (ID {_echo(node.id)}) has parent {_echo(parent_id)} by the "
                    f"ID-prefix rule (separator {_echo(separator)}), but no row has that ID."
                )
        parents.append(by_id.get(parent_id) if parent_id is not None else None)
    return parents


def _report_cycles(tree: list[TreeNode | None], parents: list[int | None], problems: _Problems) -> None:
    """Report each parent cycle once, starting from its earliest node; nodes in a cycle reach no root."""
    state = [0] * len(tree)  # 0 unvisited, 1 on the current path, 2 done
    for start in range(len(tree)):
        path: list[int] = []
        index: int | None = start
        while index is not None and state[index] == 0:
            state[index] = 1
            path.append(index)
            index = parents[index]
        if index is not None and state[index] == 1:
            cycle = path[path.index(index) :]
            first = min(cycle)
            problems.at(first, tree[first].location)  # type: ignore[union-attr]
            cycle = cycle[cycle.index(first) :] + cycle[: cycle.index(first)]
            steps = " > ".join(f"{_echo(tree[i].id)} ({tree[i].location})" for i in cycle)  # type: ignore[union-attr]
            problems.add(f"Parent cycle: {steps} > {_echo(tree[first].id)}.")  # type: ignore[union-attr]
        for visited in path:
            state[visited] = 2


# ---------------------------------------------------------------------------------------------
# Emission: tree -> elements
# ---------------------------------------------------------------------------------------------


def emit_elements(roots: list[TreeNode]) -> list[dict[str, Any]]:
    """Pre-order, source-order elements: a node with children is a heading (then its text, then its children)."""
    elements: list[dict[str, Any]] = []

    def element(kind: str, content: str, level: int | None, parent_id: str | None) -> dict[str, Any]:
        item: dict[str, Any] = {
            "element_id": f"e-{len(elements) + 1:03d}",
            "type": kind,
            "content": content,
            "page": None,
            "level": level,
            "parent_id": parent_id,
            "style": {},
        }
        elements.append(item)
        return item

    stack: list[tuple[TreeNode, int, str | None]] = [(root, 1, None) for root in reversed(roots)]
    while stack:
        node, depth, parent_id = stack.pop()
        if node.children:
            heading = element("heading", node.title or "", depth, parent_id)
            heading.update(id=node.id, properties=node.properties)
            if node.text is not None:
                element("paragraph", node.text, None, heading["element_id"])["id"] = None
            stack.extend((child, depth + 1, heading["element_id"]) for child in reversed(node.children))
        else:
            leaf = element("paragraph", node.text if node.text is not None else node.title or "", None, parent_id)
            leaf["id"] = node.id
            if node.text is not None and node.title is not None:
                leaf["title"] = node.title
            leaf["properties"] = node.properties
    return elements


def convert_source(data: bytes, mapping: Mapping, limits: StructuredLimits) -> dict[str, Any]:
    """Read *data* through *mapping*; return {"elements", "metadata"} (without processing_time_ms)."""
    problems = _Problems(mapping.id)
    source_nodes, field_names = _READERS[mapping.format](data, mapping, limits, problems)
    spec = mapping.document["nodes" if mapping.format == "json" else "rows"]
    if "levels" in spec:
        source_nodes = expand_levels(source_nodes, spec, limits, problems)
        problems.raise_if_any()
    elif "level_cells" in spec:
        source_nodes = expand_level_cells(source_nodes, spec, limits, problems)
        problems.raise_if_any()
    roots = build_tree(source_nodes, mapping, limits, problems)
    problems.raise_if_any()
    elements = emit_elements(roots)
    used = set(_mapped_fields(spec))
    return {
        "elements": elements,
        "metadata": {
            "element_count": len(elements),
            "detected_format": mapping.format,
            "page_count": None,
            "title": mapping.title,
            "dropped": [],
            "source": {
                "kind": "structured",
                "format": mapping.format,
                "mapping": {"id": mapping.id, "sha256": mapping.sha256, "mapping_version": mapping.mapping_version},
                "node_count": len(source_nodes),
                "unmapped_fields": [name for name in field_names if name not in used],
            },
        },
    }


# ---------------------------------------------------------------------------------------------
# Entry points: the worker subprocess and the local CLI
# ---------------------------------------------------------------------------------------------


def run_job(input_path: str, output_path: str, job: dict[str, Any]) -> int:
    """Worker entry point. Writes the output, or ERROR_FILE_NAME beside it and returns INPUT_ERROR_EXIT_CODE.

    *job* comes from the service (converter.convert_structured, on the worker's stdin): the
    validated mapping document, its sha256 and the limits. The mapping is validated again here.
    """
    mapping = Mapping.from_document(job["mapping"], job["mapping_sha256"], "job")
    limits = StructuredLimits(**job["limits"])
    error: dict[str, Any]
    try:
        result = convert_source(Path(input_path).read_bytes(), mapping, limits)
    except StructuredSourceError as exc:
        error = {"kind": "source", "message": str(exc), "details": exc.details}
    except StructuredLimitError as exc:
        error = {"kind": "limit", "message": str(exc), "redacted_message": exc.redacted_message, "details": {}}
    else:
        Path(output_path).write_text(json.dumps(result, separators=(",", ":")), encoding="utf-8")
        return 0
    Path(output_path).with_name(ERROR_FILE_NAME).write_text(json.dumps(error), encoding="utf-8")
    return INPUT_ERROR_EXIT_CODE


def main(argv: list[str] | None = None) -> int:
    """Convert one file through one mapping file and print the output (for mapping authors)."""
    from crosscodex_ingestion.output_schema import SCHEMA_VERSION, validate_output
    from crosscodex_ingestion.security import confirm_structured_format

    parser = argparse.ArgumentParser(
        prog="python -m crosscodex_ingestion.structured",
        description="Convert a structured source through a mapping file and print the output JSON. "
        "Limits come from the same environment variables as the service; the worker's resource limits do not apply.",
    )
    parser.add_argument("--mapping", required=True, type=Path, help="mapping file (YAML)")
    parser.add_argument("input", type=Path, help="source file (JSON, CSV or XLSX)")
    args = parser.parse_args(argv)
    try:
        mapping = load_mapping_file(args.mapping)
    except MappingError as exc:
        sys.stderr.write(f"{exc}\n")
        return 2
    started = time.monotonic()
    try:
        data = args.input.read_bytes()
        confirm_structured_format(data, mapping.id, mapping.format)
        result = convert_source(data, mapping, StructuredLimits.from_settings(IngestionSettings()))
    except (OSError, ValueError) as exc:
        sys.stderr.write(f"{exc}\n")
        return 1
    result["metadata"]["processing_time_ms"] = round((time.monotonic() - started) * 1000, 2)
    output = {"schema_version": SCHEMA_VERSION, **result}
    validate_output(output)
    sys.stdout.write(json.dumps(output, indent=2, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
