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

Publisher profiles. After chrome stripping, a page matched by exactly one HTML profile is rewritten by
that profile's declarative rules; see docs/html-profiles.md.
"""

import re
from collections.abc import Callable, Sequence
from io import BytesIO
from pathlib import Path
from typing import Any, override

from bs4 import BeautifulSoup, Comment, NavigableString, Tag
from docling.backend.html_backend import HTMLDocumentBackend
from docling.datamodel.document import DoclingDocument

from crosscodex_ingestion.html_profile import HtmlProfile

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


def _division_title(div: Tag, divisions: dict[str, Any]) -> Tag | None:
    """The first element child that makes div a division under the profile's divisions rule, or None."""
    exclude = divisions.get("exclude")
    if exclude is not None and div.css.match(exclude):
        return None
    first = div.find(True, recursive=False)
    if not isinstance(first, Tag) or not first.css.match(divisions["title"]):
        return None
    return first


def _apply_profile(soup: BeautifulSoup, profile: HtmlProfile, is_balanced: Callable[[str], bool]) -> None:
    """Run the profile's operations in the fixed order described in docs/html-profiles.md."""
    document = profile.document
    for selector in document.get("unwrap", []):
        for tag in soup.select(selector):
            tag.unwrap()

    for selector in document.get("remove", []):
        for tag in soup.select(selector):
            if not tag.decomposed and is_balanced(tag.name):
                tag.decompose()

    title_rule = document.get("title")
    if title_rule is not None:
        container = soup.select_one(title_rule["container"])
        if container is not None:
            lines = container.select(title_rule["lines"])
            if lines:
                heading = soup.new_tag("h1")
                heading.string = " ".join(" ".join(line.get_text(" ") for line in lines).split())
                lines[0].insert_before(heading)
                for line in lines:
                    line.decompose()

    division_rule = document.get("divisions")
    if division_rule is not None:
        subtitle = division_rule.get("subtitle")
        separator = division_rule.get("separator", " - ")
        divisions = [
            (div, title) for div in soup.find_all("div") if (title := _division_title(div, division_rule)) is not None
        ]
        division_ids = {id(div) for div, _ in divisions}
        for div, title in divisions:
            if title.decomposed:  # consumed as the subtitle of an enclosing division
                continue
            depth = 1 + sum(1 for ancestor in div.parents if id(ancestor) in division_ids)
            parts = [title.get_text(" ")]
            sibling = title.find_next_sibling(True)
            while subtitle is not None and isinstance(sibling, Tag) and sibling.css.match(subtitle):
                parts.append(sibling.get_text(" "))
                following = sibling.find_next_sibling(True)
                sibling.decompose()
                sibling = following
            heading = soup.new_tag(f"h{min(depth + 1, 6)}")
            heading.string = separator.join(text for text in (" ".join(part.split()) for part in parts) if text)
            title.replace_with(heading)

    table_rule = document.get("numbered_tables")
    if table_rule is not None:
        exclude = table_rule.get("exclude")
        for table in soup.find_all("table"):
            if table.decomposed or (exclude is not None and table.css.match(exclude)):
                continue
            rows = [row for row in table.find_all("tr") if row.find_parent("table") is table]
            if len(rows) != 1:
                continue
            cells = rows[0].find_all(["td", "th"], recursive=False)
            if len(cells) != 2:
                continue
            label = " ".join(cells[0].get_text(" ").split())
            body = cells[1]
            first = body.find(True, recursive=False)
            if not re.fullmatch(table_rule["label"], label) or not isinstance(first, Tag) or first.name != "p":
                continue
            first.insert(0, f"{label} ")
            for child in list(body.contents):
                table.insert_before(child.extract())
            table.decompose()


class SiteChromeStrippingHTMLBackend(HTMLDocumentBackend):
    """Docling's HTML backend, minus navigation and page-level header/footer."""

    profiles: tuple[HtmlProfile, ...] = ()
    # Outcome of the last convert(); class attributes because Docling instantiates the backend itself.
    applied: HtmlProfile | None = None
    ambiguous: bool = False

    def _raw_source(self) -> bytes:
        """The page's bytes, read the way Docling's own __init__ reads them."""
        if isinstance(self.path_or_stream, BytesIO):
            return self.path_or_stream.getvalue()
        return Path(str(self.path_or_stream)).read_bytes()

    @override
    def convert(self) -> DoclingDocument:
        type(self).applied = None
        type(self).ambiguous = False
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
        soup = self.soup
        matches = [profile for profile in self.profiles if soup.select_one(profile.document["detect"]) is not None]
        if len(matches) == 1:
            _apply_profile(soup, matches[0], is_balanced)
            type(self).applied = matches[0]
        elif matches:
            type(self).ambiguous = True
        return super().convert()


def html_backend_for(profiles: Sequence[HtmlProfile]) -> type[SiteChromeStrippingHTMLBackend]:
    """A SiteChromeStrippingHTMLBackend subclass bound to profiles, with its own outcome attributes."""
    return type(
        "ProfiledHTMLBackend",
        (SiteChromeStrippingHTMLBackend,),
        {"profiles": tuple(profiles), "applied": None, "ambiguous": False},
    )
