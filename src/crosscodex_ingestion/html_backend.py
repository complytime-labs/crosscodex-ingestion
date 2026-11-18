"""
HTML backend that drops site chrome before Docling reads the page.

Publisher pages wrap their content in navigation menus, site banners and footers. Docling
treats those as ordinary content, so they would become headings and paragraphs of the
extracted document. This backend removes them from the parsed tree first.

Definitions. A role attribute is matched case-insensitively as a whitespace-separated token.
  - nav-like: <nav> or role="navigation".
  - header-like: <header> or role="banner". footer-like: <footer> or role="contentinfo".
  - content landmark: <main> or role="main".
  - sectioning ancestor: <main>, <article>, <section>, <aside> or role="main".

Excluded (HTML input only):
  1. Every nav-like element, wherever it appears (collection sidebars and previous/next links
     live inside <main> and <article>). Its label heading (h1-h6) goes too, but only when the
     heading and the nav are the only two element children of their parent and the parent has
     no other non-whitespace text (comments ignored): NCSC's <div><h2>Pages</h2><nav>..</nav></div>.
     A heading followed by a nav and then more content is content and is kept.
  2. Header-like and footer-like elements, only when the document has a content landmark (the
     page declares where its content is) and the element has no sectioning ancestor. Without a
     landmark a page-level <header> is often front matter (pandoc's title block with author and
     abstract) and a page-level <footer> can hold "References"; both are kept.

Balance guard. Docling parses with "html.parser", where an unclosed <nav> or <header> swallows the
rest of the body, so removing it would silently delete the document. An element is removed only if
the raw source has as many start tags as end tags for its tag name (counted once per name,
case-insensitively, on the bytes; this includes <div> for role-based matches). Otherwise it is
kept. Tag-like text inside scripts or comments can unbalance the counts; that only keeps chrome,
which is the safe direction. Likewise, a candidate that contains the content landmark is never
removed (a <header> or role="navigation" wrapper around <main> is misused markup). Nothing is
logged: the conversion subprocess has no logging setup.

Kept: everything else. Stock Docling keeps <footer> paragraphs, so a kept footer is emitted whole.

Limits: chrome not marked up with these elements or roles (for example <div class="menu">) is
still emitted. HTMLBackendOptions.render_page re-parses the page inside super().convert(), which
would bypass this stripping; this service does not enable it.
"""

import re
from io import BytesIO
from pathlib import Path
from typing import override

from bs4 import Comment, NavigableString, Tag
from docling.backend.html_backend import HTMLDocumentBackend
from docling.datamodel.document import DoclingDocument

_SECTIONING_TAGS = ("main", "article", "section", "aside")
_HEADINGS = ("h1", "h2", "h3", "h4", "h5", "h6")


def _has_role(tag: Tag, role: str) -> bool:
    value = tag.get("role")
    if isinstance(value, list):
        value = " ".join(value)
    return isinstance(value, str) and role in value.lower().split()


def _is_nav(tag: Tag) -> bool:
    return tag.name == "nav" or _has_role(tag, "navigation")


def _is_content_landmark(tag: Tag) -> bool:
    return tag.name == "main" or _has_role(tag, "main")


def _is_sectioning(tag: Tag) -> bool:
    return tag.name in _SECTIONING_TAGS or _has_role(tag, "main")


def _is_chrome_candidate(tag: Tag) -> bool:
    return _is_nav(tag) or tag.name in ("header", "footer") or _has_role(tag, "banner") or _has_role(tag, "contentinfo")


def _is_label_heading_of(heading: Tag, nav: Tag) -> bool:
    """True when heading and nav are the only element children of their parent, with no other text."""
    parent = nav.parent
    if parent is None or heading.parent is not parent or heading.name not in _HEADINGS:
        return False
    children = [child for child in parent.children if isinstance(child, Tag)]
    if children != [heading, nav]:
        return False
    return not any(
        isinstance(child, NavigableString) and not isinstance(child, Comment) and child.strip()
        for child in parent.children
    )


class SiteChromeStrippingHTMLBackend(HTMLDocumentBackend):
    """Docling's HTML backend, minus navigation and page-level header/footer."""

    def _raw_source(self) -> bytes:
        """The page's bytes, read the way Docling's own __init__ reads them."""
        if isinstance(self.path_or_stream, BytesIO):
            return self.path_or_stream.getvalue()
        return Path(str(self.path_or_stream)).read_bytes()

    @override
    def convert(self) -> DoclingDocument:
        if self.soup is None:
            return super().convert()
        raw = self._raw_source()
        balanced: dict[str, bool] = {}

        def is_balanced(name: str) -> bool:
            if name not in balanced:
                escaped = re.escape(name.encode())
                starts = len(re.findall(rb"<" + escaped + rb"[\s/>]", raw, re.IGNORECASE))
                ends = len(re.findall(rb"</" + escaped + rb"\s*>", raw, re.IGNORECASE))
                balanced[name] = starts == ends
            return balanced[name]

        has_landmark = self.soup.find(_is_content_landmark) is not None
        for tag in self.soup.find_all(_is_chrome_candidate):
            # A candidate inside an already-removed element is detached with it; skip it.
            if tag.decomposed or not is_balanced(tag.name):
                continue
            # Chrome never contains the page's content; one that does is misused markup.
            if tag.find(_is_content_landmark) is not None:
                continue
            if _is_nav(tag):
                label = tag.find_previous_sibling(_HEADINGS)
                if label is not None and _is_label_heading_of(label, tag):
                    label.decompose()
                tag.decompose()
            elif has_landmark and tag.find_parent(_is_sectioning) is None:
                tag.decompose()
        return super().convert()
