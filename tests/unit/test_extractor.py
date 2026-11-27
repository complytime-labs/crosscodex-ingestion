"""Unit tests for the extractor module."""

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import ClassVar

import pytest

from crosscodex_ingestion.extractor import ExtractionResult, extract_elements
from crosscodex_ingestion.output_schema import validate_output


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
    TITLE = "title"


@dataclass
class MockProvenance:
    """Mock Docling ProvenanceItem (only the page number is read)."""

    page_no: int


@dataclass
class MockDocItem:
    """Mock Docling document item."""

    label: str
    text: str = ""
    level: int = 1
    prov: list[MockProvenance] = field(default_factory=list)


@dataclass
class MockTableCell:
    """Mock Docling TableCell (only the text and header flag are read)."""

    text: str
    column_header: bool = False


@dataclass
class MockTableData:
    """Mock Docling TableData whose grid is *grid* or raises *error*."""

    grid_rows: list[list[MockTableCell]] = field(default_factory=lambda: [[MockTableCell("x")]])
    error: Exception | None = None
    table_cells: list[MockTableCell] = field(default_factory=list)

    @property
    def grid(self) -> list[list[MockTableCell]]:
        if self.error:
            raise self.error
        return self.grid_rows


@dataclass
class MockTableItem:
    """Mock Docling TableItem whose markdown export returns *markdown* or raises *error*."""

    markdown: str = ""
    error: Exception | None = None
    prov: list[MockProvenance] = field(default_factory=list)
    label: str = DocItemLabel.TABLE
    data: MockTableData = field(default_factory=MockTableData)
    self_ref: str = "#/tables/0"

    def export_to_markdown(self, doc: object = None) -> str:
        if self.error:
            raise self.error
        return self.markdown


class MockDoclingDocument:
    """Mock DoclingDocument for testing."""

    def __init__(self, items: list[MockDocItem], pages: int = 0, markdown: str | Exception = ""):
        self._items = items
        self._pages = pages
        self._markdown = markdown
        self.markdown_exports = 0

    def num_pages(self) -> int:
        return self._pages

    def export_to_markdown(self) -> str:
        """Docling renders tables here with the same serializer as TableItem.export_to_markdown."""
        self.markdown_exports += 1
        if isinstance(self._markdown, Exception):
            raise self._markdown
        return self._markdown

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
        # Only the converter sees the whole conversion, so it alone reports processing time.
        assert "processing_time_ms" not in result.metadata

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


class TestDoclingStructureFidelity:
    """Real documents (NIST SP 800-171r3) repeat section headings such as DISCUSSION on every requirement."""

    def test_repeated_section_headings_are_kept_in_typed_documents(self):
        """Docling labels page headers separately, so a repeated SECTION_HEADER is real structure."""
        items = []
        for req in ("03.01.01 Account Management", "03.01.02 Access Enforcement", "03.01.03 Flow Enforcement"):
            items += [
                MockDocItem(label=DocItemLabel.SECTION_HEADER, text=req, level=2),
                MockDocItem(label=DocItemLabel.TEXT, text=f"Requirement text of {req}."),
                MockDocItem(label=DocItemLabel.SECTION_HEADER, text="DISCUSSION", level=3),
                MockDocItem(label=DocItemLabel.TEXT, text=f"Informative discussion of {req}."),
            ]
        result = extract_elements(MockDoclingDocument(items), "", "pdf")

        by_id = {e["element_id"]: e for e in result.elements}
        discussions = [e for e in result.elements if e["type"] == "heading" and e["content"] == "DISCUSSION"]
        assert len(discussions) == 3
        for d in discussions:
            assert by_id[d["parent_id"]]["level"] == 2
        informative = [e for e in result.elements if e["content"].startswith("Informative discussion")]
        assert all(by_id[e["parent_id"]]["content"] == "DISCUSSION" for e in informative)

    def test_elements_carry_their_source_page(self):
        items = [
            MockDocItem(label=DocItemLabel.SECTION_HEADER, text="Scope", prov=[MockProvenance(page_no=3)]),
            MockDocItem(label=DocItemLabel.TEXT, text="Applies to all systems.", prov=[MockProvenance(page_no=4)]),
            MockDocItem(label=DocItemLabel.TEXT, text="No provenance recorded."),
        ]
        result = extract_elements(MockDoclingDocument(items, pages=4), "", "pdf")

        assert [e["page"] for e in result.elements] == [3, 4, None]

    def test_metadata_reports_page_count_and_document_title(self):
        items = [
            MockDocItem(label=DocItemLabel.TITLE, text="  Protecting Controlled Unclassified Information  "),
            MockDocItem(label=DocItemLabel.SECTION_HEADER, text="Introduction"),
            MockDocItem(label=DocItemLabel.TITLE, text="A later title-styled line"),
        ]
        result = extract_elements(MockDoclingDocument(items, pages=120), "", "pdf")

        assert result.metadata["page_count"] == 120
        assert result.metadata["title"] == "Protecting Controlled Unclassified Information"
        assert all(e["type"] != "title" for e in result.elements)

    def test_unpaginated_documents_report_no_page_count(self):
        """HTML and DOCX have no pages; Docling reports 0, which is not a page count."""
        items = [MockDocItem(label=DocItemLabel.TEXT, text="Body text of an HTML page.")]
        result = extract_elements(MockDoclingDocument(items, pages=0), "", "html")

        assert result.metadata["page_count"] is None
        assert result.metadata["title"] is None


class TestPdfHeadingLevels:
    """Docling's PDF pipeline sets every SECTION_HEADER to level 1; the levels come from section numbering."""

    FLAT = (
        MockDocItem(label=DocItemLabel.SECTION_HEADER, text="3. The Security Requirements"),
        MockDocItem(label=DocItemLabel.SECTION_HEADER, text="3.1. Access Control"),
        MockDocItem(label=DocItemLabel.SECTION_HEADER, text="03.01.01 Account Management"),
        MockDocItem(label=DocItemLabel.TEXT, text="Requirement text."),
        MockDocItem(label=DocItemLabel.SECTION_HEADER, text=""),
        MockDocItem(label=DocItemLabel.SECTION_HEADER, text="DISCUSSION"),
        MockDocItem(label=DocItemLabel.TEXT, text="Discussion text."),
        MockDocItem(label=DocItemLabel.SECTION_HEADER, text="3.2. Awareness and Training"),
    )

    @staticmethod
    def _tree(result: ExtractionResult) -> list[tuple[str, int | None, str | None]]:
        by_id = {e["element_id"]: e for e in result.elements}
        return [
            (e["content"], e["level"], by_id[e["parent_id"]]["content"] if e["parent_id"] else None)
            for e in result.elements
        ]

    def test_flat_pdf_headings_nest_by_their_numbering(self):
        result = extract_elements(MockDoclingDocument(list(self.FLAT)), None, "pdf")

        assert self._tree(result) == [
            ("3. The Security Requirements", 1, None),
            ("3.1. Access Control", 2, "3. The Security Requirements"),
            ("03.01.01 Account Management", 3, "3.1. Access Control"),
            ("Requirement text.", None, "03.01.01 Account Management"),
            ("DISCUSSION", 4, "03.01.01 Account Management"),
            ("Discussion text.", None, "DISCUSSION"),
            ("3.2. Awareness and Training", 2, "3. The Security Requirements"),
        ]

    @pytest.mark.parametrize("fmt", ["html", "docx"])
    def test_other_formats_keep_docling_levels(self, fmt):
        result = extract_elements(MockDoclingDocument(list(self.FLAT)), None, fmt)

        assert {e["level"] for e in result.elements if e["type"] == "heading"} == {1}

    def test_pdf_levels_docling_already_nested_are_kept(self):
        items = [
            MockDocItem(label=DocItemLabel.SECTION_HEADER, text="1.1 Purpose", level=1),
            MockDocItem(label=DocItemLabel.SECTION_HEADER, text="1.1.1 Scope", level=1),
            MockDocItem(label=DocItemLabel.SECTION_HEADER, text="Detail", level=2),
        ]
        result = extract_elements(MockDoclingDocument(items), None, "pdf")

        assert [e["level"] for e in result.elements] == [1, 1, 2]


class TestBlankHeadings:
    """CrossCodex turns headings into OSCAL groups, which need a title: a blank heading must never be emitted."""

    def test_blank_heading_is_not_emitted_and_its_content_attaches_to_the_heading_above(self):
        items = [
            MockDocItem(label=DocItemLabel.SECTION_HEADER, text="Access Control", level=1),
            MockDocItem(label=DocItemLabel.SECTION_HEADER, text="  ", level=2, prov=[MockProvenance(page_no=7)]),
            MockDocItem(label=DocItemLabel.TEXT, text="Restrict access to authorized users."),
        ]
        result = extract_elements(MockDoclingDocument(items, pages=7), "", "pdf")

        assert [(e["type"], e["content"]) for e in result.elements] == [
            ("heading", "Access Control"),
            ("paragraph", "Restrict access to authorized users."),
        ]
        assert result.elements[1]["parent_id"] == result.elements[0]["element_id"]
        assert result.metadata["dropped"] == [
            {
                "type": "heading",
                "page": 7,
                "reason": "heading has no text; its content is attached to the heading above",
            }
        ]

    def test_blank_heading_does_not_end_the_section_above(self):
        """A blank heading at a higher level must not pop the real headings off the hierarchy."""
        items = [
            MockDocItem(label=DocItemLabel.SECTION_HEADER, text="Chapter", level=1),
            MockDocItem(label=DocItemLabel.SECTION_HEADER, text="Section", level=2),
            MockDocItem(label=DocItemLabel.SECTION_HEADER, text="", level=1),
            MockDocItem(label=DocItemLabel.SECTION_HEADER, text="Subsection", level=3),
        ]
        result = extract_elements(MockDoclingDocument(items), "", "pdf")

        by_content = {e["content"]: e for e in result.elements}
        assert by_content["Subsection"]["parent_id"] == by_content["Section"]["element_id"]

    def test_blank_first_heading_leaves_its_content_at_the_top_level(self):
        items = [
            MockDocItem(label=DocItemLabel.SECTION_HEADER, text="", level=1),
            MockDocItem(label=DocItemLabel.TEXT, text="Preamble text."),
        ]
        result = extract_elements(MockDoclingDocument(items), "", "pdf")

        assert [(e["type"], e["parent_id"]) for e in result.elements] == [("paragraph", None)]
        assert result.metadata["dropped"][0]["reason"] == "heading has no text; its content stays at the top level"


class TestDroppedTables:
    """Requirement catalogs keep requirements in tables; a table that can't be exported must leave a trace."""

    def test_table_that_fails_to_export_is_recorded_with_its_page(self):
        items = [
            MockDocItem(label=DocItemLabel.SECTION_HEADER, text="Controls"),
            MockTableItem(error=ValueError("bad cell span"), prov=[MockProvenance(page_no=4)]),
            MockDocItem(label=DocItemLabel.TEXT, text="After the table."),
        ]
        result = extract_elements(MockDoclingDocument(items, pages=4), "", "pdf")

        assert [e["type"] for e in result.elements] == ["heading", "paragraph"]
        assert result.metadata["dropped"] == [
            {"type": "table", "page": 4, "reason": "table could not be exported to Markdown (ValueError)"}
        ]

    def test_table_that_exports_nothing_is_recorded(self):
        items = [MockDocItem(label=DocItemLabel.SECTION_HEADER, text="Controls"), MockTableItem(markdown="  \n")]
        result = extract_elements(MockDoclingDocument(items), "", "pdf")

        assert [e["type"] for e in result.elements] == ["heading"]
        assert result.metadata["dropped"] == [{"type": "table", "page": None, "reason": "table exported no content"}]

    def test_table_element_has_cells_header_rows_row_pages(self):
        grid = [[MockTableCell("ID", True), MockTableCell("Text", True)], [MockTableCell("AC-1"), MockTableCell("Do")]]
        items = [
            MockDocItem(label=DocItemLabel.SECTION_HEADER, text="Controls"),
            MockTableItem(markdown="| ID | Text |", data=MockTableData(grid), prov=[MockProvenance(page_no=3)]),
        ]

        result = extract_elements(MockDoclingDocument(items), "", "pdf")

        table = result.elements[1]
        assert table["cells"] == [["ID", "Text"], ["AC-1", "Do"]]
        assert table["header_rows"] == 1
        assert table["row_pages"] == [3, 3]

    def test_table_element_without_prov_has_null_row_pages(self):
        items = [MockTableItem(markdown="| a |")]

        result = extract_elements(MockDoclingDocument(items), "", "pdf")

        assert result.elements[0]["row_pages"] == [None]

    def test_unexportable_table_dropped_and_has_no_cells(self):
        items = [
            MockTableItem(
                markdown="| a |",
                data=MockTableData(error=ValueError("secret cell text")),
                prov=[MockProvenance(page_no=4)],
            ),
            MockDocItem(label=DocItemLabel.TEXT, text="After the table."),
        ]

        result = extract_elements(MockDoclingDocument(items), "", "pdf")

        assert [e["type"] for e in result.elements] == ["paragraph"]
        assert result.metadata["dropped"] == [
            {"type": "table", "page": 4, "reason": "table could not be exported to Markdown (ValueError)"}
        ]

    def test_exported_table_becomes_an_element_under_its_heading(self):
        items = [
            MockDocItem(label=DocItemLabel.SECTION_HEADER, text="Controls"),
            MockTableItem(markdown="| ID |\n|----|\n| AC-1 |\n"),
        ]
        result = extract_elements(MockDoclingDocument(items), "", "pdf")

        assert result.elements[1]["type"] == "table"
        assert result.elements[1]["content"] == "| ID |\n|----|\n| AC-1 |"
        assert result.elements[1]["parent_id"] == result.elements[0]["element_id"]
        assert result.metadata["dropped"] == []

    def test_drops_survive_the_markdown_fallback(self):
        """Docling's Markdown can't render what the typed pass dropped, so the drops still describe the output."""
        items = [
            MockDocItem(label=DocItemLabel.SECTION_HEADER, text=" ", prov=[MockProvenance(page_no=1)]),
            MockTableItem(markdown="", prov=[MockProvenance(page_no=2)]),
        ]
        doc = MockDoclingDocument(items, pages=2, markdown="Some body text with more than five words.")
        result = extract_elements(doc, None, "pdf")

        assert [e["type"] for e in result.elements] == ["paragraph"]
        assert [(d["type"], d["page"]) for d in result.metadata["dropped"]] == [("heading", 1), ("table", 2)]

    def test_unexportable_table_does_not_fail_extraction_when_the_typed_pass_has_content(self):
        """The document's Markdown export raises on the same table, so it is exported only for the fallback."""
        items = [
            MockDocItem(label=DocItemLabel.SECTION_HEADER, text="Controls"),
            MockTableItem(error=ValueError("bad cell span"), prov=[MockProvenance(page_no=4)]),
        ]
        doc = MockDoclingDocument(items, pages=4, markdown=ValueError("bad cell span"))
        result = extract_elements(doc, None, "pdf")

        assert doc.markdown_exports == 0
        assert [e["type"] for e in result.elements] == ["heading"]
        assert result.metadata["dropped"][0]["type"] == "table"

    def test_fallback_exports_the_document_markdown_when_none_is_given(self):
        doc = MockDoclingDocument([], markdown="# Scope\n\nApplies to all systems.\n")
        result = extract_elements(doc, None, "pdf")

        assert doc.markdown_exports == 1
        assert [e["content"] for e in result.elements] == ["Scope", "Applies to all systems."]


class TestExtractionConformsToSchema:
    """Every extraction path produces elements and metadata that pass the output schema."""

    @pytest.mark.parametrize(
        ("doc", "markdown", "fmt"),
        [
            (
                MockDoclingDocument(
                    [
                        MockDocItem(label=DocItemLabel.TITLE, text="Policy"),
                        MockDocItem(label=DocItemLabel.SECTION_HEADER, text="Scope", prov=[MockProvenance(page_no=1)]),
                        MockDocItem(label=DocItemLabel.SECTION_HEADER, text=" ", level=2),
                        MockDocItem(label=DocItemLabel.LIST_ITEM, text="Item one."),
                        MockTableItem(error=RuntimeError("boom"), prov=[MockProvenance(page_no=2)]),
                        MockTableItem(markdown="| a |\n|---|\n| 1 |"),
                    ],
                    pages=2,
                ),
                "",
                "pdf",
            ),
            (None, "# One\n\nText one.\n\n## Two\n\nText two.\n", "md"),
            (None, "Section 1. Intro\n\nBody one.\n\nSection 2. More\n\nBody two.\n\nSection 3. End\n\nBody.\n", "txt"),
            (None, "This paragraph has more than five words in it.\n\nSo does this second one here.\n", "txt"),
            (None, "", "txt"),
        ],
    )
    def test_extraction_output_validates(self, doc, markdown, fmt):
        result = extract_elements(doc, markdown, fmt)
        # Layout is the output shape before 1.4, which emits the structure found in it instead. Its cell
        # model is worker-internal and never ships, so the schema has no place for it.
        worker_internal = {"cells", "header_rows", "row_pages"}
        output = {
            "schema_version": "1.3",
            "elements": [{k: v for k, v in e.items() if k not in worker_internal} for e in result.elements],
            "metadata": {**result.metadata, "processing_time_ms": 1.0},
        }

        validate_output(output)


class TestMarkdownPreamble:
    """Text before the first heading must reach the output (issue #24)."""

    def test_text_before_first_heading_is_top_level(self):
        result = extract_elements(None, "Lead-in text.\n\nMore lead-in.\n\n# Intro\n\nBody.", "md")

        assert [(e["type"], e["content"], e["parent_id"]) for e in result.elements][:2] == [
            ("paragraph", "Lead-in text.", None),
            ("paragraph", "More lead-in.", None),
        ]
        heading = next(e for e in result.elements if e["type"] == "heading")
        body = next(e for e in result.elements if e["content"] == "Body.")
        assert result.elements.index(heading) == 2
        assert body["parent_id"] == heading["element_id"]
        assert len({e["element_id"] for e in result.elements}) == len(result.elements)

    def test_no_preamble_when_text_starts_with_heading(self):
        result = extract_elements(None, "# Intro\n\nBody.", "md")

        assert [e["type"] for e in result.elements] == ["heading", "paragraph"]

    def test_text_before_first_section_in_pattern_tier_is_kept(self):
        text = (
            "Preface text.\n\n"
            "Section 1. Introduction\n\nIntro body.\n\n"
            "Section 2. Access Control\n\nAccess body.\n\n"
            "Section 3. Audit Logging\n\nAudit body.\n"
        )
        result = extract_elements(None, text, "txt")

        assert result.elements[0]["content"] == "Preface text."
        assert result.elements[0]["type"] == "paragraph"
        assert result.elements[0]["parent_id"] is None
        assert sum(e["type"] == "heading" for e in result.elements) == 3

    def test_headingless_text_is_still_fully_extracted(self):
        result = extract_elements(None, "Only prose, but long enough.\n\nMore prose, also long enough.", "txt")

        assert [e["content"] for e in result.elements] == [
            "Only prose, but long enough.",
            "More prose, also long enough.",
        ]

    def test_short_paragraphs_skipped_by_last_tier_are_recorded(self):
        result = extract_elements(None, "Too short.\n\nThis paragraph has enough words to keep.", "txt")

        assert [e["content"] for e in result.elements] == ["This paragraph has enough words to keep."]
        assert result.metadata["dropped"] == [
            {"type": "paragraph", "page": None, "reason": "paragraph has fewer than 5 words"}
        ]


class TestUnlabelledFurniture:
    """Repeated margin text is removed from PDFs only, and each removal is reported."""

    REASON = "text repeated in the page margin on 3 pages; removed as an unlabelled running header or footer"

    @staticmethod
    def _document() -> object:
        from docling_core.types.doc import (
            BoundingBox,
            CoordOrigin,
            DoclingDocument,
            ProvenanceItem,
            Size,
        )
        from docling_core.types.doc import (
            DocItemLabel as RealLabel,
        )

        doc = DoclingDocument(name="f")
        for page_no in (1, 2, 3):
            doc.add_page(page_no=page_no, size=Size(width=600, height=1000))
            body = ProvenanceItem(
                page_no=page_no,
                bbox=BoundingBox(l=0, r=1, t=400, b=440, coord_origin=CoordOrigin.TOPLEFT),
                charspan=(0, 4),
            )
            margin = ProvenanceItem(
                page_no=page_no,
                bbox=BoundingBox(l=0, r=1, t=10, b=50, coord_origin=CoordOrigin.TOPLEFT),
                charspan=(0, 5),
            )
            doc.add_text(label=RealLabel.TEXT, text="Draft", prov=margin)
            doc.add_text(label=RealLabel.TEXT, text=f"Body {page_no} has enough words in it.", prov=body)
        return doc

    def test_furniture_dropped_entries_per_occurrence(self):
        doc = self._document()

        result = extract_elements(doc, None, "pdf")

        assert [e["content"] for e in result.elements] == [f"Body {n} has enough words in it." for n in (1, 2, 3)]
        assert result.metadata["dropped"] == [
            {"type": "furniture", "page": page, "reason": self.REASON} for page in (1, 2, 3)
        ]

    def test_furniture_not_applied_to_docx(self):
        doc = self._document()

        result = extract_elements(doc, None, "docx")

        assert [e["content"] for e in result.elements].count("Draft") == 3
        assert result.metadata["dropped"] == []


class TestTableRejoin:
    """A PDF table continued on the next page becomes one table element, and each removed header row is reported."""

    HEADER: ClassVar[list[str]] = ["ID", "Text"]

    @staticmethod
    def _row_reason(page: int) -> str:
        return f"repeated header row of a table continued from page {page}; removed when the parts were rejoined"

    @staticmethod
    def _pdf(pages: int = 4) -> object:
        from docling_core.types.doc import DoclingDocument, Size

        doc = DoclingDocument(name="r")
        for page_no in range(1, pages + 1):
            doc.add_page(page_no=page_no, size=Size(width=600, height=1000))
        return doc

    @staticmethod
    def _prov(page_no: int, top: float = 400.0) -> object:
        from docling_core.types.doc import BoundingBox, CoordOrigin, ProvenanceItem

        bbox = BoundingBox(l=0, r=100, t=top, b=top + 40, coord_origin=CoordOrigin.TOPLEFT)
        return ProvenanceItem(page_no=page_no, bbox=bbox, charspan=(0, 0))

    @classmethod
    def _table(cls, doc: object, page: int, rows: list[list[str]], header_rows: int = 0) -> object:
        from docling_core.types.doc import TableCell, TableData

        cells = [
            TableCell(
                text=text,
                start_row_offset_idx=r,
                end_row_offset_idx=r + 1,
                start_col_offset_idx=c,
                end_col_offset_idx=c + 1,
                column_header=r < header_rows,
            )
            for r, row in enumerate(rows)
            for c, text in enumerate(row)
        ]
        data = TableData(table_cells=cells, num_rows=len(rows), num_cols=len(rows[0]))
        return doc.add_table(data=data, prov=cls._prov(page))

    @staticmethod
    def _tables(result: ExtractionResult) -> list[dict]:
        return [e for e in result.elements if e["type"] == "table"]

    def test_four_part_chain(self):
        from crosscodex_ingestion.quality import measure_structure

        doc = self._pdf()
        for page in (1, 2, 3, 4):
            self._table(doc, page, [self.HEADER, [f"AC-{page}", "x"]], header_rows=1)

        result = extract_elements(doc, None, "pdf")

        [table] = self._tables(result)
        assert table["cells"] == [self.HEADER, ["AC-1", "x"], ["AC-2", "x"], ["AC-3", "x"], ["AC-4", "x"]]
        assert table["row_pages"] == [1, 1, 2, 3, 4]
        assert result.metadata["dropped"] == [
            {"type": "row", "page": page, "reason": self._row_reason(page - 1)} for page in (2, 3, 4)
        ]
        assert measure_structure(result.elements)["table_rejoin_count"] == 3

    def test_join_two_row_header_emits_two_row_entries(self):
        doc = self._pdf()
        header = [self.HEADER, ["", "(normative)"]]
        self._table(doc, 1, [*header, ["AC-1", "a"]], header_rows=2)
        self._table(doc, 2, [*header, ["AC-2", "b"]], header_rows=2)

        result = extract_elements(doc, None, "pdf")

        [table] = self._tables(result)
        assert (table["cells"], table["header_rows"]) == ([*header, ["AC-1", "a"], ["AC-2", "b"]], 2)
        assert result.metadata["dropped"] == [{"type": "row", "page": 2, "reason": self._row_reason(1)}] * 2

    @pytest.mark.parametrize("fmt", ["docx", "html", "txt"])
    def test_no_join_docx_html_txt(self, fmt):
        from crosscodex_ingestion.quality import measure_structure

        doc = self._pdf()
        self._table(doc, 1, [self.HEADER, ["AC-1", "a"]], header_rows=1)
        self._table(doc, 2, [self.HEADER, ["AC-2", "b"]], header_rows=1)

        result = extract_elements(doc, None, fmt)

        assert len(self._tables(result)) == 2
        assert result.metadata["dropped"] == []
        assert measure_structure(result.elements)["table_rejoin_count"] == 0

    def test_full_width_and_empty_cell_rows_kept_and_no_row_entry(self):
        from docling_core.types.doc import TableCell, TableData

        doc = self._pdf()
        self._table(doc, 1, [self.HEADER, ["AC-1", "a"]], header_rows=1)

        def cell(text: str, row: int, col: int, span: int = 1) -> TableCell:
            return TableCell(
                text=text,
                start_row_offset_idx=row,
                end_row_offset_idx=row + 1,
                start_col_offset_idx=col,
                end_col_offset_idx=col + span,
                col_span=span,
            )

        cells = [cell("GOAL", 0, 0, span=2), cell("", 1, 0), cell("", 1, 1), cell("AC-2", 2, 0), cell("b", 2, 1)]
        doc.add_table(data=TableData(table_cells=cells, num_rows=3, num_cols=2), prov=self._prov(2))

        result = extract_elements(doc, None, "pdf")

        [table] = self._tables(result)
        assert table["cells"] == [self.HEADER, ["AC-1", "a"], ["GOAL", "GOAL"], ["", ""], ["AC-2", "b"]]
        assert table["row_pages"] == [1, 1, 2, 2, 2]
        assert result.metadata["dropped"] == []

    def test_headerless_join_emits_no_row_entry(self):
        doc = self._pdf()
        self._table(doc, 1, [["AC-1", "a"]])
        self._table(doc, 2, [["AC-2", "b"]])

        result = extract_elements(doc, None, "pdf")

        [table] = self._tables(result)
        assert table["cells"] == [["AC-1", "a"], ["AC-2", "b"]]
        assert result.metadata["dropped"] == []

    def test_joined_element_keeps_first_part_id_page_parent(self):
        from docling_core.types.doc import DocItemLabel as RealLabel

        doc = self._pdf()
        doc.add_heading(text="Controls", prov=self._prov(1, top=100))
        self._table(doc, 1, [self.HEADER, ["AC-1", "a"]], header_rows=1)
        self._table(doc, 2, [self.HEADER, ["AC-2", "b"]], header_rows=1)
        doc.add_text(label=RealLabel.TEXT, text="After the table.", prov=self._prov(2, top=600))

        result = extract_elements(doc, None, "pdf")

        assert [(e["element_id"], e["type"], e["page"], e["parent_id"]) for e in result.elements] == [
            ("e-001", "heading", 1, None),
            ("e-002", "table", 1, "e-001"),
            ("e-003", "paragraph", 2, "e-001"),
        ]

    def test_unjoined_table_content_unchanged(self):
        doc = self._pdf()
        first = self._table(doc, 1, [self.HEADER, ["AC-1", "a"]], header_rows=1)
        gap = self._table(doc, 3, [self.HEADER, ["AC-3", "c"]], header_rows=1)

        result = extract_elements(doc, None, "pdf")

        assert [e["content"] for e in self._tables(result)] == [
            first.export_to_markdown(doc=doc).strip(),
            gap.export_to_markdown(doc=doc).strip(),
        ]

    def test_joined_content_is_docling_render_of_merged_data(self):
        doc = self._pdf()
        self._table(doc, 1, [self.HEADER, ["AC-1", "a"]], header_rows=1)
        self._table(doc, 2, [self.HEADER, ["AC-2", "b"]], header_rows=1)
        reference_doc = self._pdf()
        reference = self._table(reference_doc, 1, [self.HEADER, ["AC-1", "a"], ["AC-2", "b"]], header_rows=1)

        result = extract_elements(doc, None, "pdf")

        [table] = self._tables(result)
        assert table["content"] == reference.export_to_markdown(doc=reference_doc).strip()
        assert table["content"].splitlines()[0].split("|")[1].strip() == "ID"
        assert len(table["content"].splitlines()) == len(table["row_pages"]) + 1

    @pytest.mark.parametrize(
        "secret",
        ["<b>|ID|</b> `**x**` [y](z)", "ID\x00\x1b[31m\x07\u202e", "S" * 10_240],
        ids=["markup", "control-characters", "10KB"],
    )
    def test_reasons_and_logs_hold_no_document_text(self, secret, caplog, monkeypatch):
        import logging
        import traceback

        from docling_core.types.doc import DocItemLabel as RealLabel
        from docling_core.types.doc import TableItem

        def build() -> object:
            doc = self._pdf(3)
            doc.add_text(label=RealLabel.TEXT, text=secret, prov=self._prov(1, top=10))
            self._table(doc, 1, [[secret, "Text"], ["AC-1", "a"]], header_rows=1)
            self._table(doc, 2, [[secret, "Text"], ["AC-2", "b"]], header_rows=1)
            doc.add_text(label=RealLabel.TEXT, text=secret, prov=self._prov(2, top=10))
            doc.add_text(label=RealLabel.TEXT, text=secret, prov=self._prov(3, top=10))
            return doc

        caplog.set_level(logging.DEBUG)

        result = extract_elements(build(), None, "pdf")

        furniture = "text repeated in the page margin on 3 pages; removed as an unlabelled running header or footer"
        row = {"type": "row", "page": 2, "reason": self._row_reason(1)}
        if len(secret) <= 100:
            margin = [{"type": "furniture", "page": page, "reason": furniture} for page in (1, 2, 3)]
            assert result.metadata["dropped"] == [margin[0], row, margin[1], margin[2]]
        else:
            # Too long to be furniture: the text stays as content and only the header row is reported.
            assert result.metadata["dropped"] == [row]
        assert all(secret not in record.getMessage() for record in caplog.records)

        def failing_copy(self, **kwargs):
            raise ValueError(f"cannot copy {secret}")

        monkeypatch.setattr(TableItem, "model_copy", failing_copy)

        with pytest.raises(RuntimeError) as raised:
            extract_elements(build(), None, "pdf")

        assert secret not in "".join(traceback.format_exception(raised.value))
        assert all(secret not in record.getMessage() for record in caplog.records)

    def test_would_be_tail_that_fails_to_export_is_dropped_and_the_earlier_part_stays_unjoined(self, monkeypatch):
        from docling_core.types.doc import TableItem

        doc = self._pdf()
        first = self._table(doc, 1, [self.HEADER, ["AC-1", "a"]], header_rows=1)
        tail = self._table(doc, 2, [self.HEADER, ["AC-2", "b"]], header_rows=1)
        expected_first = first.export_to_markdown(doc=doc).strip()
        real_export = TableItem.export_to_markdown

        def export_failing_on_tail(self, *args, **kwargs):
            if self.self_ref == tail.self_ref:
                raise ValueError("AC-2 secret")
            return real_export(self, *args, **kwargs)

        monkeypatch.setattr(TableItem, "export_to_markdown", export_failing_on_tail)

        result = extract_elements(doc, None, "pdf")

        [table] = self._tables(result)
        assert (table["content"], table["row_pages"]) == (expected_first, [1, 1])
        assert result.metadata["dropped"] == [
            {"type": "table", "page": 2, "reason": "table could not be exported to Markdown (ValueError)"}
        ]
