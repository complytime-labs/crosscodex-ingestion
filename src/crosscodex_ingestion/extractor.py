"""
Document extractor — converts Docling output to structured elements.

Extraction strategy (two-stage):

  Primary — _parse_docling_document():
    Iterates the DoclingDocument object using typed DocItemLabel elements.
    PAGE_HEADER/PAGE_FOOTER items are skipped. SECTION_HEADER items drive
    hierarchy via level (1-100). No regex or heuristics required for
    well-structured documents.

  Fallback — _parse_markdown() cascade (runs when primary finds no elements):
    Used for .txt files, scanned PDFs, and documents where Docling emits no
    SECTION_HEADER elements. Tiers in order:
      1. Markdown headings: ## markers
      2. Repeating-pattern detection: scan first tokens for recurring prefix
      3. Paragraph splitting: final fallback (blank-line split)
"""

import re
import time
from dataclasses import dataclass
from typing import Any

# Markdown heading pattern
_HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.+)$")

# Candidate section-numbering patterns for auto-detection (Tier 3).
# Each tuple: (regex_str, label). The regex must have exactly one capture group
# that extracts the section identifier.
_CANDIDATE_PATTERNS = [
    (r"^([A-Z]\d+)\.", "letter-number-dot"),  # B26. A5. C12.
    (r"^(\d+\.\d+)", "dotted-number"),  # 4.1  10.3
    (r"^((?:Art|Article)\.\s*\d+)", "article"),  # Art. 5  Article. 12
    (r"^((?:Section|SECTION)\s+\d+)", "section"),  # Section 3
    (r"^((?:Rule|RULE)\s+[\d\-]+)", "rule"),  # Rule 3-101
    (r"^(§\s*\d+[\.\d]*)", "section-sign"),  # §4.1  §302
    (r"^(\([a-z]+\))", "paren-letter"),  # (a) (b) (iv)
    (r"^(\(\d+\))", "paren-number"),  # (1) (2)
    (r"^([ivxIVX]+)\.\s+(?=\S)", "roman-lower"),  # i. ii. iii.
    (r"^([a-z])\.\s+(?=\S)", "letter-lower"),  # a. b. c.
    (r"^([A-Z])\.\s+(?=\S)", "letter-upper"),  # A. B. C.
    (r"^(\d{1,4})\.\s+(?=\S)", "plain-number"),  # 24. Title  1. Item
]

# Minimum distinct section IDs a candidate pattern must match to be accepted.
_MIN_PATTERN_MATCHES = 3

# Maximum allowed heading repeats before filtering as spurious (page artifacts)
_MAX_HEADING_REPEATS = 3


@dataclass
class ExtractionResult:
    """Result of document extraction."""

    elements: list[dict]
    metadata: dict


class DocumentExtractor:
    """Extracts structured elements from Docling documents or markdown text."""

    def __init__(self) -> None:
        self.elements: list[dict] = []
        self.discarded_content: list[str] = []
        self._element_counter = 0

    def _next_element_id(self) -> str:
        """Generate the next sequential element ID."""
        self._element_counter += 1
        return f"e-{self._element_counter:03d}"

    def _clean_text(self, text: str) -> str:
        """Clean text: remove HTML comments, collapse whitespace."""
        # Strip HTML comments
        text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
        # Remove hyphenation across line breaks
        text = re.sub(r"(\w+)-\n(\w+)", r"\1\2", text)
        # Collapse multiple spaces/tabs to single space (preserve newlines)
        text = re.sub(r"[ \t]+", " ", text)
        return text

    def _parse_docling_document(self, doc: Any) -> None:
        """Primary extraction: process DoclingDocument using typed elements.

        Uses DocItemLabel types to classify content. PAGE_HEADER and PAGE_FOOTER
        elements are skipped. SectionHeaderItem.level drives hierarchy.
        """
        try:
            from docling_core.types.doc import DocItemLabel
        except ImportError:
            # Docling not installed — this is fine for unit tests
            # Try to use the mock instead
            DocItemLabel = type(  # type: ignore[misc,assignment]
                "DocItemLabel",
                (),
                {
                    "SECTION_HEADER": "section_header",
                    "TEXT": "text",
                    "PARAGRAPH": "paragraph",
                    "LIST_ITEM": "list_item",
                    "TABLE": "table",
                    "PAGE_HEADER": "page_header",
                    "PAGE_FOOTER": "page_footer",
                    "PICTURE": "picture",
                    "CHART": "chart",
                    "FORMULA": "formula",
                    "TITLE": "title",
                    "DOCUMENT_INDEX": "document_index",
                    "EMPTY_VALUE": "empty_value",
                    "GRADING_SCALE": "grading_scale",
                    "HANDWRITTEN_TEXT": "handwritten_text",
                    "FORM": "form",
                    "KEY_VALUE_REGION": "key_value_region",
                    "CHECKBOX_SELECTED": "checkbox_selected",
                    "CHECKBOX_UNSELECTED": "checkbox_unselected",
                },
            )

        # Labels explicitly skipped
        SKIP_LABELS = {
            DocItemLabel.PAGE_HEADER,
            DocItemLabel.PAGE_FOOTER,
            DocItemLabel.PICTURE,
            DocItemLabel.CHART,
            DocItemLabel.FORMULA,
            DocItemLabel.TITLE,
            DocItemLabel.DOCUMENT_INDEX,
            DocItemLabel.EMPTY_VALUE,
            DocItemLabel.GRADING_SCALE,
            DocItemLabel.HANDWRITTEN_TEXT,
            DocItemLabel.FORM,
            DocItemLabel.KEY_VALUE_REGION,
            DocItemLabel.CHECKBOX_SELECTED,
            DocItemLabel.CHECKBOX_UNSELECTED,
        }
        HEADING_LABELS = {DocItemLabel.SECTION_HEADER}
        TEXT_LABELS = {
            DocItemLabel.TEXT,
            DocItemLabel.PARAGRAPH,
            DocItemLabel.LIST_ITEM,
        }

        # Pass 1: count SECTION_HEADER title occurrences
        heading_title_counts: dict = {}
        for item, _depth in doc.iterate_items():
            if getattr(item, "label", None) in HEADING_LABELS:
                t = getattr(item, "text", "").strip()
                heading_title_counts[t] = heading_title_counts.get(t, 0) + 1
        spurious_titles = {t for t, c in heading_title_counts.items() if c >= _MAX_HEADING_REPEATS}

        # Pass 2: build elements from typed items
        current_heading_id = None  # The current heading element ID (for text content parent)
        heading_stack: list[tuple] = []  # (level, element_id)

        for item, _depth in doc.iterate_items():
            label = getattr(item, "label", None)
            if label is None or label in SKIP_LABELS:
                continue

            if label in HEADING_LABELS:
                title = getattr(item, "text", "").strip()
                if title in spurious_titles:
                    continue  # page artifact

                level = getattr(item, "level", 1)
                element_id = self._next_element_id()

                # Update heading stack
                while heading_stack and heading_stack[-1][0] >= level:
                    heading_stack.pop()
                parent_id = heading_stack[-1][1] if heading_stack else None
                heading_stack.append((level, element_id))

                # Track current heading for text content
                current_heading_id = element_id

                self.elements.append(
                    {
                        "element_id": element_id,
                        "type": "heading",
                        "content": title,
                        "page": None,
                        "level": level,
                        "parent_id": parent_id,
                        "style": {},
                    }
                )

            elif label in TEXT_LABELS:
                text = getattr(item, "text", "").strip()
                if text:
                    element_id = self._next_element_id()
                    elem_type = "list_item" if label == DocItemLabel.LIST_ITEM else "paragraph"
                    self.elements.append(
                        {
                            "element_id": element_id,
                            "type": elem_type,
                            "content": text,
                            "page": None,
                            "level": None,
                            "parent_id": current_heading_id,
                            "style": {},
                        }
                    )

            elif label == DocItemLabel.TABLE:
                # Extract table as element
                try:
                    table_md = item.export_to_markdown(doc=doc).strip()
                    if table_md:
                        element_id = self._next_element_id()
                        self.elements.append(
                            {
                                "element_id": element_id,
                                "type": "table",
                                "content": table_md,
                                "page": None,
                                "level": None,
                                "parent_id": current_heading_id,
                                "style": {},
                            }
                        )
                except Exception:  # noqa: S110
                    # Silently skip unparseable tables
                    pass

    def _parse_markdown(self, text: str) -> None:
        """Fallback extraction: parse markdown text using detection cascade.

        1. Markdown headings (## markers)
        2. Repeating-pattern auto-detection
        3. Paragraph splitting fallback
        """
        # Tier 1: markdown headings
        self._parse_headings(text)

        # Strip markdown heading markers for pattern detection
        stripped = re.sub(r"^#+\s*", "", text, flags=re.MULTILINE)

        # Tier 2: repeating-pattern auto-detection
        if not self.elements and self.discarded_content:
            detected = self._detect_section_pattern(stripped)
            if detected:
                self.discarded_content.clear()
                self._parse_with_pattern(stripped, detected)

        # Tier 3: paragraph splitting fallback
        if not self.elements:
            self.discarded_content.clear()
            self._parse_paragraphs(stripped)

    def _parse_headings(self, text: str) -> None:
        """Parse markdown heading markers (# H1, ## H2, etc.).

        Maintains heading stack for parent-child relationships. Headings that
        appear >= _MAX_HEADING_REPEATS times are filtered as page artifacts.
        """
        lines = text.split("\n")

        # Pre-scan: identify spurious heading titles
        heading_title_counts: dict = {}
        for line in lines:
            m = _HEADING_PATTERN.match(line.strip())
            if m:
                t = m.group(2).strip()
                heading_title_counts[t] = heading_title_counts.get(t, 0) + 1
        spurious_titles = {t for t, c in heading_title_counts.items() if c >= _MAX_HEADING_REPEATS}

        current_heading_id = None  # Current heading for text content parent
        heading_stack: list[tuple[int, str]] = []  # (level, element_id)

        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue

            match = _HEADING_PATTERN.match(stripped)
            if match:
                title = match.group(2).strip()
                if title in spurious_titles:
                    continue  # page artifact

                level = len(match.group(1))  # number of # chars
                element_id = self._next_element_id()

                # Update heading stack
                while heading_stack and heading_stack[-1][0] >= level:
                    heading_stack.pop()
                parent_id = heading_stack[-1][1] if heading_stack else None
                heading_stack.append((level, element_id))

                # Track current heading for text content
                current_heading_id = element_id

                self.elements.append(
                    {
                        "element_id": element_id,
                        "type": "heading",
                        "content": title,
                        "page": None,
                        "level": level,
                        "parent_id": parent_id,
                        "style": {},
                    }
                )
            else:
                if current_heading_id:
                    # Content under current heading
                    element_id = self._next_element_id()
                    self.elements.append(
                        {
                            "element_id": element_id,
                            "type": "paragraph",
                            "content": stripped,
                            "page": None,
                            "level": None,
                            "parent_id": current_heading_id,
                            "style": {},
                        }
                    )
                else:
                    self.discarded_content.append(f"PREAMBLE: {stripped}")

    def _detect_section_pattern(self, text: str) -> re.Pattern | None:
        """Auto-detect repeating section numbering pattern.

        Returns compiled regex with one capture group, or None.
        """
        first_lines = self._extract_first_lines(text)

        for pattern_str, _label in _CANDIDATE_PATTERNS:
            pat = re.compile(pattern_str, re.IGNORECASE)
            if len(self._count_pattern_matches(pat, first_lines)) >= _MIN_PATTERN_MATCHES:
                return pat

        return None

    def _parse_with_pattern(self, text: str, pattern: re.Pattern) -> None:  # type: ignore[type-arg]
        """Parse using a detected regex pattern.

        Content after the section identifier on the matched line is preserved.
        """
        lines = text.split("\n")
        current_id = None
        current_title = None
        current_text: list[str] = []

        for line in lines:
            line = line.strip()
            if not line:
                continue

            match = pattern.match(line)
            if match:
                # Save previous section
                if current_id:
                    content = "\n".join(current_text)
                    element_id = self._next_element_id()
                    self.elements.append(
                        {
                            "element_id": element_id,
                            "type": "heading",
                            "content": current_title or current_id,
                            "page": None,
                            "level": 1,
                            "parent_id": None,
                            "style": {},
                        }
                    )
                    if content:
                        para_id = self._next_element_id()
                        self.elements.append(
                            {
                                "element_id": para_id,
                                "type": "paragraph",
                                "content": content,
                                "page": None,
                                "level": None,
                                "parent_id": element_id,
                                "style": {},
                            }
                        )

                current_id = match.group(1).strip()
                current_text = []
                # Preserve text after section identifier
                remainder = line[match.end() :].strip()
                current_title = remainder if remainder else current_id
                if remainder:
                    current_text.append(remainder)
            else:
                if current_id:
                    current_text.append(line)
                else:
                    self.discarded_content.append(f"PREAMBLE: {line}")

        # Save final section
        if current_id:
            content = "\n".join(current_text)
            element_id = self._next_element_id()
            self.elements.append(
                {
                    "element_id": element_id,
                    "type": "heading",
                    "content": current_title or current_id,
                    "page": None,
                    "level": 1,
                    "parent_id": None,
                    "style": {},
                }
            )
            if content:
                para_id = self._next_element_id()
                self.elements.append(
                    {
                        "element_id": para_id,
                        "type": "paragraph",
                        "content": content,
                        "page": None,
                        "level": None,
                        "parent_id": element_id,
                        "style": {},
                    }
                )

    def _parse_paragraphs(self, text: str) -> None:
        """Fallback: split on blank lines, each paragraph becomes an element."""
        raw = re.split(r"\n\s*\n", text)

        # Merge list continuations
        merged: list[str] = []
        for chunk in raw:
            chunk = chunk.strip()
            if not chunk:
                continue
            if merged and re.search(r"[\-:;]\s*$", merged[-1]) and re.match(r"\s*[-*+]|\s*\d+[.)]\s", chunk):
                merged[-1] = merged[-1] + "\n" + chunk
            else:
                merged.append(chunk)

        for para in merged:
            if len(para.split()) < 5:
                continue
            element_id = self._next_element_id()
            self.elements.append(
                {
                    "element_id": element_id,
                    "type": "paragraph",
                    "content": para,
                    "page": None,
                    "level": None,
                    "parent_id": None,
                    "style": {},
                }
            )

    @staticmethod
    def _extract_first_lines(text: str) -> list:
        """Split on blank lines and return each paragraph's first line."""
        first_lines = []
        for p in re.split(r"\n\s*\n", text):
            p = p.strip()
            if p:
                first_lines.append(p.split("\n")[0].strip())
        return first_lines

    @staticmethod
    def _count_pattern_matches(pat: re.Pattern, first_lines: list) -> set:
        """Return set of distinct captured IDs from matching pattern."""
        matched_ids = set()
        for line in first_lines:
            m = pat.match(line)
            if m:
                matched_ids.add(m.group(1))
        return matched_ids


def extract_elements(
    doc: Any,
    markdown: str,
    format_hint: str,
) -> ExtractionResult:
    """Extract structured elements from Docling document or markdown text.

    Parameters
    ----------
    doc : DoclingDocument or None
        Docling document object (if available).
    markdown : str
        Markdown text (fallback if doc is None).
    format_hint : str
        Format hint (e.g., 'pdf', 'html', 'txt').

    Returns
    -------
    ExtractionResult
        Elements and metadata.
    """
    t_start = time.time()
    extractor = DocumentExtractor()

    # Clean the markdown text
    cleaned_text = extractor._clean_text(markdown)

    # Primary: typed Docling document traversal
    if doc is not None:
        extractor._parse_docling_document(doc)

    # Fallback: markdown pattern cascade
    if not extractor.elements:
        if extractor.discarded_content:
            extractor.discarded_content.clear()
        extractor._parse_markdown(cleaned_text)

    # Build metadata
    elapsed_ms = (time.time() - t_start) * 1000
    metadata = {
        "element_count": len(extractor.elements),
        "detected_format": format_hint,
        "processing_time_ms": round(elapsed_ms, 2),
        "page_count": None,
        "title": None,
    }

    return ExtractionResult(elements=extractor.elements, metadata=metadata)
