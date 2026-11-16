"""A conforming structured output for tests to start from."""

from typing import Any

from crosscodex_ingestion.output_schema import SCHEMA_VERSION


def conforming_output() -> dict[str, Any]:
    """Return a fresh output that conforms to the current schema: a heading with a paragraph under it."""
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
            },
            {
                "element_id": "e-002",
                "type": "paragraph",
                "content": "Applies to all systems.",
                "page": 1,
                "level": None,
                "parent_id": "e-001",
                "style": {},
            },
        ],
        "metadata": {
            "element_count": 2,
            "detected_format": "pdf",
            "page_count": 1,
            "title": None,
            "processing_time_ms": 12.5,
            "dropped": [],
        },
    }
