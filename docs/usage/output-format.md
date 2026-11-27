# Output format

For anyone who consumes the service's output. Back to the [README](../../README.md).

The file at `structured_json_uri` is one JSON document. Its contract is the JSON Schema
[`src/crosscodex_ingestion/schemas/structured-output-v1.json`](../../src/crosscodex_ingestion/schemas/structured-output-v1.json),
which is also installed with the package (`crosscodex_ingestion/schemas/structured-output-v1.json`).

The fallback's output for a one-heading document (the `decompose-nested` test input) whose text has two
clauses, `(a)` and `(b)`. Clause `(a)` splits again, so it is a `section`; `(b)` and the leaves are controls.
`processing_time_ms` is the one illustrative value.

```json
{
  "schema_version": "1.5",
  "elements": [
    {"element_id": "e-001", "type": "heading", "content": "Nested", "page": null, "level": 1, "parent_id": null, "style": {}, "id": "sec-1.a", "role": "section", "title": "Nested", "text": "lead1 lead2 lead3 lead4 lead5 lead6 lead7 lead8 lead9 lead10 lead11 lead12 lead13 lead14 lead15 lead16 lead17 lead18 lead19 lead20 lead21 lead22 lead23 lead24 lead25 lead26 lead27 lead28 lead29 lead30\n1. mid1 mid2 mid3 mid4 mid5 mid6 mid7 mid8 mid9 mid10 mid11 mid12 mid13 mid14 mid15 mid16 mid17 mid18 mid19 mid20 mid21 mid22 mid23 mid24 mid25 mid26 mid27 mid28 mid29 mid30\n  i. p1 p2 p3 p4 p5 p6 p7 p8 p9\n  ii. q1 q2 q3 q4 q5 q6 q7 q8 q9\n2. r1 r2 r3 r4 r5 r6 r7 r8 r9", "properties": {"label": "a"}},
    {"element_id": "e-002", "type": "paragraph", "content": "lead1 lead2 lead3 lead4 lead5 lead6 lead7 lead8 lead9 lead10 lead11 lead12 lead13 lead14 lead15 lead16 lead17 lead18 lead19 lead20 lead21 lead22 lead23 lead24 lead25 lead26 lead27 lead28 lead29 lead30\n1. mid1 mid2 mid3 mid4 mid5 mid6 mid7 mid8 mid9 mid10 mid11 mid12 mid13 mid14 mid15 mid16 mid17 mid18 mid19 mid20 mid21 mid22 mid23 mid24 mid25 mid26 mid27 mid28 mid29 mid30\n  i. p1 p2 p3 p4 p5 p6 p7 p8 p9\n  ii. q1 q2 q3 q4 q5 q6 q7 q8 q9\n2. r1 r2 r3 r4 r5 r6 r7 r8 r9", "page": null, "level": null, "parent_id": "e-001", "style": {}, "id": null},
    {"element_id": "e-003", "type": "heading", "content": "Nested", "page": null, "level": 2, "parent_id": "e-001", "style": {}, "id": "sec-1.a.1", "role": "section", "title": "Nested", "text": "mid1 mid2 mid3 mid4 mid5 mid6 mid7 mid8 mid9 mid10 mid11 mid12 mid13 mid14 mid15 mid16 mid17 mid18 mid19 mid20 mid21 mid22 mid23 mid24 mid25 mid26 mid27 mid28 mid29 mid30\n  i. p1 p2 p3 p4 p5 p6 p7 p8 p9\n  ii. q1 q2 q3 q4 q5 q6 q7 q8 q9", "properties": {"label": "1"}},
    {"element_id": "e-004", "type": "paragraph", "content": "mid1 mid2 mid3 mid4 mid5 mid6 mid7 mid8 mid9 mid10 mid11 mid12 mid13 mid14 mid15 mid16 mid17 mid18 mid19 mid20 mid21 mid22 mid23 mid24 mid25 mid26 mid27 mid28 mid29 mid30\n  i. p1 p2 p3 p4 p5 p6 p7 p8 p9\n  ii. q1 q2 q3 q4 q5 q6 q7 q8 q9", "page": null, "level": null, "parent_id": "e-003", "style": {}, "id": null},
    {"element_id": "e-005", "type": "paragraph", "content": "p1 p2 p3 p4 p5 p6 p7 p8 p9", "page": null, "level": null, "parent_id": "e-003", "style": {}, "id": "sec-1.a.1.i", "role": "control", "title": "Nested", "text": "p1 p2 p3 p4 p5 p6 p7 p8 p9", "properties": {"label": "i"}},
    {"element_id": "e-006", "type": "paragraph", "content": "q1 q2 q3 q4 q5 q6 q7 q8 q9", "page": null, "level": null, "parent_id": "e-003", "style": {}, "id": "sec-1.a.1.ii", "role": "control", "title": "Nested", "text": "q1 q2 q3 q4 q5 q6 q7 q8 q9", "properties": {"label": "ii"}},
    {"element_id": "e-007", "type": "paragraph", "content": "r1 r2 r3 r4 r5 r6 r7 r8 r9", "page": null, "level": null, "parent_id": "e-001", "style": {}, "id": "sec-1.a.2", "role": "control", "title": "Nested", "text": "r1 r2 r3 r4 r5 r6 r7 r8 r9", "properties": {"label": "2"}},
    {"element_id": "e-008", "type": "paragraph", "content": "flat1 flat2 flat3 flat4 flat5 flat6 flat7 flat8 flat9 flat10 flat11 flat12 flat13 flat14 flat15 flat16 flat17 flat18 flat19 flat20 flat21 flat22 flat23 flat24 flat25 flat26 flat27 flat28 flat29 flat30 flat31 flat32 flat33 flat34 flat35 flat36 flat37 flat38 flat39 flat40 flat41 flat42 flat43 flat44 flat45", "page": null, "level": null, "parent_id": null, "style": {}, "id": "sec-1.b", "role": "control", "title": "Nested", "text": "flat1 flat2 flat3 flat4 flat5 flat6 flat7 flat8 flat9 flat10 flat11 flat12 flat13 flat14 flat15 flat16 flat17 flat18 flat19 flat20 flat21 flat22 flat23 flat24 flat25 flat26 flat27 flat28 flat29 flat30 flat31 flat32 flat33 flat34 flat35 flat36 flat37 flat38 flat39 flat40 flat41 flat42 flat43 flat44 flat45", "properties": {"label": "b"}}
  ],
  "metadata": {"element_count": 8, "detected_format": "pdf", "page_count": null, "title": null, "processing_time_ms": 1.0, "dropped": [], "structure_method": "heading", "structure": {"heading_count": 1, "table_count": 0, "max_heading_depth": 1, "unparented_count": 0, "fragment_count": 0, "table_rejoin_count": 0, "warnings": []}}
}
```

A node with children is a heading followed by an id-less paragraph holding its text, then its children;
a node without children is one paragraph. This is the same shape for documents and structured sources.
`metadata.structure` describes the layout the structurer was given (one heading, no tables), not these controls.

For an HTML page that matched a profile, `metadata` also carries the profile that shaped it:

```json
"html_profile": {"id": "eur-lex-oj", "sha256": "<SHA-256 of the profile file>"}
```

| Field                         | Meaning                                                                                                                                                   |
|-------------------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------|
| `schema_version`              | `MAJOR.MINOR` version of the schema the document follows (see "Versions" below).                                                                          |
| `elements`                    | Document content in reading order.                                                                                                                        |
| `element_id`                  | Opaque identifier, unique within the document. Don't parse it.                                                                                            |
| `type`                        | `heading`, `paragraph`, `list_item` or `table`. A table's `content` is Markdown.                                                                          |
| `content`                     | The element's text. Never blank.                                                                                                                          |
| `page`                        | 1-based page the element starts on; `null` for HTML, DOCX and text, or when Docling recorded none.                                                        |
| `level`                       | Heading depth, 1 (top) to 100; `null` on every other type.                                                                                                |
| `parent_id`                   | `element_id` of the heading the element sits under, or `null` at the top level.                                                                           |
| `style`                       | Reserved; always `{}`.                                                                                                                                    |
| `metadata.page_count`         | Pages in the source; `null` for unpaginated formats.                                                                                                      |
| `metadata.title`              | Document title as Docling labelled it, or `null`.                                                                                                         |
| `metadata.processing_time_ms` | Wall time of the conversion worker.                                                                                                                       |
| `metadata.dropped`            | Source content that was not emitted as an element, each with `type`, `page` and `reason`. Empty when nothing was dropped.                                 |
| `id` (1.1)                    | The node's ID: from the source for structured sources, derived by the fallback structurer for documents (since 1.4); unique among non-null ids. `null` on an element without an ID of its own. |
| `title` (1.1)                 | The node's title (the source's, or for documents the heading's). Since 1.3, an element with a string `id` always carries it (`null` when the node has no title), headings included.               |
| `role` (1.3)                  | `group`, `control` or (1.4) `section`, on every element with a string `id`. For structured sources the mapping decides (see "Writing a mapping" in Structured sources); for documents it is `control` or `section`.  |
| `text` (1.3)                  | The node's own text, or `null` when it has none (structured sources and, since 1.4, documents); on every element with a string `id`. Don't read the id-less paragraph that follows a heading for this.    |
| `properties` (1.1)            | Extra source fields the mapping names, as `name: string`.                                                                                                 |
| `metadata.source` (1.1)       | Present for structured sources: `kind` (`structured`), `format`, `mapping` (`id`, `sha256`, `mapping_version`), `node_count`, `unmapped_fields`; since 1.3 also `catalog_version`, the mapping's `version` or `null`. |
| `metadata.structure_method` (1.4) | How the structure was found, always present: `mapping` (structured sources), or `heading`, `table`, `pattern` (the fallback structurer's tier for a document). |
| `metadata.structure` (1.2)    | Structure signals: `heading_count`, `table_count`, `max_heading_depth`, `unparented_count`, `fragment_count`, `table_rejoin_count` (1.5: page breaks across which a PDF table's parts were rejoined), and `warnings` (open-ended codes such as `no_headings`, and since 1.5 `table_rejoined`). |
| `metadata.html_profile` (1.2) | Present when an HTML profile shaped the conversion: `id` and the `sha256` of the profile file (see "HTML profiles").                                      |

## PDF heading levels

Docling marks PDF headings without a level (every one is level 1). For PDF input whose headings are
all level 1, the level comes from the section number at the start of the heading:

- Decimal numbers nest by their number of parts: `2.` or `2`, then `2.1` or `2.1.`, then
  `03.01.01` (leading zeros are allowed, at most 6 parts).
- `CHAPTER`, `ANNEX` and `APPENDIX` headings rank with one-part numbers, `SECTION` with two-part
  numbers and `ARTICLE` with three-part numbers, in any letter case. The number after the word can be
  arabic (`Article 12`), roman (`CHAPTER IV`) or a word from ONE to TWENTY (`CHAPTER TWO`); annexes and
  appendices can also use a letter (`Appendix A.`).
- The ranks a document uses are numbered from level 1 down, so NIS2's `CHAPTER I` is level 1 and its
  `Article 1` level 2, and 800-53r5's `CHAPTER THREE` is level 1 and its `3.1 ACCESS CONTROL` level 2.
- A heading without a number (`DISCUSSION`, `Subject matter`, `AC-2 ACCOUNT MANAGEMENT`) is one level
  below the numbered heading before it. Headings before the first numbered one are level 1.
- A number that does not come after the previous one of its kind and depth is not used. For
  example, 800-53r5 has a list item, `3. Include a description of the authorized uses of the
  system.`, labelled as a heading inside `CHAPTER THREE`. It is treated as a heading without a number.
  The check restarts under each new parent, so a requirement quoted early as an example (`03.13.11` in
  800-171r3's section 2.2) does not hide the real ones later.
- When fewer than two headings carry a usable number, every heading stays at level 1.

Font size is not used: Docling's output does not record it. A heading's box height grows with its line
count, so it does not show the font size either. Limits that follow from this:

- Control identifiers such as `AC-1` and enhancements such as `(1)` are not section numbers. In 800-53r5
  they are siblings under their family (`3.1 ACCESS CONTROL`).
- An unnumbered heading after the last numbered section, such as a closing `REFERENCES`, nests under
  that section.
- A number may skip ahead, because Docling misses some headings (NIS2 goes from `Article 31` to
  `Article 37`). So a stray item numbered *higher* than the real next section is taken as a heading,
  and the real ones after it at that depth are treated as unnumbered.
- Not recognised as numbering: a number without a space after it (`1.Intro`), lettered decimals
  (`A.1`), suffixed articles (`Article 2a`), parts of more than 3 digits, and roman numerals above C.
  `ANNEX` and `APPENDIX` numbers are not checked for order, because a letter and a roman numeral
  (`ANNEX I`, `ANNEX C`) cannot be told apart.

DOCX and HTML keep the levels in their markup.

## What HTML conversion leaves out

For HTML input, site chrome is not emitted:

- Every `<nav>` or `role="navigation"` element, wherever it is, including inside `<main>` or
  `<article>`. Its label heading goes too, but only when the heading and the nav are the only two
  element children of their parent and the parent has no other text (a `<div><h2>Pages</h2><nav>`
  sidebar). A heading followed by a nav and then more content is kept.
- `<header>`/`role="banner"` and `<footer>`/`role="contentinfo"` elements, but only when the page has
  a `<main>` or `role="main"` and the element is not inside `<main>`, `<article>`, `<section>` or
  `<aside>`. Pages without a main landmark keep them (a pandoc title block, a "References" footer).
- Nothing is removed when its tag's start and end tags do not balance in the source (an unclosed
  `<nav>` would otherwise swallow the document), and nothing that contains the `<main>` or
  `role="main"` element is removed; that chrome is kept.

Chrome that is not marked up with these elements or roles (for example `<div class="menu">`) is not
recognised and is still emitted.

## EUR-Lex pages

EUR-Lex HTML (the Official Journal reuse format, recognised by its `oj-` CSS classes) has no `<h*>`
elements and lays out numbered text as tables. The packaged profile
[`html_profiles/eur-lex-oj.yaml`](../../src/crosscodex_ingestion/html_profiles/eur-lex-oj.yaml) (see "HTML
profiles") rewrites these pages; for them only:

- The act's title (for example "REGULATION (EU) 2016/679 ... (Text with EEA relevance)") becomes
  `metadata.title`, and is not emitted as an element.
- Chapters, sections, articles and annexes become nested headings that include their subtitle,
  such as `CHAPTER III - Rights of the data subject` (level 1), `Section 1 - Transparency and
  modalities` (level 2) and `Article 12 - Transparent information, ...` (level 3). Article text
  attaches to its article. The preamble (citations and recitals) comes before the first heading
  and has no parent.
- Recitals and numbered points are paragraphs that keep their number, such as `(5) The economic
  and social integration ...`, `(a) ...` or `- their parliament;`. Footnote markers stay inside their
  paragraph (`... Social Committee (1),`). Real data tables (annex tables) are still tables.
- The Official Journal page header (`4.5.2016 | EN | Official Journal of the European Union |
  L 119/1`) is not emitted.

The final provisions, signatures and footnotes after the last article are attached to that
article, because the markup does not mark where the article ends.

## HTML profiles

A profile is a reviewed YAML file that describes how one publisher marks up document structure, so the
HTML converter can turn that markup into headings and paragraphs. Profiles ship with the package
(`src/crosscodex_ingestion/html_profiles/`; today only `eur-lex-oj`) and, optionally, in an operator
directory: put `<id>.yaml` files there and point `HTML_PROFILES_PATH` at it. They are added to the packaged ones.
Requests cannot name or supply a profile: the converter picks one from the page's content.

A page matches a profile when its `detect` selector finds an element (after navigation and page header and
footer are removed). Exactly one match applies. No match leaves the page as the converter reads it, and
more than one match applies none and adds the `html_profile_ambiguous` warning to `metadata.structure.warnings`.

A file is `profile_version: 1` with these keys. `id`, `profile_version`, `description` and `detect` are
required; every operation section is optional.

| Key                       | Meaning                                                                                                                      |
|---------------------------|------------------------------------------------------------------------------------------------------------------------------|
| `id`                      | `^[a-z0-9][a-z0-9-]*$`, at most 128 characters, unique; the file must be named `<id>.yaml`.                                  |
| `description`             | Free text for reviewers.                                                                                                     |
| `detect`                  | CSS selector. The profile matches a page when it finds an element.                                                           |
| `unwrap`                  | List of selectors; every match is replaced by its children.                                                                  |
| `remove`                  | List of selectors; every match is removed, unless its tag's start and end tags do not balance in the source.                 |
| `title.container`         | Selector for the element holding the document title (first match is used).                                                   |
| `title.lines`             | Selector, relative to the container, for the title lines. They become one `<h1>` (lines joined by a space), the document title. |
| `divisions.title`         | Selector a `<div>`'s first element child must match for the `<div>` to be a division.                                        |
| `divisions.exclude`       | Optional selector; a `<div>` matching it is never a division.                                                                |
| `divisions.subtitle`      | Optional selector; consecutive following siblings of the title that match are merged into the heading and removed.           |
| `divisions.separator`     | String joining title and subtitles; default `" - "`.                                                                         |
| `numbered_tables.exclude` | Optional selector; matching tables are never unwrapped.                                                                      |
| `numbered_tables.label`   | Regex the first cell's whitespace-normalized text must fully match; such one-row, two-cell tables become a numbered paragraph. |

Every selector, `description` and `numbered_tables.label` is 1 to 2048 characters; `divisions.separator` is at most 64.

Operations run in this order: unwrap, remove, title, divisions, numbered tables. Selectors and regexes are compiled when the
service starts. The schema is
[`schemas/html-profile-v1.schema.json`](../../src/crosscodex_ingestion/schemas/html-profile-v1.schema.json);
how the engine works is in [docs/html-profiles.md](../html-profiles.md).

At startup the service validates every profile and **refuses to start** if any is invalid (with the file),
if `HTML_PROFILES_PATH` is not a directory, or if two files register the same ID. Restart the service to
pick up changes. Each output records the profile that shaped it in `metadata.html_profile` (`id` and the
SHA-256 of the file), so a stored output can be traced to the exact rules. A regex that runs long counts
against the conversion's CPU limit and fails that conversion, not the service.

## Document structure

Since 1.4 a document (PDF, DOCX, HTML or text) is returned as a catalog of controls, not as raw layout. The
service looks for controls in this order and stops at the first that finds any. `metadata.structure_method`
names it.

1. `heading`: one control per heading, its text the content under it. A heading title that repeats more than
   three times (page furniture) is skipped.
2. `table`: one control per table row, the first cell as the ID when it is 1 to 49 bytes.
3. `pattern`: several numbering patterns are tried in a fixed order, and the first that starts at least three
   lines wins, one control per matching line.

A control of 40 words or more with clauses on separate lines, each labelled `(a)`, `1.`, `i.`, `a.` or `A.`, is
replaced by its clauses, which become controls with IDs `<id>.<label>`. Any lines before the first clause (a
lead-in) are not kept, which matches crosscodex's cascade. Only a clause that splits again becomes a `section`,
and its `text` is that clause's full text, sub-clauses included. Each clause sits under its nearest enclosing section.

What changes for a reader of document output:

- Elements with an `id` carry `role`, `title` and `text`, as for structured sources. Read controls from
  those; the heading and paragraph elements are the same nodes in the 1.0 layout shape.
- IDs are derived from the document (`sec-3`, a table's first cell, a matched number, `<id>.<label>`
  for clauses). They are stable for the same input and the same service version, and not a catalog's own IDs.
  They are not a catalog's own IDs; document mappings that would give those are planned and none exist yet.
- `page` is set where the layout had pages.
- `metadata.structure` still describes the layout Docling produced, not the controls.
- Since 1.5 a PDF's layout is repaired before the structure is found (see "PDF layout repair").
- Nothing is configurable: the same document gives the same structure on every deployment.

A document with no controls fails with `ERROR_CODE_INVALID_ARGUMENT` and nothing is stored. Prose-only text,
or a document whose controls' identifiers repeat, is such a case. The message names the format and the
registered mappings for it. The message suggests a document mapping, but none exist yet (planned: issue #38,
phase 3), so such a document cannot be converted by this version.

### PDF layout repair

Since 1.5, a PDF is cleaned of two layout artefacts before the structure is found. Both apply to PDFs only,
and each removal is listed in `metadata.dropped`, so nothing disappears silently.

- **Tables continued across a page break are one table.** When a table's next page starts with a table of
  the same column count (the same header repeated, or no header) and nothing but blank text or removed
  running text lies between them, the parts are joined and the repeated header rows are removed. Each removed
  row is a `dropped` entry of type `row`, with its page and the reason `repeated header row of a table
  continued from page <n>; removed when the parts were rejoined`. A control from the table tier keeps the page
  of its own row. `metadata.structure.table_rejoin_count` counts the page breaks joined, and when it is above 0
  `metadata.structure.warnings` contains `table_rejoined`.
- **Unlabelled running headers and footers are removed.** A short text (at most 100 characters) that
  appears on at least 3 pages, always within the top or bottom 10% of the page, is dropped, as an entry
  of type `furniture` with the reason `text repeated in the page margin on <n> pages; removed as an
  unlabelled running header or footer`. Text Docling already labels as a page header or footer was never emitted.

The header must repeat exactly (same text after collapsing whitespace, same case). When Docling reads a
repeated header differently on a later page, the table stays two tables: a missed join is safe, a false one
is not. A table with a caption, footnote or other content between its parts is joined only when that content
is the table's own, or blank. A table that can't be exported, or whose later part is nothing but the repeated
header, is never joined. The thresholds are fixed, not configurable.

## What version 1.0 guarantees

These hold for every 1.x output, and version 1.1 adds the guarantees in [Structured sources](structured-sources.md).

- No element has blank `content`. A heading whose text is blank is not emitted: the content under it is
  attached to the nearest non-blank heading above, and the heading is listed in `metadata.dropped`.
- `level` is an integer from 1 to 100 on headings and `null` on everything else.
- `element_id` values are unique, and every non-null `parent_id` is the `element_id` of an **earlier**
  element, so one pass in document order can build the tree. `metadata.element_count` equals the number
  of elements. JSON Schema can't express these three rules: a generic schema validator doesn't check
  them, but the service does (see below).
- A table that can't be exported is listed in `metadata.dropped` with its page, and logged by the service;
  the rest of the document still converts. Nothing is discarded silently.
- The service validates every output against the schema and the rules above before it stores it.
  - A conversion whose output doesn't conform stores nothing and fails with `ERROR_CODE_INTERNAL` (it is a
    service bug, not a problem with your request).
  - The error message names the offending element and rule, and `Error.details` carries `schema_version`,
    `location` and, when the problem is inside an element, `element_index` and `element_id`.
  - Messages never quote document text. The only values they quote are element ids and property names, and
    only when they have the form the service generates.

## Versions

`schema_version` is `MAJOR.MINOR`. There is one schema file per major, and its name carries the
major (`-v1`).

- A **minor** version only adds optional fields (on elements or in `metadata`). Every field and guarantee
  of earlier minors keeps its meaning. **Readers must ignore fields they don't know**, so a reader written
  for 1.0 reads any 1.x output.
- A **major** version is anything else: removing, renaming or retyping a field, a new `type` value,
  or a weaker guarantee.
- Readers should check the major of `schema_version` and reject a major they don't know.
- The `-v1` file validates every released 1.x output: `schema_version` lists each released minor, and
  fields added by a minor are optional, so outputs stored under an earlier minor stay valid. The file
  rejects fields it doesn't define, so validate with a copy at least as new as the output (a 1.0-era
  copy rejects fields that 1.1 adds).

| Version | Adds |
|---|---|
| 1.0 | Elements (`heading`, `paragraph`, `list_item`, `table`) with `element_id`, `parent_id`, `level`, `page`; `metadata`. |
| 1.1 | Optional element fields `id`, `title`, `properties` and optional `metadata.source`, emitted for structured sources. Documents converted by Docling are unchanged apart from the version. |
| 1.2 | Optional `metadata.structure` (structure signals) and `metadata.html_profile` (the HTML profile applied, with its hash). |
| 1.3 | Optional element fields `role`, `text` (and `title` on headings) on elements with a string `id`, and optional `metadata.source.catalog_version`, emitted for structured sources. |
| 1.4 | Required `metadata.structure_method`; `role` `section`. Documents now carry `id`, `role`, `title` and `text` like structured sources. |
| 1.5 | Required `metadata.structure.table_rejoin_count`; `metadata.dropped` types `row`, `furniture` and `paragraph`; warning `table_rejoined`. A PDF's split tables are rejoined and its unlabelled running headers and footers removed. |
