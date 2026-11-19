"""Tests for the HTML backend that drops site chrome (navigation, page header/footer).

These run real Docling HTML conversion (no model downloads, no subprocess) because the
behaviour under test is how Docling's parser sees the markup, which mocks cannot show.
"""

from pathlib import Path

import pytest
from docling.datamodel.base_models import InputFormat
from docling.document_converter import DocumentConverter, HTMLFormatOption

from crosscodex_ingestion import converter
from crosscodex_ingestion.extractor import ExtractionResult, extract_elements
from crosscodex_ingestion.html_backend import SiteChromeStrippingHTMLBackend, html_backend_for
from crosscodex_ingestion.html_profile import HtmlProfile, load_html_profiles

FIXTURE = Path(__file__).parent.parent / "fixtures" / "documents" / "ncsc-caf-objective-a.html"

CHROME_HEADINGS = {"Utility navigation", "Main navigation", "Search", "Popular Searches", "Pages"}
PRINCIPLES = [
    "Principle: A1 Governance",
    "Principle: A2 Risk Management",
    "Principle: A3 Asset Management",
    "Principle: A4 Supply Chain",
]
PACKAGED_PROFILES_BACKEND = html_backend_for(load_html_profiles("").profiles)


def _extract(source: Path | str, tmp_path: Path, backend=PACKAGED_PROFILES_BACKEND) -> ExtractionResult:
    """Convert HTML (a file path or a markup string) and return the extraction result."""
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
    return extract_elements(doc, None, "html")


def _convert(source: Path | str, tmp_path: Path, backend=PACKAGED_PROFILES_BACKEND) -> list[dict]:
    """Convert HTML (a file path or a markup string) and return the extracted elements."""
    return _extract(source, tmp_path, backend).elements


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


def test_worker_script_registers_the_profile_backend_for_html():
    assert "html_backend_for(" in converter._WORKER_SCRIPT
    assert "HTMLFormatOption(backend=backend)" in converter._WORKER_SCRIPT


EURLEX_GDPR = FIXTURE.parent / "eurlex-gdpr-32016R0679.html"
EURLEX_NIS2 = FIXTURE.parent / "eurlex-nis2-32022L2555.html"


@pytest.fixture(scope="module")
def gdpr_result(tmp_path_factory) -> ExtractionResult:
    return _extract(EURLEX_GDPR, tmp_path_factory.mktemp("gdpr"))


@pytest.fixture(scope="module")
def gdpr(gdpr_result) -> list[dict]:
    return gdpr_result.elements


@pytest.fixture(scope="module")
def nis2(tmp_path_factory) -> list[dict]:
    return _convert(EURLEX_NIS2, tmp_path_factory.mktemp("nis2"))


def _headings(elements: list[dict]) -> list[tuple[str, int]]:
    return [(e["content"], e["level"]) for e in elements if e["type"] == "heading"]


def _by_content(elements: list[dict], content: str) -> dict:
    return next(e for e in elements if e["content"] == content)


def _starting(elements: list[dict], prefix: str) -> dict:
    return next(e for e in elements if str(e["content"]).startswith(prefix))


class TestEurLexGdprRegression:
    """Regression for issue #21 against a trimmed GDPR (CELEX 32016R0679) page."""

    def test_chapters_sections_and_articles_become_nested_headings(self, gdpr):
        assert _headings(gdpr) == [
            ("CHAPTER I - General provisions", 1),
            ("Article 1 - Subject-matter and objectives", 2),
            ("Article 4 - Definitions", 2),
            ("CHAPTER III - Rights of the data subject", 1),
            ("Section 1 - Transparency and modalities", 2),
            (
                "Article 12 - Transparent information, communication and modalities for the exercise of the "
                "rights of the data subject",
                3,
            ),
            ("Section 2 - Information and access to personal data", 2),
            ("Article 13 - Information to be provided where personal data are collected from the data subject", 3),
            ("CHAPTER VI - Independent supervisory authorities", 1),
            ("Section 1 - Independent status", 2),
            ("Article 53 - General conditions for the members of the supervisory authority", 3),
        ]

    def test_heading_parents_follow_the_act_structure(self, gdpr):
        chapter = _by_content(gdpr, "CHAPTER III - Rights of the data subject")
        section = _by_content(gdpr, "Section 1 - Transparency and modalities")
        article = _starting(gdpr, "Article 12 - ")
        assert chapter["parent_id"] is None
        assert section["parent_id"] == chapter["element_id"]
        assert article["parent_id"] == section["element_id"]

    def test_article_body_attaches_to_its_article(self, gdpr):
        article = _by_content(gdpr, "Article 1 - Subject-matter and objectives")
        body = next(e for e in gdpr if "This Regulation lays down rules" in str(e["content"]))
        assert body["type"] == "paragraph"
        assert body["parent_id"] == article["element_id"]

    def test_act_title_becomes_the_document_title(self, gdpr_result):
        title = gdpr_result.metadata["title"]
        assert title.startswith(
            "REGULATION (EU) 2016/679 OF THE EUROPEAN PARLIAMENT AND OF THE COUNCIL of 27 April 2016"
        )
        assert title.endswith("(Text with EEA relevance)")

    def test_preamble_is_body_content(self, gdpr):
        assert any(str(e["content"]).startswith("Having regard to the Treaty") for e in gdpr)

    def test_no_tables_are_emitted(self, gdpr):
        assert [e["content"] for e in gdpr if e["type"] == "table"] == []

    def test_oj_page_header_is_not_emitted(self, gdpr):
        assert "Official Journal of the European Union" not in _text(gdpr)

    def test_recital_number_stays_with_its_text(self, gdpr):
        recital = _starting(gdpr, "(1) The protection of natural persons")
        assert recital["type"] == "paragraph"
        assert recital["parent_id"] is None

    def test_definition_number_stays_with_its_text(self, gdpr):
        definition = _starting(gdpr, "(1) 'personal data' means")
        assert definition["parent_id"] == _by_content(gdpr, "Article 4 - Definitions")["element_id"]

    def test_nested_point_keeps_its_letter_under_its_article(self, gdpr):
        point = _starting(gdpr, "(c) a complaint has been lodged with that supervisory authority")
        assert point["parent_id"] == _by_content(gdpr, "Article 4 - Definitions")["element_id"]

    def test_dash_indent_keeps_its_dash(self, gdpr):
        indent = _by_content(gdpr, "- their parliament;")
        assert indent["parent_id"] == _starting(gdpr, "Article 53 - ")["element_id"]

    def test_footnote_marker_stays_inside_its_paragraph(self, gdpr):
        assert any(str(e["content"]).endswith("European Economic and Social Committee (1),") for e in gdpr), (
            "footnote reference was split out of its paragraph"
        )
        orphans = [e["content"] for e in gdpr if e["content"] in {"(1)", "(2)", "(3)", ",", "."}]
        assert orphans == []


class TestEurLexNis2Regression:
    """Regression for issue #21 against a trimmed NIS2 (CELEX 32022L2555) page."""

    def test_chapters_articles_and_annexes_become_headings(self, nis2):
        assert _headings(nis2) == [
            ("CHAPTER I - GENERAL PROVISIONS", 1),
            ("Article 1 - Subject matter", 2),
            ("Article 2 - Scope", 2),
            ("ANNEX I - SECTORS OF HIGH CRITICALITY", 1),
            ("ANNEX III - CORRELATION TABLE", 1),
        ]

    def test_only_annex_data_tables_are_tables(self, nis2):
        tables = [e for e in nis2 if e["type"] == "table"]
        parents = {e["element_id"]: e["content"] for e in nis2 if e["type"] == "heading"}
        assert [parents[t["parent_id"]] for t in tables] == [
            "ANNEX I - SECTORS OF HIGH CRITICALITY",
            "ANNEX III - CORRELATION TABLE",
        ]
        assert "Sector" in tables[0]["content"]
        assert "Official Journal of the European Union" not in _text(nis2)

    def test_point_under_article_keeps_its_letter(self, nis2):
        article = _by_content(nis2, "Article 2 - Scope")
        points = [e for e in nis2 if e["parent_id"] == article["element_id"] and str(e["content"]).startswith("(a) ")]
        assert points, "no '(a) ...' point under Article 2"


EURLEX_SKELETON = (
    '<body><table><tr><td><p class="oj-hd-date">1.1.2024</p></td><td><p class="oj-hd-oj">L 1/1</p></td></tr></table>'
    "{body}</body>"
)


class TestEurLexEdgeCases:
    def test_article_without_chapter_is_top_level(self, tmp_path):
        html = EURLEX_SKELETON.format(
            body='<div id="art_1"><p class="oj-ti-art">Article 1</p><p class="oj-normal">Body</p></div>'
        )
        elements = _convert(html, tmp_path)
        assert _headings(elements) == [("Article 1", 1)]
        assert _by_content(elements, "Body")["parent_id"] == elements[0]["element_id"]

    def test_subtitle_sibling_that_is_itself_a_division_does_not_fail(self, tmp_path):
        html = EURLEX_SKELETON.format(
            body='<div id="art_1"><p class="oj-ti-art">A</p>'
            '<div class="eli-title"><p class="oj-ti-art">B</p><p class="oj-normal">Body</p></div></div>'
        )
        elements = _convert(html, tmp_path)
        assert [text for text, _ in _headings(elements)] == ["A - B Body"]

    def test_two_cell_table_without_numbering_label_stays_a_table(self, tmp_path):
        html = EURLEX_SKELETON.format(
            body='<table><tr><td><p class="oj-normal">Name</p></td><td><p class="oj-normal">Value</p></td></tr></table>'
        )
        assert [e["type"] for e in _convert(html, tmp_path)] == ["table"]

    def test_oj_data_table_with_numbering_layout_stays_a_table(self, tmp_path):
        html = EURLEX_SKELETON.format(
            body='<table class="oj-table"><tr><td><p class="oj-tbl-txt">(a)</p></td>'
            '<td><p class="oj-tbl-txt">Electricity</p></td></tr></table>'
        )
        assert [e["type"] for e in _convert(html, tmp_path)] == ["table"]

    def test_numbering_table_outside_eurlex_stays_a_table(self, tmp_path):
        html = "<body><h2>Doc</h2><table><tr><td><p>(5)</p></td><td><p>Recital text</p></td></tr></table></body>"
        types = [e["type"] for e in _convert(html, tmp_path)]
        assert types == ["heading", "table"]


def _test_site_profile(**operations) -> HtmlProfile:
    document = {"profile_version": 1, "id": "test-site", "description": "Test", "detect": "body.test-site"}
    return HtmlProfile("test-site", "0" * 64, document | operations)


def _test_site_page(body: str) -> str:
    return f'<html><body class="test-site">{body}</body></html>'


class TestProfileEngine:
    def test_unwrap_keeps_the_text(self, tmp_path):
        profile = _test_site_profile(unwrap=["span"])
        backend = html_backend_for([profile])
        elements = _convert(_test_site_page("<p>Kept <span>inner</span> text</p>"), tmp_path, backend)
        assert [e["content"] for e in elements] == ["Kept inner text"]
        assert backend.applied is profile

    def test_remove_drops_the_match(self, tmp_path):
        backend = html_backend_for([_test_site_profile(remove=["div.ad"])])
        elements = _convert(_test_site_page('<p>Body</p><div class="ad"><p>Buy now</p></div>'), tmp_path, backend)
        assert [e["content"] for e in elements] == ["Body"]

    def test_title_lines_become_the_document_title(self, tmp_path):
        backend = html_backend_for([_test_site_profile(title={"container": "div.head", "lines": "p.t"})])
        page = _test_site_page('<div class="head"><p class="t">Act</p><p class="t">Name</p></div><p>Body</p>')
        result = _extract(page, tmp_path, backend)
        assert result.metadata["title"] == "Act Name"
        assert [e["content"] for e in result.elements] == ["Body"]

    def test_divisions_become_nested_headings(self, tmp_path):
        profile = _test_site_profile(divisions={"title": "p.sec", "subtitle": "p.sub", "separator": ": "})
        page = _test_site_page(
            '<div><p class="sec">Part 1</p><p class="sub">Intro</p><p>Text</p>'
            '<div><p class="sec">Chapter A</p><p>Inner</p></div></div>'
        )
        elements = _convert(page, tmp_path, html_backend_for([profile]))
        # h2 and h3 in the rewritten page; the extractor numbers headings below the h1 title from 1.
        assert _headings(elements) == [("Part 1: Intro", 1), ("Chapter A", 2)]
        assert _by_content(elements, "Inner")["parent_id"] == _by_content(elements, "Chapter A")["element_id"]

    def test_numbered_table_becomes_a_labelled_paragraph(self, tmp_path):
        profile = _test_site_profile(numbered_tables={"label": r"\d+\."})
        page = _test_site_page("<table><tr><td>1.</td><td><p>Text</p></td></tr></table>")
        elements = _convert(page, tmp_path, html_backend_for([profile]))
        assert [(e["type"], e["content"]) for e in elements] == [("paragraph", "1. Text")]

    def test_title_container_without_lines_inserts_nothing(self, tmp_path):
        backend = html_backend_for([_test_site_profile(title={"container": "div.head", "lines": "p.t"})])
        elements = _convert(_test_site_page('<div class="head"><p>Not a line</p></div>'), tmp_path, backend)
        assert [(e["type"], e["content"]) for e in elements] == [("paragraph", "Not a line")]

    def test_nested_unwrap_and_unbalanced_remove(self, tmp_path):
        profile = _test_site_profile(unwrap=["div"], remove=["section.x"])
        page = _test_site_page('<div>One<div>Two<div>Three</div></div></div><section class="x"><p>Unclosed</p>')
        text = _text(_convert(page, tmp_path, html_backend_for([profile])))
        assert all(word in text for word in ("One", "Two", "Three", "Unclosed"))

    def test_two_matching_profiles_apply_none(self, tmp_path):
        second = HtmlProfile("other-site", "1" * 64, _test_site_profile().document | {"id": "other-site"})
        backend = html_backend_for([_test_site_profile(remove=["p"]), second])
        page = _test_site_page("<p>Body</p>")
        assert _convert(page, tmp_path, backend) == _convert(page, tmp_path, SiteChromeStrippingHTMLBackend)
        assert backend.ambiguous is True
        assert backend.applied is None

    def test_no_matching_profile_leaves_page_unchanged(self, tmp_path):
        backend = html_backend_for([_test_site_profile(remove=["p"])])
        page = "<html><body><p>Body</p></body></html>"
        assert _convert(page, tmp_path, backend) == _convert(page, tmp_path, SiteChromeStrippingHTMLBackend)
        assert backend.applied is None
        assert backend.ambiguous is False

    def test_outcome_resets_between_documents(self, tmp_path):
        profiles = load_html_profiles("").profiles
        backend = html_backend_for(profiles)
        _convert(EURLEX_GDPR, tmp_path, backend)
        assert backend.applied == next(p for p in profiles if p.id == "eur-lex-oj")
        _convert("<html><body><p>Plain</p></body></html>", tmp_path, backend)
        assert backend.applied is None
