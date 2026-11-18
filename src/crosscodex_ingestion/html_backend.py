"""
HTML backend that drops site chrome before Docling reads the page.

Publisher pages wrap their content in navigation menus, site banners and footers. Docling
treats those as ordinary content, so they would become headings and paragraphs of the
extracted document. This backend removes them from the parsed tree first.

Excluded (HTML input only):
  - every <nav> element, wherever it appears, including inside <main> or <article>
    (collection sidebars and previous/next links live there), plus the heading placed
    directly before it as its label (NCSC's "Pages" sidebar has its heading outside the
    <nav>; a heading with nothing under it but a menu is not content);
  - <header> and <footer> elements that are page-level, meaning they have no <main>,
    <article>, <section> or <aside> ancestor (the HTML spec's rule for site-wide banner
    and contentinfo landmarks).

Kept: everything else, including a <header> or <footer> nested in <main>, <article>,
<section> or <aside> (an article's byline or "Reviewed" block is content).

Docling itself files <footer> text under its furniture layer, so a kept <footer> may still
lose its plain paragraphs downstream; its headings survive. That is Docling's behaviour,
not this module's.

Limit: chrome that is not marked up with these elements (for example a <div class="menu">)
is not recognised and is still emitted.
"""

from typing import override

from bs4 import Comment, NavigableString, Tag
from docling.backend.html_backend import HTMLDocumentBackend
from docling.datamodel.document import DoclingDocument

_SECTIONING_ANCESTORS = ("main", "article", "section", "aside")
_HEADINGS = ("h1", "h2", "h3", "h4", "h5", "h6")


class SiteChromeStrippingHTMLBackend(HTMLDocumentBackend):
    """Docling's HTML backend, minus navigation and page-level header/footer."""

    @override
    def convert(self) -> DoclingDocument:
        for tag in self.soup.find_all(["nav", "header", "footer"]) if self.soup else []:
            # A nav inside an already-removed header is detached with it; skip it.
            if tag.decomposed:
                continue
            if tag.name == "nav":
                # Only whitespace or comments may separate the label from its nav: a heading
                # with text of its own after it is a content heading, not a menu label.
                label = tag.previous_sibling
                while isinstance(label, Comment) or (isinstance(label, NavigableString) and not label.strip()):
                    label = label.previous_sibling
                if isinstance(label, Tag) and label.name in _HEADINGS:
                    label.decompose()
                tag.decompose()
            elif tag.find_parent(_SECTIONING_ANCESTORS) is None:
                tag.decompose()
        return super().convert()
