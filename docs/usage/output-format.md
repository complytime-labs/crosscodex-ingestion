# Output format

For anyone who consumes the service's output. Back to the [README](../../README.md).

The file at `structured_json_uri` is one JSON document. Its contract is the JSON Schema
[`src/crosscodex_ingestion/schemas/structured-output-v1.json`](../../src/crosscodex_ingestion/schemas/structured-output-v1.json),
which is also installed with the package (`crosscodex_ingestion/schemas/structured-output-v1.json`).

```json
{
  "schema_version": "1.2",
  "elements": [
    {"element_id": "e-001", "type": "heading", "content": "Access Control", "page": 3,
     "level": 1, "parent_id": null, "style": {}},
    {"element_id": "e-002", "type": "paragraph", "content": "Limit system access to authorized users.",
     "page": 3, "level": null, "parent_id": "e-001", "style": {}},
    {"element_id": "e-003", "type": "table", "content": "| ID | Control |\n|----|----|\n| AC-1 | Policy |",
     "page": 4, "level": null, "parent_id": "e-001", "style": {}}
  ],
  "metadata": {
    "element_count": 3, "detected_format": "pdf", "page_count": 12, "title": "Access Policy",
    "processing_time_ms": 8412.5,
    "dropped": [{"type": "table", "page": 7, "reason": "table could not be exported to Markdown (ValueError)"}],
    "structure": {"heading_count": 1, "table_count": 1, "max_heading_depth": 1, "unparented_count": 0,
                  "fragment_count": 0, "warnings": []}
  }
}
```

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
| `id` (1.1)                    | The element's ID in the source (structured sources only); unique among non-null ids. `null` on an element without an ID of its own. Absent for documents. |
| `title` (1.1)                 | The source node's title, on a non-heading element whose `content` is its text (a heading's title is its `content`).                                       |
| `properties` (1.1)            | Extra source fields the mapping names, as `name: string`.                                                                                                 |
| `metadata.source` (1.1)       | Present for structured sources: `kind` (`structured`), `format`, `mapping` (`id`, `sha256`, `mapping_version`), `node_count`, `unmapped_fields`.          |
| `metadata.structure` (1.2)    | Structure signals: `heading_count`, `table_count`, `max_heading_depth`, `unparented_count`, `fragment_count`, and `warnings` (open-ended codes such as `no_headings`). |
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
