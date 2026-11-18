"""Tests for the HTML backend that drops site chrome (navigation, page header/footer).

These run real Docling HTML conversion (no model downloads, no subprocess) because the
behaviour under test is how Docling's parser sees the markup, which mocks cannot show.
"""

from pathlib import Path

import pytest
from docling.datamodel.base_models import InputFormat
from docling.document_converter import DocumentConverter, HTMLFormatOption

from crosscodex_ingestion import converter
from crosscodex_ingestion.extractor import extract_elements
from crosscodex_ingestion.html_backend import SiteChromeStrippingHTMLBackend

FIXTURE = Path(__file__).parent.parent / "fixtures" / "documents" / "ncsc-caf-objective-a.html"

CHROME_HEADINGS = {"Utility navigation", "Main navigation", "Search", "Popular Searches", "Pages"}
PRINCIPLES = [
    "Principle: A1 Governance",
    "Principle: A2 Risk Management",
    "Principle: A3 Asset Management",
    "Principle: A4 Supply Chain",
]


def _convert(source: Path | str, tmp_path: Path, backend=SiteChromeStrippingHTMLBackend) -> list[dict]:
    """Convert HTML (a file path or a markup string) and return the extracted elements."""
    if isinstance(source, str):
        path = tmp_path / "page.html"
        path.write_text(source, encoding="utf-8")
    else:
        path = source
    conv = DocumentConverter(
        allowed_formats=[InputFormat.HTML],
        format_options={InputFormat.HTML: HTMLFormatOption(backend=backend)},
    )
    doc = conv.convert(path).document
    return extract_elements(doc, None, "html").elements


def _text(elements: list[dict]) -> str:
    return "\n".join(str(e["content"]) for e in elements)


class TestNcscRegression:
    def test_unfiltered_docling_emits_the_chrome(self, tmp_path):
        """Guards the fixture: without the backend the chrome headings are present."""
        from docling.backend.html_backend import HTMLDocumentBackend

        headings = {e["content"] for e in _convert(FIXTURE, tmp_path, HTMLDocumentBackend) if e["type"] == "heading"}
        assert headings >= CHROME_HEADINGS

    def test_no_navigation_headings(self, tmp_path):
        elements = _convert(FIXTURE, tmp_path)
        headings = {e["content"] for e in elements if e["type"] == "heading"}
        assert not headings & CHROME_HEADINGS
        assert "About this website" not in headings

    def test_no_navigation_text(self, tmp_path):
        text = _text(_convert(FIXTURE, tmp_path))
        for chrome in ("Open child page navigation menu", "icons/chevron", "Accessibility statement", "About NCSC"):
            assert chrome not in text

    def test_objective_and_principle_headings_remain(self, tmp_path):
        headings = [e["content"] for e in _convert(FIXTURE, tmp_path) if e["type"] == "heading"]
        assert "CAF Objective A - Managing Security Risk" in headings
        for principle in PRINCIPLES:
            assert principle in headings

    def test_article_footer_inside_main_is_kept(self, tmp_path):
        text = _text(_convert(FIXTURE, tmp_path))
        assert "Reviewed" in text
        assert "30 October 2026" in text


class TestRemovalRule:
    def test_nav_inside_article_is_removed(self, tmp_path):
        html = "<body><main><article><h2>Title</h2><p>Body text</p><nav><h3>Contents</h3></nav></article></main></body>"
        text = _text(_convert(html, tmp_path))
        assert "Contents" not in text
        assert "Title" in text
        assert "Body text" in text

    def test_page_level_header_and_footer_are_removed(self, tmp_path):
        html = (
            "<body><header><h1>Site banner</h1></header>"
            "<main><h2>Policy</h2><p>Body text</p></main>"
            "<footer><p>Copyright notice</p></footer></body>"
        )
        text = _text(_convert(html, tmp_path))
        assert "Site banner" not in text
        assert "Copyright notice" not in text
        assert "Policy" in text
        assert "Body text" in text

    @pytest.mark.parametrize("ancestor", ["article", "section", "main", "aside"])
    def test_header_and_footer_inside_sectioning_element_are_kept(self, ancestor, tmp_path):
        html = (
            f"<body><{ancestor}><header><h2>Section heading</h2></header>"
            f"<p>Body text</p><footer><h3>Reviewed by Legal</h3></footer></{ancestor}></body>"
        )
        text = _text(_convert(html, tmp_path))
        assert "Section heading" in text
        assert "Reviewed by Legal" in text

    def test_heading_not_directly_before_a_nav_is_kept(self, tmp_path):
        html = "<body><main><h2>Policy</h2><p>Body text</p><nav><p>Menu</p></nav></main></body>"
        text = _text(_convert(html, tmp_path))
        assert "Policy" in text
        assert "Menu" not in text

    def test_heading_separated_from_nav_by_bare_text_is_kept(self, tmp_path):
        html = "<body><main><h2>Policy</h2>Body text<nav><p>Menu</p></nav></main></body>"
        text = _text(_convert(html, tmp_path))
        assert "Policy" in text
        assert "Menu" not in text

    def test_nav_inside_removed_header_does_not_fail(self, tmp_path):
        html = "<body><header><nav><p>Menu</p></nav></header><main><p>Body text</p></main></body>"
        text = _text(_convert(html, tmp_path))
        assert "Menu" not in text
        assert "Body text" in text


def test_worker_script_registers_the_backend_for_html():
    assert "InputFormat.HTML: HTMLFormatOption(backend=SiteChromeStrippingHTMLBackend)" in converter._WORKER_SCRIPT
