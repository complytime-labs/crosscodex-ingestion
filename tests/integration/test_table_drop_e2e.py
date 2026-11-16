"""Integration tests: a table Docling can't serialize is recorded, not fatal, on a real DoclingDocument."""

import pytest
from docling_core.transforms.serializer import markdown as docling_markdown
from docling_core.types.doc import DocItemLabel, DoclingDocument, TableCell, TableData

from crosscodex_ingestion.extractor import extract_elements

pytestmark = pytest.mark.integration


def _document_with_a_table() -> DoclingDocument:
    doc = DoclingDocument(name="controls")
    doc.add_heading(text="Controls", level=1)
    cell = TableCell(
        text="AC-1", start_row_offset_idx=0, end_row_offset_idx=1, start_col_offset_idx=0, end_col_offset_idx=1
    )
    doc.add_table(data=TableData(num_rows=1, num_cols=1, table_cells=[cell]))
    doc.add_text(label=DocItemLabel.TEXT, text="After the table.")
    return doc


def test_failing_table_serializer_is_recorded_as_a_drop(monkeypatch):
    """TableItem.export_to_markdown and DoclingDocument.export_to_markdown share the serializer that raises."""

    def fail(*args, **kwargs):
        raise ValueError("bad cell span")

    monkeypatch.setattr(docling_markdown.MarkdownTableSerializer, "serialize", fail)
    doc = _document_with_a_table()
    with pytest.raises(ValueError, match="bad cell span"):
        doc.export_to_markdown()

    result = extract_elements(doc, None, "pdf")

    assert [e["content"] for e in result.elements] == ["Controls", "After the table."]
    assert result.metadata["dropped"] == [
        {"type": "table", "page": None, "reason": "table could not be exported to Markdown (ValueError)"}
    ]
