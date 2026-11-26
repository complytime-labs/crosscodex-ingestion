"""A conforming structured output for tests to start from."""

from typing import Any

from crosscodex_ingestion.output_schema import SCHEMA_VERSION


def conforming_output() -> dict[str, Any]:
    """Return a fresh document output that conforms to the current schema: a control with one control under it.

    metadata.structure is what the conversion worker measured on the layout it structured.
    """
    return {
        "schema_version": SCHEMA_VERSION,
        "elements": [
            {
                "element_id": "e-001",
                "type": "heading",
                "content": "Scope",
                "page": 1,
                "level": 1,
                "parent_id": None,
                "style": {},
                "id": "sec-1",
                "role": "control",
                "title": "Scope",
                "text": None,
                "properties": {},
            },
            {
                "element_id": "e-002",
                "type": "paragraph",
                "content": "Applies to all systems.",
                "page": 1,
                "level": None,
                "parent_id": "e-001",
                "style": {},
                "id": "sec-1.a",
                "role": "control",
                "title": None,
                "text": "Applies to all systems.",
                "properties": {"label": "a"},
            },
        ],
        "metadata": {
            "element_count": 2,
            "detected_format": "pdf",
            "page_count": 1,
            "title": None,
            "processing_time_ms": 12.5,
            "dropped": [],
            "structure_method": "heading",
            "structure": {
                "heading_count": 1,
                "table_count": 0,
                "max_heading_depth": 1,
                "unparented_count": 0,
                "fragment_count": 0,
                "warnings": [],
            },
        },
    }


def structured_output() -> dict[str, Any]:
    """Return a fresh output of a structured source: a node with children and text, and one leaf under it."""
    return {
        "schema_version": SCHEMA_VERSION,
        "elements": [
            {
                "element_id": "e-001",
                "type": "heading",
                "content": "Password Security",
                "page": None,
                "level": 1,
                "parent_id": None,
                "style": {},
                "id": "V6.2",
                "role": "control",
                "title": "Password Security",
                "text": "Passwords must be long.",
                "properties": {},
            },
            {
                "element_id": "e-002",
                "type": "paragraph",
                "content": "Passwords must be long.",
                "page": None,
                "level": None,
                "parent_id": "e-001",
                "style": {},
                "id": None,
            },
            {
                "element_id": "e-003",
                "type": "paragraph",
                "content": "Verify that passwords have at least 8 characters.",
                "page": None,
                "level": None,
                "parent_id": "e-001",
                "style": {},
                "id": "V6.2.1",
                "role": "control",
                "title": "Length",
                "text": "Verify that passwords have at least 8 characters.",
                "properties": {"level": "1"},
            },
        ],
        "metadata": {
            "element_count": 3,
            "detected_format": "json",
            "page_count": None,
            "title": "OWASP ASVS 5.0.0",
            "processing_time_ms": 3.5,
            "dropped": [],
            "source": {
                "kind": "structured",
                "format": "json",
                "mapping": {"id": "owasp-asvs-5.0.0-json", "sha256": "a" * 64, "mapping_version": 1},
                "node_count": 2,
                "unmapped_fields": ["Ordinal"],
            },
            "structure_method": "mapping",
        },
    }
