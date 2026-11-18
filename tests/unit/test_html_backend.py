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


class TestNavLabelHeading:
    def test_ncsc_style_label_heading_is_removed(self, tmp_path):
        html = "<body><main><div><h2>Pages</h2> <nav><a>c1</a></nav></div><p>Body text</p></main></body>"
        text = _text(_convert(html, tmp_path))
        assert "Pages" not in text
        assert "c1" not in text
        assert "Body text" in text

    def test_heading_followed_by_nav_and_more_content_is_kept(self, tmp_path):
        html = "<body><main><h2>Related controls</h2><nav><a>c1</a></nav><p>para</p></main></body>"
        text = _text(_convert(html, tmp_path))
        assert "Related controls" in text
        assert "c1" not in text
        assert "para" in text

    def test_annex_heading_before_nav_in_article_is_kept(self, tmp_path):
        html = (
            "<body><main><article><h1>Std</h1><h2>Annex A (informative)</h2>"
            "<nav><a>c1</a></nav></article></main></body>"
        )
        text = _text(_convert(html, tmp_path))
        assert "Annex A (informative)" in text
        assert "c1" not in text

    def test_label_heading_kept_when_parent_has_bare_text(self, tmp_path):
        html = "<body><main><div><h2>Pages</h2>Some words<nav><a>c1</a></nav></div></main></body>"
        text = _text(_convert(html, tmp_path))
        assert "Pages" in text
        assert "c1" not in text

    def test_comments_do_not_block_label_removal(self, tmp_path):
        html = "<body><main><div><h2>Pages</h2><!-- x --><nav><a>c1</a></nav></div><p>Body</p></main></body>"
        text = _text(_convert(html, tmp_path))
        assert "Pages" not in text
        assert "c1" not in text


class TestPageLevelHeaderFooterNeedsContentLandmark:
    def test_pandoc_title_block_without_main_is_kept(self, tmp_path):
        html = (
            '<body><header id="title-block-header"><h1>Title</h1><p class="author">Ed</p>'
            '<p class="abstract">Abstract text</p></header><h2>Intro</h2><p>Body</p></body>'
        )
        text = _text(_convert(html, tmp_path))
        assert "Ed" in text
        assert "Abstract text" in text

    def test_footer_references_without_main_is_kept(self, tmp_path):
        html = "<body><h1>Doc</h1><p>Body</p><footer><h2>References</h2><p>Ref one</p></footer></body>"
        text = _text(_convert(html, tmp_path))
        assert "References" in text
        assert "Ref one" in text

    def test_role_main_counts_as_content_landmark(self, tmp_path):
        html = '<body><header><h1>Banner</h1></header><div role="main"><p>Body</p></div></body>'
        text = _text(_convert(html, tmp_path))
        assert "Banner" not in text
        assert "Body" in text

    def test_footer_inside_role_main_is_kept(self, tmp_path):
        html = '<body><div role="main"><p>Body</p><footer><h3>Reviewed by Legal</h3></footer></div></body>'
        assert "Reviewed by Legal" in _text(_convert(html, tmp_path))


class TestAria:
    def test_aria_landmarks(self, tmp_path):
        html = (
            '<body><div role="banner"><h1>Banner text</h1></div>'
            '<div role="navigation"><p>Nav text</p></div>'
            '<div role="main"><h2>Main text</h2></div>'
            '<div role="contentinfo"><p>Footer text</p></div></body>'
        )
        text = _text(_convert(html, tmp_path))
        assert "Main text" in text
        for chrome in ("Banner text", "Nav text", "Footer text"):
            assert chrome not in text

    def test_role_is_case_insensitive_token(self, tmp_path):
        html = '<body><main><p>Body</p><div ROLE="Navigation"><p>Nav text</p></div></main></body>'
        text = _text(_convert(html, tmp_path))
        assert "Nav text" not in text
        assert "Body" in text

    def test_role_token_among_several(self, tmp_path):
        html = '<body><main><p>Body</p><div role="  foo NAVIGATION "><p>Nav text</p></div></main></body>'
        assert "Nav text" not in _text(_convert(html, tmp_path))

    def test_unbalanced_div_role_is_kept(self, tmp_path):
        html = '<body><main><p>Body</p><div role="navigation"><p>Swallowed text</p></main></body>'
        assert "Swallowed text" in _text(_convert(html, tmp_path))


class TestBalanceGuard:
    def test_unclosed_nav_keeps_rest_of_document(self, tmp_path):
        html = "<h1>T</h1><nav><ul><li><a>menu</a></li></ul><h2>Real</h2><p>real text</p></body>"
        text = _text(_convert(html, tmp_path))
        assert "real text" in text
        assert "Real" in text

    def test_unclosed_header_with_main_keeps_content(self, tmp_path):
        html = "<body><header><h1>Banner</h1><main><h2>Policy</h2><p>Body text</p></main></body>"
        text = _text(_convert(html, tmp_path))
        assert "Body text" in text
        assert "Policy" in text

    @pytest.mark.parametrize("wrapper", ["header", "div role='navigation'", "div role='banner'"])
    def test_chrome_wrapping_the_main_landmark_is_kept(self, wrapper, tmp_path):
        tag = wrapper.split()[0]
        html = f"<body><{wrapper}><main><h2>Policy</h2><p>Body text</p></main></{tag}></body>"
        text = _text(_convert(html, tmp_path))
        assert "Body text" in text

    def test_one_unclosed_nav_keeps_every_nav(self, tmp_path):
        """Counts are per tag name, so one stray <nav> protects every <nav> on the page."""
        html = "<body><main><nav><a>one-menu</a></nav><nav><a>two-menu</a><p>Body</p></main></body>"
        text = _text(_convert(html, tmp_path))
        assert "Body" in text

    def test_longer_tag_names_with_nav_prefix_are_not_counted(self, tmp_path):
        html = "<body><main><nav-x><p>Custom</p><nav><a>menu</a></nav><p>Body</p></main></body>"
        text = _text(_convert(html, tmp_path))
        assert "menu" not in text
        assert "Body" in text


def test_worker_script_registers_the_backend_for_html():
    assert "InputFormat.HTML: HTMLFormatOption(backend=SiteChromeStrippingHTMLBackend)" in converter._WORKER_SCRIPT
