"""Feasibility gate: an excerpt's layout repair must match the full PDF's on the same pages.

Usage: python gate.py <excerpt docling.json> <full docling.json> <source pages>

<source pages> lists, comma-separated and in order, the full-PDF page each excerpt page was cut from (for
example 66,67,68,69,70,110). Both documents are captures by capture_docling.py. The script runs the
extractor on each and compares, in full-PDF page numbers:

- the joins (pairs of consecutive pages one table element spans), keeping a full-PDF join only when both
  of its pages are in the excerpt;
- the `dropped` entries' type and page, keeping a full-PDF `row` entry only when the page its table
  continued from is in the excerpt too. A `furniture` reason counts the pages its text occurs on, which
  trimming changes, so reasons are not compared.

It prints both sides and exits 0 when they are equal and 1 when they differ.
"""

import itertools
import json
import sys
from pathlib import Path
from typing import Any

from docling_core.types.doc import DoclingDocument

from crosscodex_ingestion.extractor import extract_elements

_ROW_REASON_PREFIX = "repeated header row of a table continued from page "

Dropped = tuple[str, int | None, int | None]


def _repair(path: Path, page_map: dict[int, int] | None) -> tuple[list[tuple[int, int]], list[Dropped]]:
    """The joins and the (type, page, continued-from page) of each dropped entry, in source pages."""

    def source(page: int | None) -> int | None:
        return page_map[page] if page_map is not None and page is not None else page

    doc = DoclingDocument.model_validate(json.loads(path.read_text(encoding="utf-8")))
    result = extract_elements(doc, None, "pdf")
    joins: list[tuple[int, int]] = []
    for element in result.elements:
        if element["type"] == "table":
            pages = sorted(set(element["row_pages"]))
            joins.extend(itertools.pairwise([page_map[p] for p in pages] if page_map is not None else pages))
    dropped: list[Dropped] = []
    for entry in result.metadata["dropped"]:
        reason: str = entry["reason"]
        continued_from = None
        if entry["type"] == "row":
            if not reason.startswith(_ROW_REASON_PREFIX):
                sys.exit(f"{path}: a row entry's reason does not start with {_ROW_REASON_PREFIX!r}")
            continued_from = source(int(reason.removeprefix(_ROW_REASON_PREFIX).split(";")[0]))
        dropped.append((entry["type"], source(entry["page"]), continued_from))
    return sorted(joins), sorted(dropped, key=str)


def main() -> None:
    if len(sys.argv) != 4:
        sys.exit(f"Usage: {sys.argv[0]} <excerpt docling.json> <full docling.json> <source pages>")
    source_pages = [int(page) for page in sys.argv[3].split(",")]
    excerpt_doc = DoclingDocument.model_validate(json.loads(Path(sys.argv[1]).read_text(encoding="utf-8")))
    if excerpt_doc.num_pages() != len(source_pages):
        sys.exit(f"the excerpt has {excerpt_doc.num_pages()} pages but {len(source_pages)} source pages were given")
    kept = set(source_pages)
    excerpt_joins, excerpt_dropped = _repair(Path(sys.argv[1]), {i + 1: p for i, p in enumerate(source_pages)})
    full_joins, full_dropped = _repair(Path(sys.argv[2]), None)
    full_joins = [join for join in full_joins if join[0] in kept and join[1] in kept]
    full_dropped = [d for d in full_dropped if d[1] in kept and (d[2] is None or d[2] in kept)]
    report: dict[str, Any] = {
        "excerpt joins": excerpt_joins,
        "full-PDF joins on these pages": full_joins,
        "excerpt dropped (type, page, continued from)": excerpt_dropped,
        "full-PDF dropped on these pages": full_dropped,
    }
    for label, value in report.items():
        print(f"{label}: {len(value)} {value}")
    passed = excerpt_joins == full_joins and excerpt_dropped == full_dropped
    print("GATE", "PASS" if passed else "FAIL")
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
