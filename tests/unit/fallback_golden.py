"""Helpers that read the fallback structurer's golden cases (tests/fixtures/fallback)."""

import json
from pathlib import Path
from typing import Any

FIXTURES = Path(__file__).parent.parent / "fixtures" / "fallback"
_ALLOWED_PROPS = {"label", "parent-id"}


def load_case(name: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Return (input elements, golden) of one fixture case."""
    case = FIXTURES / name
    elements = json.loads((case / "input.json").read_text(encoding="utf-8"))["elements"]
    return elements, json.loads((case / "golden.json").read_text(encoding="utf-8"))


def nearest_section_parents(golden: dict[str, Any]) -> list[str]:
    """The parent the port gives each golden item: its nearest enclosing section, not Go's ParentID.

    The port departs from Go here (spec "Fallback structurer" item 3; Go decompose.go:197-203 overwrites
    the inner call's ParentID, so every clause names its top-level clause). For an item whose ParentID P
    is not empty, the parent is the deepest golden section S (Class compliance-section) with S == P or
    S starting with P + ".", such that the item's ID starts with S + ".". Empty ParentIDs stay empty.
    """
    sections = [i["ID"] for i in golden["items"] if i["Class"] == "compliance-section"]
    parents = []
    for item in golden["items"]:
        p = item["ParentID"]
        if p:
            enclosing = [s for s in sections if (s == p or s.startswith(p + ".")) and item["ID"].startswith(s + ".")]
            if not enclosing:
                raise ValueError(f"golden item {item['ID']!r} has ParentID {p!r} but no enclosing section")
            p = max(enclosing, key=len)
        parents.append(p)
    return parents


def expected_nodes(golden: dict[str, Any]) -> list[dict[str, Any]]:
    """Map a golden's Go ControlItems to the structure nodes the Python port must produce.

    Empty strings become None; Class "compliance-section" is role "section", anything else "control";
    "parent-id" moves out of properties; the parent is the nearest enclosing section
    (nearest_section_parents). A Props key beyond label and parent-id fails loudly.
    """
    nodes = []
    for item, parent in zip(golden["items"], nearest_section_parents(golden), strict=True):
        props = dict(item["Props"] or {})
        unknown = set(props) - _ALLOWED_PROPS
        if unknown:
            raise ValueError(f"golden item {item['ID']!r} has unmapped Props keys {sorted(unknown)}")
        props.pop("parent-id", None)
        nodes.append(
            {
                "id": item["ID"],
                "title": item["Title"] or None,
                "text": item["Text"] or None,
                "parent": parent or None,
                "role": "section" if item["Class"] == "compliance-section" else "control",
                "properties": props,
            }
        )
    return nodes
