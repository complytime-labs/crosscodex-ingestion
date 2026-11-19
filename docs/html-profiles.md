# HTML profiles: how the engine works

Maintainer notes for `src/crosscodex_ingestion/html_backend.py`. Profile loading and validation live in
`html_profile.py`; the file format is in `src/crosscodex_ingestion/schemas/html-profile-v1.schema.json`.

## Engine

`SiteChromeStrippingHTMLBackend.convert()` first strips site chrome (nav, header, footer; see the module
docstring), then tests each bound profile's `detect` selector with `soup.select_one` on the stripped page.

- Exactly one profile matches: `_apply_profile` rewrites the parsed tree with it and the class attribute
  `applied` is set to that profile.
- More than one matches: none is applied and `ambiguous` is set.
- None matches: the page is converted unchanged.

Docling instantiates the backend class itself, so profiles cannot be passed to a constructor.
`html_backend_for(profiles)` returns a fresh subclass with `profiles` bound, and the outcome (`applied`,
`ambiguous`) is stored on that subclass. Every `convert()` resets both before doing anything else, so a
reused subclass never reports a previous document's outcome. A subclass is not safe to share between
concurrent conversions; the service runs each conversion in its own subprocess.

Selectors are evaluated with soupsieve through BeautifulSoup (`select`, `select_one`, `Tag.css.match`).

## Operations and their order

The operations always run in this order, whatever order the YAML lists them in:

1. `unwrap`: every match of each selector is replaced by its children.
2. `remove`: every match of each selector is deleted, but only when the raw source has as many start
   as end tags for the match's tag name (the same balance guard chrome stripping uses: with
   `html.parser`, an unclosed element swallows the rest of the page, so deleting it would delete the
   document). Matches inside an already-removed element are skipped.
3. `title`: the `lines` inside the first `container` match are joined by a space into one `<h1>`,
   inserted where the first line was; the lines are removed. Nothing happens if no line matches.
   A `lines` selector that matches nested elements repeats their text, so anchor it (for example
   `:scope >`), as the EUR-Lex profile does.
4. `divisions`: a `<div>` not matching `exclude` whose first element child matches `title` is a division.
   That child becomes `<h{depth+1}>` (capped at `<h6>`), where depth counts the division and its
   division ancestors. Consecutive following siblings matching `subtitle` are appended to the heading
   text, joined by `separator` (default `" - "`), and removed.
5. `numbered_tables`: a table not matching `exclude`, with exactly one row of two cells, whose first
   cell's whitespace-normalized text fully matches the `label` regex and whose second cell starts with a
   `<p>`, is replaced by the second cell's content, with the label prefixed to that `<p>`. Nested
   numbering tables are unwrapped the same way.

Why this order:

- Unwrapping first lets later selectors and text joins see the content without inline wrappers.
- Removing before rewriting keeps removed furniture (a page header laid out as a table) out of the
  title, division and table passes.
- `title` must run, and must produce an `<h1>`, before `divisions` creates headings. Docling's
  `infer_furniture` moves everything before the first heading to the furniture layer, so a page whose
  first heading is a division would lose the content above it (for an act, its preamble). The `<h1>`
  is also what Docling reports as the document title. Division headings start at `<h2>` for the same
  reason: `<h1>` is left to the title.
- Numbered tables run last, the order in which the EUR-Lex rules were written and verified.

## The EUR-Lex profile (`html_profiles/eur-lex-oj.yaml`)

Official Journal pages from eur-lex.europa.eu use no `<h*>` elements and lay out numbered text as tables.

- `detect`: any element with a class token starting `oj-` (the Official Journal stylesheet's
  namespace). Class-prefix tests use the pair `[class^='oj-'], [class*=' oj-']` because `[class^='oj-']`
  alone only checks the first token and misses `class="a oj-b"`.
- `unwrap: ["a"]`: Docling splits a paragraph at each link, which left footnote markers such as "(1)"
  and the punctuation after them as separate paragraphs.
- `remove`: the OJ page header, a table containing a `<p>` with an `oj-hd-*` class. `:not(.oj-table)`
  keeps the rule that `oj-table` (EUR-Lex's mark for real data tables) is never treated as the header.
- `title`: the `oj-doc-ti` lines directly inside the first `div.eli-main-title` become the act title.
- `divisions`: chapters, titles and sections (`oj-ti-section-1`), articles (`oj-ti-art`) and annexes
  (`oj-doc-ti`). `exclude: div.eli-main-title` stops the act title block, which also holds `oj-doc-ti`
  lines, from being a division. Subtitles (`div.eli-title`, `oj-ti-section-2`, `oj-sti-art`, `oj-doc-ti`)
  produce headings such as "CHAPTER I - General provisions".
- `numbered_tables`: recitals, points and indents are laid out as label/text tables; `label` accepts
  "(5)", "(a)", "12." and dashes. `exclude: table.oj-table` keeps data tables as tables.

Known limit: content after the last article (final provisions, signatures, footnotes) attaches to that
article, as nothing in the markup marks where the article ends.
