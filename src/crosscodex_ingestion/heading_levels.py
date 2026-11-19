"""
Heading levels inferred from section numbering, for PDF headings Docling leaves flat.

Docling's PDF pipeline labels every section header level 1. The numbering in the heading text
is the evidence used to nest them instead. Each recognised form has a fixed rank:

  rank 1: CHAPTER <n>, ANNEX <n>, APPENDIX <n>, and decimal numbers of one part ("3.", "3")
  rank 2: SECTION <n>, and decimal numbers of two parts ("3.1", "3.1.")
  rank 3: ARTICLE <n>, and decimal numbers of three parts ("03.01.01")
  rank d: decimal numbers of d parts (at most 6)

The ranks present in a document are renumbered 1, 2, 3 ... in order, so a document whose
shallowest numbers are "1.1" starts at level 1. A heading without recognised numbering sits one
level below the numbered heading before it, or at level 1 before the first one.

A number that does not advance past the previous number of its kind and rank, since a shallower
heading was last accepted, is not structure (NIST SP 800-53r5 has a list item "3. Include ..."
promoted to a heading inside CHAPTER THREE); it is treated as unnumbered. ANNEX and APPENDIX are
not sequence-checked: their letters and roman numerals cannot be told apart ("ANNEX I", "ANNEX C").

Numbers may skip ahead (Docling misses headings), so a stray item numbered higher than the next
real section is accepted and hides the real ones at its rank. Roman numerals above C are read as
words, not numbers.

With fewer than two numbered headings there is no evidence, and every heading stays at level 1.
"""

import re

_MAX_DECIMAL_PARTS = 6
_MIN_NUMBERED_HEADINGS = 2
_MAX_ROMAN = 100

_DECIMAL = re.compile(rf"^(\d{{1,3}}(?:\.\d{{1,3}}){{0,{_MAX_DECIMAL_PARTS - 1}}})\.?(?=\s|$)")
_DIVISION = re.compile(
    r"^(chapter|section|article|annex|appendix)\s+([0-9]{1,3}|[a-z]+)(?=$|[\s.:\-\u2013\u2014])", re.IGNORECASE
)
_ROMAN = re.compile(r"^m{0,3}(cm|cd|d?c{0,3})(xc|xl|l?x{0,3})(ix|iv|v?i{0,3})$")
_ROMAN_VALUES = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}
_WORDS = [
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
    "eleven",
    "twelve",
    "thirteen",
    "fourteen",
    "fifteen",
    "sixteen",
    "seventeen",
    "eighteen",
    "nineteen",
    "twenty",
]
_DIVISION_RANKS = {"chapter": 1, "annex": 1, "appendix": 1, "section": 2, "article": 3}


def _ordinal(token: str) -> int | None:
    """Return the value of an arabic, roman or English-word ordinal, or None if *token* is none of them."""
    token = token.lower()
    if token.isdigit():
        return int(token)
    if token in _WORDS:
        return _WORDS.index(token) + 1
    if not _ROMAN.match(token):
        return None
    values = [_ROMAN_VALUES[c] for c in token]
    value = sum(-v if i + 1 < len(values) and v < values[i + 1] else v for i, v in enumerate(values))
    # Above C, a "numeral" is far more often a word ("Chapter Mix" reads as 1009).
    return value if value <= _MAX_ROMAN else None


def _numbering(title: str) -> tuple[str, int, tuple[int, ...] | None] | None:
    """Return (kind, rank, ordinal) for numbered *title*, or None; ordinal None means not sequence-checked."""
    if m := _DECIMAL.match(title):
        parts = tuple(int(p) for p in m.group(1).split("."))
        return "number", len(parts), parts
    if m := _DIVISION.match(title):
        keyword, token = m.group(1).lower(), m.group(2)
        if keyword in ("annex", "appendix"):
            if len(token) == 1 or _ordinal(token) is not None:
                return keyword, _DIVISION_RANKS[keyword], None
            return None
        value = _ordinal(token)
        if value is None:
            return None
        # CHAPTER n is the same position as the decimal number n, so the two share a sequence.
        return ("number" if keyword == "chapter" else keyword), _DIVISION_RANKS[keyword], (value,)
    return None


def infer_heading_levels(titles: list[str]) -> list[int]:
    """Return a level for each heading in *titles* (document order), from its section numbering."""
    ranks: list[int | None] = []
    last: dict[tuple[str, int], tuple[int, ...]] = {}
    for title in titles:
        found = _numbering(title.strip())
        if found is not None:
            kind, rank, ordinal = found
            previous = last.get((kind, rank))
            if ordinal is not None and previous is not None and ordinal <= previous:
                found = None
            else:
                last = {key: value for key, value in last.items() if key[1] < rank}
                if ordinal is not None:
                    last[(kind, rank)] = ordinal
        ranks.append(found[1] if found else None)

    if sum(r is not None for r in ranks) < _MIN_NUMBERED_HEADINGS:
        return [1] * len(titles)
    level_of = {r: i + 1 for i, r in enumerate(sorted({r for r in ranks if r is not None}))}
    levels: list[int] = []
    numbered_level = 0
    for heading_rank in ranks:
        if heading_rank is not None:
            numbered_level = level_of[heading_rank]
            levels.append(numbered_level)
        else:
            levels.append(numbered_level + 1)
    return levels
