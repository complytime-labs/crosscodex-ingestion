"""Structure signals measured from extracted elements.

`measure_structure` is a pure function over the element dicts produced by the
extractor (keys: element_id, type, content, page, level, parent_id, style).

Counts:
- heading_count: elements of type "heading".
- table_count: elements of type "table".
- max_heading_depth: highest heading `level`; 0 when there are no headings.
- unparented_count: non-heading elements whose `parent_id` is None.
- fragment_count: paragraph/list_item elements whose stripped content is at
  most FRAGMENT_MAX_CHARS characters (e.g. stray "(a)" or "12" list markers).
- warnings: fixed-order list of warning codes; the only code produced here is
  "no_headings" (NO_HEADINGS_MIN_ELEMENTS or more elements, zero headings).
"""

from typing import Any

NO_HEADINGS_MIN_ELEMENTS = 50
FRAGMENT_MAX_CHARS = 4


def measure_structure(elements: list[dict[str, Any]]) -> dict[str, Any]:
    """Return structure counts and warnings for a list of extracted elements."""
    heading_count = 0
    table_count = 0
    max_heading_depth = 0
    unparented_count = 0
    fragment_count = 0

    for element in elements:
        kind = element["type"]
        if kind == "heading":
            heading_count += 1
            max_heading_depth = max(max_heading_depth, element["level"] or 0)
            continue
        if kind == "table":
            table_count += 1
        elif len(element["content"].strip()) <= FRAGMENT_MAX_CHARS:
            fragment_count += 1
        if element["parent_id"] is None:
            unparented_count += 1

    warnings: list[str] = []
    if len(elements) >= NO_HEADINGS_MIN_ELEMENTS and heading_count == 0:
        warnings.append("no_headings")

    return {
        "heading_count": heading_count,
        "table_count": table_count,
        "max_heading_depth": max_heading_depth,
        "unparented_count": unparented_count,
        "fragment_count": fragment_count,
        "warnings": warnings,
    }
