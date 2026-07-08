"""Unit tests for the extractor module."""

from collections.abc import Iterator
from dataclasses import dataclass

from crosscodex_ingestion.extractor import ExtractionResult, extract_elements


# Mock Docling types (avoid importing the real library in unit tests)
class DocItemLabel:
    """Mock DocItemLabel enum."""

    SECTION_HEADER = "section_header"
    TEXT = "text"
    PARAGRAPH = "paragraph"
    TABLE = "table"
    LIST_ITEM = "list_item"
    PAGE_HEADER = "page_header"
    PAGE_FOOTER = "page_footer"
    PICTURE = "picture"


@dataclass
class MockDocItem:
    """Mock Docling document item."""

    label: str
    text: str = ""
    level: int = 1


class MockDoclingDocument:
    """Mock DoclingDocument for testing."""

    def __init__(self, items: list[MockDocItem]):
        self._items = items

    def iterate_items(self) -> Iterator[tuple[MockDocItem, int]]:
        """Yield (item, depth) tuples."""
        for item in self._items:
            yield item, 0


class TestExtractElements:
    """Test the extract_elements function."""

    def test_empty_input_produces_empty_elements(self):
        """Empty input produces empty element list."""
        result = extract_elements(None, "", "txt")
        assert isinstance(result, ExtractionResult)
        assert result.elements == []
        assert result.metadata["element_count"] == 0

    def test_docling_document_with_headings_and_text(self):
        """Docling document with headings and text produces correct elements."""
        items = [
            MockDocItem(label=DocItemLabel.SECTION_HEADER, text="Introduction", level=1),
            MockDocItem(label=DocItemLabel.TEXT, text="This is introductory text."),
            MockDocItem(label=DocItemLabel.SECTION_HEADER, text="Background", level=1),
            MockDocItem(label=DocItemLabel.PARAGRAPH, text="Background information here."),
        ]
        doc = MockDoclingDocument(items)

        result = extract_elements(doc, "", "pdf")

        assert len(result.elements) == 4
        # First heading
        assert result.elements[0]["type"] == "heading"
        assert result.elements[0]["content"] == "Introduction"
        assert result.elements[0]["level"] == 1
        assert result.elements[0]["parent_id"] is None
        # First text
        assert result.elements[1]["type"] == "paragraph"
        assert result.elements[1]["content"] == "This is introductory text."
        # Second heading
        assert result.elements[2]["type"] == "heading"
        assert result.elements[2]["content"] == "Background"
        # Second paragraph
        assert result.elements[3]["type"] == "paragraph"
        assert result.elements[3]["content"] == "Background information here."

    def test_heading_hierarchy_tracking(self):
        """Nested headings track parent_id correctly."""
        items = [
            MockDocItem(label=DocItemLabel.SECTION_HEADER, text="Main Section", level=1),
            MockDocItem(label=DocItemLabel.SECTION_HEADER, text="Subsection A", level=2),
            MockDocItem(label=DocItemLabel.TEXT, text="Content under A."),
            MockDocItem(label=DocItemLabel.SECTION_HEADER, text="Subsection B", level=2),
            MockDocItem(label=DocItemLabel.SECTION_HEADER, text="Deep Section", level=3),
        ]
        doc = MockDoclingDocument(items)

        result = extract_elements(doc, "", "pdf")

        # Main Section is root (no parent)
        main_section = next(e for e in result.elements if e["content"] == "Main Section")
        assert main_section["parent_id"] is None

        # Subsection A has Main Section as parent
        sub_a = next(e for e in result.elements if e["content"] == "Subsection A")
        assert sub_a["parent_id"] == main_section["element_id"]

        # Content under A has Subsection A as parent
        content_a = next(e for e in result.elements if e["content"] == "Content under A.")
        assert content_a["parent_id"] == sub_a["element_id"]

        # Subsection B also has Main Section as parent (sibling of A)
        sub_b = next(e for e in result.elements if e["content"] == "Subsection B")
        assert sub_b["parent_id"] == main_section["element_id"]

        # Deep Section has Subsection B as parent
        deep = next(e for e in result.elements if e["content"] == "Deep Section")
        assert deep["parent_id"] == sub_b["element_id"]

    def test_element_ids_are_sequential(self):
        """Element IDs are generated sequentially (e-001, e-002, ...)."""
        items = [
            MockDocItem(label=DocItemLabel.SECTION_HEADER, text="First", level=1),
            MockDocItem(label=DocItemLabel.TEXT, text="Text one."),
            MockDocItem(label=DocItemLabel.SECTION_HEADER, text="Second", level=1),
            MockDocItem(label=DocItemLabel.TEXT, text="Text two."),
        ]
        doc = MockDoclingDocument(items)

        result = extract_elements(doc, "", "pdf")

        assert result.elements[0]["element_id"] == "e-001"
        assert result.elements[1]["element_id"] == "e-002"
        assert result.elements[2]["element_id"] == "e-003"
        assert result.elements[3]["element_id"] == "e-004"

    def test_markdown_heading_parsing(self):
        """Markdown headings are parsed correctly (tier 2)."""
        markdown = """# Level One

Content under level one.

## Level Two

Content under level two.

### Level Three

Deep content.
"""
        result = extract_elements(None, markdown, "md")

        headings = [e for e in result.elements if e["type"] == "heading"]
        assert len(headings) == 3

        assert headings[0]["content"] == "Level One"
        assert headings[0]["level"] == 1
        assert headings[0]["parent_id"] is None

        assert headings[1]["content"] == "Level Two"
        assert headings[1]["level"] == 2
        assert headings[1]["parent_id"] == headings[0]["element_id"]

        assert headings[2]["content"] == "Level Three"
        assert headings[2]["level"] == 3
        assert headings[2]["parent_id"] == headings[1]["element_id"]

    def test_pattern_detection_tier_3(self):
        """Pattern detection (tier 3) detects repeating patterns."""
        text = """Section 1. Introduction

Organizations shall implement security controls.

Section 2. Access Control

Access must be restricted to authorized users only.

Section 3. Audit Logging

All access events must be logged and monitored continuously.
"""
        result = extract_elements(None, text, "txt")

        # Should detect "Section N." pattern
        headings = [e for e in result.elements if e["type"] == "heading"]
        assert len(headings) >= 3

        # Verify section titles were extracted
        titles = [h["content"] for h in headings]
        assert any("Introduction" in t for t in titles)
        assert any("Access Control" in t for t in titles)
        assert any("Audit Logging" in t for t in titles)

    def test_paragraph_fallback(self):
        """Plain text without patterns falls back to paragraph splitting."""
        text = """This is the first paragraph with enough words to be meaningful.

This is the second paragraph that also has enough content.

Third paragraph goes here with sufficient length.
"""
        result = extract_elements(None, text, "txt")

        # All should be paragraphs
        assert all(e["type"] == "paragraph" for e in result.elements)
        assert len(result.elements) == 3

    def test_spurious_heading_filtering(self):
        """Headings repeated 3+ times are filtered as page artifacts."""
        markdown = """# RELEASE

Content one.

# Real Heading

Real content here.

# RELEASE

Content two.

# Another Real Heading

More content.

# RELEASE

Final content.
"""
        result = extract_elements(None, markdown, "md")

        headings = [e for e in result.elements if e["type"] == "heading"]
        heading_texts = [h["content"] for h in headings]

        # "RELEASE" should not appear in headings (appears 3 times)
        assert "RELEASE" not in heading_texts
        assert "Real Heading" in heading_texts
        assert "Another Real Heading" in heading_texts

    def test_element_type_mapping(self):
        """Element types are mapped correctly from DocItemLabel."""
        items = [
            MockDocItem(label=DocItemLabel.SECTION_HEADER, text="Heading"),
            MockDocItem(label=DocItemLabel.TEXT, text="Text content"),
            MockDocItem(label=DocItemLabel.PARAGRAPH, text="Paragraph content"),
            MockDocItem(label=DocItemLabel.LIST_ITEM, text="List item content"),
        ]
        doc = MockDoclingDocument(items)

        result = extract_elements(doc, "", "pdf")

        assert result.elements[0]["type"] == "heading"
        assert result.elements[1]["type"] == "paragraph"
        assert result.elements[2]["type"] == "paragraph"
        assert result.elements[3]["type"] == "list_item"

    def test_page_headers_and_footers_skipped(self):
        """PAGE_HEADER and PAGE_FOOTER elements are skipped by default."""
        items = [
            MockDocItem(label=DocItemLabel.PAGE_HEADER, text="Header text"),
            MockDocItem(label=DocItemLabel.SECTION_HEADER, text="Real Section", level=1),
            MockDocItem(label=DocItemLabel.TEXT, text="Real content."),
            MockDocItem(label=DocItemLabel.PAGE_FOOTER, text="Footer text"),
        ]
        doc = MockDoclingDocument(items)

        result = extract_elements(doc, "", "pdf")

        # Should only have 2 elements (heading + text, no headers/footers)
        assert len(result.elements) == 2
        assert result.elements[0]["type"] == "heading"
        assert result.elements[1]["type"] == "paragraph"

    def test_metadata_populated(self):
        """Metadata is populated correctly."""
        items = [
            MockDocItem(label=DocItemLabel.SECTION_HEADER, text="Test", level=1),
            MockDocItem(label=DocItemLabel.TEXT, text="Content."),
        ]
        doc = MockDoclingDocument(items)

        result = extract_elements(doc, "", "pdf")

        assert "element_count" in result.metadata
        assert result.metadata["element_count"] == 2
        assert "detected_format" in result.metadata
        assert result.metadata["detected_format"] == "pdf"
        assert "processing_time_ms" in result.metadata
        assert result.metadata["processing_time_ms"] >= 0

    def test_text_cleaning(self):
        """Text cleaning removes HTML comments and normalizes whitespace."""
        markdown = """# Heading

This    has   multiple    spaces.

<!-- This is a comment -->

Normal text here.
"""
        result = extract_elements(None, markdown, "md")

        # Find the paragraph with multiple spaces
        paras = [e for e in result.elements if e["type"] == "paragraph"]
        space_para = next((p for p in paras if "multiple" in p["content"]), None)
        assert space_para is not None
        # Multiple spaces should be collapsed to single
        assert "   " not in space_para["content"]

        # HTML comment should not appear in any content
        all_content = " ".join(e["content"] for e in result.elements)
        assert "<!--" not in all_content
        assert "comment -->" not in all_content
