# CrossCodex Ingestion Service

----

> 🤖 LLM WARNING 🤖
>
> This project was written with LLM (AI) assistance.
>
> 🤖 LLM WARNING 🤖

----

Python gRPC service for secure multi-format document ingestion and conversion using Docling.

## What It Does

The Ingestion service receives documents via gRPC, validates them for security, converts them to structured JSON using Docling, stores the results, and optionally generates cryptographic attestations. It supports both embedded (same-process) and distributed (NATS JetStream) execution modes.

**Key Features:**
- Multi-format document support (PDF, DOCX, HTML, TXT)
- Structured sources (JSON, CSV, XLSX) read through reviewed, declarative mappings (see "Structured sources")
- Security hardening (SSRF prevention, format validation, subprocess isolation, resource limits)
- Structured element extraction (headings, paragraphs, tables, lists)
- State management (memory, Redis)
- Storage backends (filesystem, S3)
- In-toto cryptographic attestations
- NATS-based audit event publishing
- Prometheus metrics and OpenTelemetry tracing

## Quick Start

### Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) (Python package manager)
- podman or docker (for containerized deployment)

### Run in a container

```bash
task compose:up
```

This generates a throwaway dev PKI in `.test-output/certs` (see "Dev and test certificates"), builds the image
(slow the first time: it downloads torch and the Docling models), and starts the service on `localhost:8080`
with mutual TLS and attestation on, in the foreground. Stop it with Ctrl-C. Then
[convert a document](#convert-a-document).

### Local Development

```bash
# Create .venv and install all dependencies
task setup

# Generate protobuf stubs
task proto

# Download the Docling models once (see "Docling models" below)
uv run docling-tools models download -o ~/.cache/crosscodex/docling-models

# Generate the dev PKI and attestation key pair in .test-output/certs
task certs

# Run the server (embedded mode, filesystem storage, memory state) with mutual TLS and attestation
C=.test-output/certs
GRPC_TLS_CERT=$C/server.pem GRPC_TLS_KEY=$C/server.key GRPC_TLS_CA=$C/ca.pem \
  GRPC_ALLOWED_CLIENTS=crosscodex-gateway \
  ATTESTATION_PRIVATE_KEY_PATH=$C/attestation.key ATTESTATION_PUBLIC_KEY_PATH=$C/attestation.pub \
  DOCLING_ARTIFACTS_PATH=~/.cache/crosscodex/docling-models STORAGE_PATH=/tmp/crosscodex-data task run

# Run tests
task test

# Run integration tests (first run downloads the pinned models into .test-output/docling-models;
# a DOCLING_ARTIFACTS_PATH already set in your shell takes precedence)
task test:integration
```

The service refuses to start without the TLS and attestation settings. For a plaintext, unattested
throwaway run, set `GRPC_TLS_MODE=off ATTESTATION_ENABLED=false` instead; never do that on a shared host.

### Convert a document

Any gRPC client works. These examples use [grpcurl](https://github.com/fullstorydev/grpcurl) with the dev client
certificate (CN `crosscodex-gateway`, the identity `GRPC_ALLOWED_CLIENTS` admits above and in compose). Run them
from the repository root, so the dev certificates and `proto/` resolve:

```bash
C=.test-output/certs
G="grpcurl -cacert $C/ca.pem -cert $C/client.pem -key $C/client.key -import-path proto -proto crosscodex/v1/ingestion.proto"

# Submit a document (bytes fields are base64 in grpcurl's JSON); prints a documentId
$G -d "{\"tenant_context\": {\"tenant_id\": \"default\"},
        \"content\": \"$(base64 -w0 policy.html)\",
        \"metadata\": {\"mime_type\": \"text/html\"}}" \
  localhost:8080 crosscodex.v1.IngestionService/ConvertDocument

# Poll until conversionStatus is JOB_STATUS_COMPLETED (or JOB_STATUS_FAILED, with an error)
$G -d '{"tenant_context": {"tenant_id": "default"}, "document_id": "<documentId>"}' \
  localhost:8080 crosscodex.v1.IngestionService/GetDocument
```

Conversion is asynchronous. A completed document's `structuredJsonUri` points to the extracted elements under
`STORAGE_PATH` (in compose, inside the container's `/data/documents`). Use `"source_uri": "https://…"`
instead of `content` to have the service fetch a URL. `tenant_id` must be a valid identifier (see
"Identifiers"). For JSON, CSV or XLSX, also set `"mapping"` (see "Structured sources").

## Output format

The file at `structured_json_uri` is one JSON document. Its contract is the JSON Schema
[`src/crosscodex_ingestion/schemas/structured-output-v1.json`](src/crosscodex_ingestion/schemas/structured-output-v1.json),
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

### What HTML conversion leaves out

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

### EUR-Lex pages

EUR-Lex HTML (the Official Journal reuse format, recognised by its `oj-` CSS classes) has no `<h*>`
elements and lays out numbered text as tables. The packaged profile
[`html_profiles/eur-lex-oj.yaml`](src/crosscodex_ingestion/html_profiles/eur-lex-oj.yaml) (see "HTML
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

### HTML profiles

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
[`schemas/html-profile-v1.schema.json`](src/crosscodex_ingestion/schemas/html-profile-v1.schema.json);
how the engine works is in [docs/html-profiles.md](docs/html-profiles.md).

At startup the service validates every profile and **refuses to start** if any is invalid (with the file),
if `HTML_PROFILES_PATH` is not a directory, or if two files register the same ID. Restart the service to
pick up changes. Each output records the profile that shaped it in `metadata.html_profile` (`id` and the
SHA-256 of the file), so a stored output can be traced to the exact rules. A regex that runs long counts
against the conversion's CPU limit and fails that conversion, not the service.

### What version 1.0 guarantees

These hold for every 1.x output, and version 1.1 adds the guarantees in "Structured sources".

- No element has blank `content`. A heading whose text is blank is not emitted: the content under it is
  attached to the nearest non-blank heading above, and the heading is listed in `metadata.dropped`.
- `level` is an integer from 1 to 100 on headings and `null` on everything else.
- `element_id` values are unique, and every non-null `parent_id` is the `element_id` of an **earlier**
  element, so one pass in document order can build the tree. `metadata.element_count` equals the number
  of elements. JSON Schema can't express these three rules: a generic schema validator doesn't check
  them, but the service does (see below).
- A table that can't be exported is listed in `metadata.dropped` with its page, and logged by the service;
  the rest of the document still converts. Nothing is discarded silently.
- The service validates every output against the schema and the rules above before it stores it. A
  conversion whose output doesn't conform stores nothing and fails with `ERROR_CODE_INTERNAL` (it is a
  service bug, not a problem with your request). The error message names the offending element and
  rule, and `Error.details` carries `schema_version`, `location` and, when the problem is inside an
  element, `element_index` and `element_id`. Messages never quote document text. The only values they
  quote are element ids and property names, and only when they have the form the service generates.

### Versions

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

## Structured sources

Control frameworks are often published as data: OWASP ASVS as JSON, MITRE ATT&CK and NIST SP 800-53 as
spreadsheets. The service reads JSON, CSV and XLSX through a **mapping**, a small reviewed YAML file that
says where the nodes are and which fields hold each node's ID, title, text and properties. The output is the
same element format as for documents, plus the source IDs and nesting.

### Requesting a conversion

Set `mapping` on `ConvertDocumentRequest` to the ID of a mapping registered on the server, and send the
file as `content` or `source_uri` as usual:

```bash
$G -d "{\"tenant_context\": {\"tenant_id\": \"default\"},
        \"content\": \"$(base64 -w0 OWASP_Application_Security_Verification_Standard_5.0.0_en.json)\",
        \"mapping\": \"owasp-asvs-5.0.0-json\"}" \
  localhost:8080 crosscodex.v1.IngestionService/ConvertDocument
```

- The mapping must be registered, or `ConvertDocument` fails at once with `INVALID_ARGUMENT` listing the
  registered IDs. Mappings can't be sent inline or by URL.
- The mapping declares the format, so with a mapping no `metadata.mime_type` or URL extension is needed.
  The input must be what the mapping reads: a JSON mapping needs JSON (first character `{` or `[`), an XLSX
  mapping a complete workbook, a CSV mapping UTF-8 text that doesn't start like JSON. Otherwise the
  conversion fails, for example:
  - `Mapping 'mitre-attack-enterprise-17.1-techniques-xlsx' reads xlsx, but the input is docx.`
  - `Mapping 'example-csv' reads csv, but the input is json.`
  - `Mapping 'nist-sp800-53r5-catalog-xlsx' reads xlsx, but the input is a ZIP archive that is truncated or
    corrupt: no end of central directory record (truncated, or not a ZIP archive).`
- JSON, CSV or XLSX **without** a mapping fails with an error naming the registered mappings. The service
  never guesses fields. Without a mapping it recognises these formats only by the ZIP contents of a workbook,
  `metadata.mime_type` (`application/json`, `text/csv`, the XLSX type) or the URL extension, so other text
  is still converted as plain text. Bare JSON or CSV with neither fails with "Cannot determine file type";
  the message says to name a mapping.

Registered with the package (`src/crosscodex_ingestion/mappings/`):

| Mapping ID | Source |
|---|---|
| `owasp-asvs-5.0.0-json` | OWASP ASVS 5.0.0 release JSON (`…_5.0.0_en.json`) |
| `mitre-attack-enterprise-17.1-techniques-xlsx` | MITRE ATT&CK Enterprise v17.1 techniques spreadsheet (sheet `techniques`) |
| `nist-sp800-53r5-catalog-xlsx` | NIST SP 800-53 Rev. 5 control catalog spreadsheet |

### How nodes become elements

- A node with children becomes a `heading` whose `content` is its title and whose `level` is its depth
  (top level = 1, at most 100). If it also has text, a `paragraph` with that text and `id: null` follows it.
  Its children follow, in source order, with `parent_id` set to the heading.
- A node without children becomes a `paragraph`. Its `content` is its text, or its title when it has no
  text; `title` is set when it has both.
- Every node's element carries `id` (the source ID) and `properties` (possibly `{}`).
- Elements are in source order, depth first, so a parent always comes before its children, even when a
  table lists a child row above its parent.
- `metadata.title` is the mapping's `title`; `metadata.source.unmapped_fields` lists source fields
  (JSON keys on nodes, or column headers) the mapping didn't use.

What a structured conversion guarantees, beyond 1.0:
- Every node has a non-blank, unique ID; a node with children has a title; a leaf has a title or text.
- Every parent named by a parent column or the ID-prefix rule exists, and parents form no cycle.
- Nothing is dropped. Only completely blank table rows are skipped. Any problem fails the whole conversion.

### Writing a mapping

A mapping is a YAML file named `<id>.yaml`. It is validated against
[`src/crosscodex_ingestion/schemas/mapping-v1.schema.json`](src/crosscodex_ingestion/schemas/mapping-v1.schema.json),
which rejects unknown keys and duplicate keys. Include the publisher's release in the `id`: a new release
gets a new mapping.

A **tree** (JSON):

```yaml
mapping_version: 1
id: owasp-asvs-5.0.0-json
title: OWASP Application Security Verification Standard 5.0.0
source:
  format: json
  release: https://github.com/OWASP/ASVS/releases/download/v5.0.0_release/OWASP_Application_Security_Verification_Standard_5.0.0_en.json
nodes:
  select: Requirements   # dotted object keys from the root to the array of top-level nodes; "" if the root is that array
  children: Items        # key of each node's child array, at every depth (optional)
  id: Shortcode
  title: Name
  text: Description
  properties:            # output name (lowercase identifier) -> source field (optional)
    level: L
    short_name: ShortName
```

A **table** (CSV or XLSX), with the parent in a column:

```yaml
mapping_version: 1
id: mitre-attack-enterprise-17.1-techniques-xlsx
title: MITRE ATT&CK Enterprise v17.1 techniques
source:
  format: xlsx
  release: https://attack.mitre.org/docs/attack-excel-files/v17.1/enterprise-attack/enterprise-attack-v17.1-techniques.xlsx
  sheet: techniques
  header_row: 1          # 1-based row with the column names (default 1)
rows:
  id: ID
  title: name
  text: description
  parent:
    column: sub-technique of   # a blank cell makes the row top-level
  properties:
    url: url
```

or with the parent derived from the ID: `parent: {id_prefix: {separator: "("}}` cuts the ID at the last
separator (`AC-2(1)` -> `AC-2`); an ID without the separator is top-level. A derived parent that no row has
is an error, never a silent top-level row. Without `parent`, every row is top-level.

A **denormalized table**, where every row repeats its chapter and section in their own columns, uses
`levels` instead of `parent` (they cannot be combined). List one `id`/`title` column pair per level,
outermost first; each distinct level ID becomes one heading, nested in that order, and each row nests under
its last level. Level nodes appear before the first row that uses them. The OWASP ASVS 5.0.0 CSV
(`owasp-asvs-5.0.0-csv`) is mapped this way:

```yaml
rows:
  levels:
    - {id: chapter_id, title: chapter_name}
    - {id: section_id, title: section_name}
  id: req_id
  text: req_description
```

Every row must fill every level column. A level ID must always have the same title, parent and level,
and must not equal a row's `id`; otherwise the error names both rows. `title` is optional when `levels` is set.

A **one-column-per-level table**, where each row fills exactly one level column and the ID is inside the
cell, uses `level_cells` (the NIST CSF 2.0 Core spreadsheet, `nist-csf-2.0-core-xlsx`, is mapped this way). It
replaces `id`, `title`, `text`, `levels` and `parent`:

```yaml
rows:
  level_cells:
    columns: [Function, Category, Subcategory]   # outermost first
    separator: ":"                               # fixed text, cut at its first occurrence
    id_brackets: {open: "(", close: ")"}         # optional
  properties:
    informative_references: Informative References
```

A cell is `LABEL<separator>TEXT`. Without `id_brackets` the label is the ID, so `GV.OC-01: The mission…` is
ID `GV.OC-01` with that text. With them, a label that ends with the closing text holds its ID between the
last opening text and that closing text, and the rest of the label is the title, so `GOVERN (GV): The…` is ID
`GV`, title `GOVERN`, text `The…`. A cell without the separator is a label with no text. A row's depth is the
column it fills, and it nests under the nearest earlier row of the level above; it is an error if there is
none, or if a row fills zero or several level columns. A row that repeats an earlier node of the same level
(same ID, same title, no text, same parent) re-enters that node rather than adding one, because the CSF
spreadsheet closes each Function's group with its bare label; any other repeated ID is a duplicate. No
pattern matching is involved: only the fixed separator and bracket text.

| Key | Meaning |
|---|---|
| `mapping_version` | `1`. |
| `id`, `title` | Registered ID (lowercase letters and digits joined by `.` or `-`) and source name. |
| `source.format` | `json` (needs `nodes`), `csv` or `xlsx` (need `rows`). |
| `source.release` | `https://` URL of the publisher file the mapping was written for. |
| `source.delimiter` | CSV only: one character other than `"` or a line break, default `,`. CSV must be UTF-8 (a BOM is allowed); the header is row 1. |
| `source.sheet`, `source.header_row` | XLSX only: worksheet name (required; not a chart sheet) and header row (default 1). CSV has no `header_row`; its header is row 1. |
| `id`, `title`, `text` | Source field (JSON key or column header, matched exactly) for each. `id` is required, and `title` or `text`. Each is one whole value or cell: nothing is extracted from part of a cell. |
| `levels` | Tables only, not with `parent`: 1 to 16 `{id, title}` column pairs, outermost first (see above). |
| `level_cells` | Tables only, instead of `id`, `title`, `text`, `levels` and `parent`: `columns` (1 to 16), `separator`, optional `id_brackets` `{open, close}` (see above). |
| `properties` | Up to 64 `name: field` pairs; names match `^[a-z][a-z0-9_]{0,63}$`. |

Values must be strings or numbers. They are trimmed; an integral number loses its fraction (`5.0` -> `"5"`)
and other numbers are written in their shortest form (`1.10` -> `"1.1"`; quote IDs like that in the source);
an empty value is missing; a missing or null property is left out. A boolean, object, array or date is an
error. Paths have no wildcards, filters or patterns.

Try a mapping locally before registering it; this prints the output, or the problems:

```bash
uv run python -m crosscodex_ingestion.structured --mapping my-source-1.0-json.yaml source.json
```

It applies the same input limits (from the same environment variables) but not the worker's resource
limits.

### Registering a mapping

Put reviewed mapping files (`*.yaml`) in a directory and point `MAPPINGS_PATH` at it; they are added to the
packaged ones. At startup the service validates every mapping and **refuses to start** if any is invalid
(with the file, and for YAML errors the line and column), if `MAPPINGS_PATH` is not a directory, or if two
files register the same ID. Restart the service to pick up changes. Each output's `metadata.source.mapping`
and the attestation's byproducts (`mapping_id`, `mapping_sha256`) record the mapping file's SHA-256.

In distributed mode the service and the workers must run the same mapping files: a worker whose mapping is
missing or differs from the one the request was accepted with fails the conversion with
`ERROR_CODE_UNAVAILABLE`; resubmit once the deployment is consistent.

### Errors

Problems in the source fail the conversion with `ERROR_CODE_INVALID_ARGUMENT`. All problems are reported
together, in source order (the first 20 listed, all counted), and each names the node by its location: a JSON
path such as `Requirements[5].Items[1].Items[4]`, `row 37` for CSV (the header is row 1, rows count records),
`sheet "techniques" row 37` for XLSX. Source values are quoted shortened to 64 characters.

```
3 problems in source (mapping owasp-asvs-5.0.0-json): 1) Node at Requirements[5].Items[1].Items[4] has no ID: field 'Shortcode' is missing or blank. 2) …
```

`Error.details` carries `mapping`, `count`, `problem.1` … `problem.N` and `location.1` … `location.N`
(the location of each problem: a JSON path, `row 37`, `sheet "x" row 37`, `header row`, or `source` for the
input as a whole). Only you get the messages: the service's audit stream and logs record the mapping, the
count and the locations, without any value from your source. The problems are:

| Problem | Example message |
|---|---|
| No ID | `Node at Requirements[5].Items[1].Items[4] has no ID: field 'Shortcode' is missing or blank.` |
| Children but no title | `Node 'V6.2' (at Requirements[5].Items[1]) has 12 children but no title: field 'Name' is missing or blank. A node with children becomes a heading, and headings need a title.` |
| Neither title nor text | `Node 'V1.1.1' (at …) has neither a title ('Name') nor text ('Description').` |
| Duplicate ID | `Duplicate ID 'V6.2.5' at <location> and <location>. IDs must be unique across the source.` |
| Dangling parent column | `Sheet "techniques" row 412 (ID 'T1548.002') names parent 'T1548' in column 'sub-technique of', but no row has that ID.` |
| Dangling ID prefix | `Row 88 (ID 'AC-2(1)') has parent 'AC-2' by the ID-prefix rule (separator '('), but no row has that ID.` |
| Cycle | `Parent cycle: 'A' (row 10) > 'B' (row 11) > 'A'.` |
| ID-prefix rule matches nothing | `The ID-prefix rule (separator '(') matches none of the 679 IDs, so nothing would nest. Check the separator; sample IDs: 'T1548', …` |
| Too deep | `Node 'X' (at …) is nested 101 levels deep; at most 100 levels are supported (heading levels 1-100).` |
| Wrong value type | `Field 'L' at Requirements[0] is a boolean; IDs, titles, text and properties must be strings or numbers.` |
| Path, sheet or column not found | `Path 'Requirements' not found: the root object has no key 'Requirements'. Its keys: 'Name', …` / `Sheet …` / `Column 'tactics' not found in the header row. Columns: …` |
| Bad header | blank or repeated column names, or a value in a column without a header |
| Malformed input | invalid UTF-8 or JSON (with line and column), duplicate JSON keys, `NaN`/`Infinity`, NUL in CSV, malformed CSV quoting, a file that is not a complete workbook (with what is missing), unsupported ZIP features, encrypted workbooks, XML entity declarations |

A source over a limit fails with `ERROR_CODE_RESOURCE_EXHAUSTED` and a message naming the setting, for
example `The JSON input is nested deeper than MAX_JSON_DEPTH (64) levels.` A limit message that names part
of your source (the ZIP entry in the compression-ratio message) reaches the audit stream and logs without
it.

### Limits

Structured input is untrusted. It runs in its own sandboxed worker subprocess, which imports no Docling or
torch and so has much lower resource limits than the Docling worker. `MAX_DOCUMENT_SIZE`, `MAX_URL_BYTES`,
`MAX_EXTRACTION_LENGTH`, `RLIMIT_FSIZE_MB` and `ALLOWED_FORMATS` apply as for documents.

| Variable | Default | Limit |
|---|---|---|
| `MAX_JSON_DEPTH` | `64` | JSON nesting, checked before parsing |
| `MAX_STRUCTURED_NODES` | `100000` | Nodes (JSON objects or table rows) in a source |
| `MAX_TABLE_ROWS` | `100000` | Data rows read from a table (blank ones included) |
| `MAX_TABLE_COLUMNS` | `256` | Columns in the header row |
| `MAX_CELL_LENGTH` | `32767` | Characters in one value (Excel's cell limit) |
| `MAX_ID_LENGTH` | `256` | Characters in one ID |
| `MAX_XLSX_ENTRIES` | `10000` | Files in the workbook's ZIP archive |
| `MAX_XLSX_UNCOMPRESSED_BYTES` | `268435456` | Total uncompressed size of the workbook (256 MB) |
| `MAX_XLSX_COMPRESSION_RATIO` | `100` | Uncompressed / compressed size of any one ZIP entry |
| `STRUCTURED_MAX_PROCESSING_TIME` | `300` | Wall-clock seconds per structured conversion |
| `STRUCTURED_RLIMIT_AS_MB` | `4096` | Address space of the structured worker (MB; `0` = none) |
| `STRUCTURED_RLIMIT_CPU` | `300` | CPU-seconds of the structured worker (`0` = none) |

The full ASVS 5.0.0 JSON, ATT&CK v17.1 techniques and SP 800-53r5 catalog convert in 0.5 to 1.6 s each
within these limits.

### Proto change

`ConvertDocumentRequest.mapping` is field 5 of `proto/crosscodex/v1/ingestion.proto`. It is additive and
wire-compatible; the copy of the proto in the CrossCodex Go monorepo needs the same field to send it.

## Docling models

The conversion worker runs in a sandboxed subprocess with no network access to model hubs
(`HF_HUB_OFFLINE=1`), so Docling's layout, table, and OCR models must already be on disk.

```bash
docling-tools models download -o <dir>
export DOCLING_ARTIFACTS_PATH=<dir>
```

The server **refuses to start** if `DOCLING_ARTIFACTS_PATH` is unset or not a directory. The container
images download the models at build time (`/opt/docling-models` in the standard image,
`/opt/app-root/docling-models` in the FIPS image) and set `DOCLING_ARTIFACTS_PATH` for you.

The images fetch the models with `scripts/download_models.py`, not `docling-tools`: the CLI has no revision
option and Docling requests the moving `main` ref for most of its models, so pinning the docling package does
not fix their contents. The script pins each Hugging Face repository to a commit SHA (edit the table in the
script to bump one). RapidOCR checkpoints come from URLs fixed by the installed Docling/RapidOCR versions.
Both images install exactly the versions in `uv.lock`, hash-checked, so the models and Docling stay matched.

## Environment Variables

| Variable                                                                  | Default             | Description                                                                                                                                                                                                                      |
|---------------------------------------------------------------------------|---------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `DOCLING_ARTIFACTS_PATH`                                                  | (required)          | Directory with pre-downloaded Docling models; server refuses to start without it                                                                                                                                                 |
| `GRPC_PORT`                                                               | `8080`              | gRPC server port                                                                                                                                                                                                                 |
| `METRICS_PORT`                                                            | `9090`              | Prometheus metrics port                                                                                                                                                                                                          |
| `EXECUTION_MODE`                                                          | `embedded`          | `embedded` or `distributed` (NATS)                                                                                                                                                                                               |
| `MAX_CONCURRENT_CONVERSIONS`                                              | `4`                 | Conversions running at once (each is a separate subprocess)                                                                                                                                                                      |
| `SHUTDOWN_GRACE_SECONDS`                                                  | `25`                | Seconds SIGTERM gives in-flight conversions to finish (`>= 0`); see "Graceful shutdown"                                                                                                                                          |
| `STATE_BACKEND`                                                           | `memory`            | `memory` or `redis`                                                                                                                                                                                                              |
| `STORAGE_BACKEND`                                                         | `filesystem`        | `filesystem` or `s3`                                                                                                                                                                                                             |
| `STORAGE_PATH`                                                            | `/data/documents`   | Filesystem storage root                                                                                                                                                                                                          |
| `ALLOWED_FORMATS`                                                         | `pdf,docx,html,txt,json,csv,xlsx` | Comma-separated list of allowed formats (`json`, `csv` and `xlsx` also need a mapping)                                                                                                                             |
| `MAPPINGS_PATH`                                                           | (empty)             | Directory of extra structured-source mappings (`*.yaml`); every mapping is validated at startup (see "Structured sources", which also lists the structured input limits)                                                         |
| `HTML_PROFILES_PATH`                                                      | (empty)             | Directory of extra HTML profiles (`*.yaml`); every profile is validated at startup (see "HTML profiles")                                                                                                                         |
| `MAX_DOCUMENT_SIZE`                                                       | `52428800`          | Maximum uploaded document size in bytes (50 MB)                                                                                                                                                                                  |
| `MAX_URL_BYTES`                                                           | `104857600`         | Maximum bytes to fetch from URLs (100 MB)                                                                                                                                                                                        |
| `URL_TIMEOUT`                                                             | `30`                | URL fetch timeout in seconds                                                                                                                                                                                                     |
| `MAX_REDIRECTS`                                                           | `5`                 | Maximum redirects followed when fetching a URL                                                                                                                                                                                   |
| `MAX_PAGES`                                                               | `1000`              | Documents with more pages fail (never silently truncated)                                                                                                                                                                        |
| `MAX_EXTRACTION_LENGTH`                                                   | `10485760`          | Maximum serialized output size in bytes (10 MB); larger output fails                                                                                                                                                             |
| `MAX_PROCESSING_TIME`                                                     | `3600`              | Wall-clock limit per conversion in seconds                                                                                                                                                                                       |
| `RLIMIT_CPU`                                                              | `21600`             | CPU-seconds limit for the conversion subprocess                                                                                                                                                                                  |
| `RLIMIT_AS_MB`                                                            | `65536`             | Virtual address space limit for the conversion subprocess (MB)                                                                                                                                                                   |
| `RLIMIT_FSIZE_MB`                                                         | `100`               | Maximum size of any file the conversion subprocess writes (MB)                                                                                                                                                                   |
| `ATTESTATION_ENABLED`                                                     | `true`              | Enable in-toto attestation generation. When `true`, both key paths below are required or the service refuses to start. `false` is for development only (conversions are unattested; a warning is logged)                         |
| `ATTESTATION_PRIVATE_KEY_PATH`                                            | (empty)             | PEM private key for attestation signing (ECDSA P-256)                                                                                                                                                                            |
| `ATTESTATION_PUBLIC_KEY_PATH`                                             | (empty)             | PEM public key matching the private key; startup fails if they are not a pair                                                                                                                                                    |
| `FIPS_MODE`                                                               | `false`             | Require the attestation key to be ECDSA on an approved curve                                                                                                                                                                     |
| `GRPC_TLS_MODE`                                                           | `mutual`            | `mutual` (require a client certificate) or `off` (plaintext, unauthenticated; development and tests only, logs a warning)                                                                                                        |
| `GRPC_TLS_CERT`                                                           | (empty)             | Server certificate (PEM); required for `mutual`                                                                                                                                                                                  |
| `GRPC_TLS_KEY`                                                            | (empty)             | Server private key (PEM); required for `mutual`                                                                                                                                                                                  |
| `GRPC_TLS_CA`                                                             | (empty)             | CA (PEM) that signs client certificates; required for `mutual`                                                                                                                                                                   |
| `GRPC_ALLOWED_CLIENTS`                                                    | (empty)             | Comma-separated client certificate identities (subject CN or DNS SAN) allowed to call the service; empty accepts any certificate signed by `GRPC_TLS_CA`. Rejected with `PERMISSION_DENIED`; not usable with `GRPC_TLS_MODE=off` |
| `NATS_URL`                                                                | (empty)             | NATS server URL (e.g., `nats://localhost:4222`)                                                                                                                                                                                  |
| `REDIS_URL`                                                               | (empty)             | Redis URL for state backend                                                                                                                                                                                                      |
| `S3_ENDPOINT`, `S3_BUCKET`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_REGION` | (see `config.py`)   | S3 storage backend settings                                                                                                                                                                                                      |

See `src/crosscodex_ingestion/config.py` for the full list.

### Limits and resource sizing

The defaults are sized from measurements of real documents (Docling offline, models pre-downloaded):

| Document         | Pages | Wall time | CPU time | VmPeak  | RSS peak |
|------------------|-------|-----------|----------|---------|----------|
| NIST CSF 2.0     | 32    | 44 s      | 186 s    | ~40 GB  | 2.7 GB   |
| NIST SP 800-53r5 | 492   | 807 s     | 3221 s   | 43.2 GB | 4.3 GB   |

- **Time**: Docling uses several threads, and `RLIMIT_CPU` counts CPU time across all threads (CPU is
  roughly 4-6x wall time). `MAX_PROCESSING_TIME=3600` and `RLIMIT_CPU=21600` leave headroom for documents
  several times larger than SP 800-53r5. A conversion that hits `RLIMIT_CPU` fails with a message naming
  the limit and its value.
- **Memory**: `RLIMIT_AS` bounds *virtual address space only*. Importing torch alone needs more than 4 GB of
  address space and conversions peak at ~40 GB `VmPeak` while using only a few GB resident, so the default is
  64 GB. It is not a memory cap. **The real memory bound is the container memory limit**: allow at least
  6 GB per concurrent conversion (`MAX_CONCURRENT_CONVERSIONS`). A worker killed by `SIGKILL` (most likely
  the OOM killer) or `SIGSEGV` (most likely `RLIMIT_AS_MB` set too low) is logged with that signal and
  its likely cause.
- **Pages and output size**: `MAX_PAGES` and `MAX_EXTRACTION_LENGTH` fail the conversion with a clear
  message instead of truncating.

### Graceful shutdown

On SIGTERM or SIGINT the service stops accepting RPCs and then drains conversions within a single budget of
`SHUTDOWN_GRACE_SECONDS` (default 25):

1. Documents still waiting for a conversion slot are marked `FAILED` immediately.
2. Running conversions may finish within the grace period and complete normally.
3. Conversions still running when it expires are cancelled: the Docling subprocess (and anything it spawned) is
   SIGKILLed, and the document ends `FAILED` with error code `UNAVAILABLE` ("The service shut down during
   conversion; resubmit the document."). An audit event is published for each. Resubmitting is safe: conversion
   uses local open-weight models, so a retry costs only CPU time.

Shutdown takes at most `SHUTDOWN_GRACE_SECONDS` plus about 2 s of cleanup. Keep the container runtime's stop timeout
above that, or the runtime SIGKILLs the process mid-cleanup and documents stay `RUNNING`: Kubernetes defaults to 30 s
(`terminationGracePeriodSeconds`), while `podman stop` and `docker stop` default to 10 s. Under podman/docker either
raise the timeout (`--stop-timeout 30`, or `stop_grace_period: 30s` in compose) or lower `SHUTDOWN_GRACE_SECONDS` to
about 7. A 492-page PDF takes about 13.5 minutes, so documents that large are cancelled by any realistic grace
period and must be resubmitted after a restart.

In `distributed` mode this service only publishes jobs to NATS JetStream; the consumer is not part of this
repository, so redelivery of in-flight messages on shutdown (negative acknowledgement) is up to that worker.

### gRPC authentication

The gRPC port uses mutual TLS by default and the service refuses to start without `GRPC_TLS_CERT`,
`GRPC_TLS_KEY` and `GRPC_TLS_CA` (or an explicit `GRPC_TLS_MODE=off`). A client certificate identifies the
calling *service* (for example the CrossCodex gateway), not a tenant: the service still trusts `tenant_id` in
the request, so only issue client certificates to services that enforce tenant isolation themselves, and list
them in `GRPC_ALLOWED_CLIENTS`. TLS settings use the gRPC/OpenSSL defaults; no cryptographic algorithms are
chosen by this service, so the FIPS image inherits whatever its OpenSSL FIPS provider permits.

The container health check only opens a TCP connection to the gRPC port, because a gRPC channel check would
need a client certificate.

### Dev and test certificates

`task certs` runs `scripts/devpki.py`, which writes a throwaway PKI to `.test-output/certs` (override with
`CERTS_DIR=...`): `ca.pem`/`ca.key`, `server.pem`/`server.key` (SANs `localhost`, `127.0.0.1`, `ingestion`,
`crosscodex-ingestion`), `client.pem`/`client.key` (CN `crosscodex-gateway`) and an ECDSA P-256 attestation pair
(`attestation.key`, `attestation.pub`). Key files are mode 0600. Never use this CA outside development.

- **Stale** means any of: a file missing or unparseable; a leftover temp directory from an interrupted run;
  a key file readable by group/other; `ca.pem` not a CA; server/client not issued by the CA or missing the
  right extended key usage; a private key not matching its certificate; a required server SAN missing;
  the attestation keys not a pair; or less than 24 hours of validity left (`--min-valid-hours`).
  `task certs` skips work when the set is fresh and `python scripts/devpki.py --verify` reports why it is not.
- **Regeneration replaces the whole set with a new CA**, never individual files, so every previously issued
  client certificate and any verifier holding the old `attestation.pub` must be re-issued or updated.
- Concurrent runs are safe: generation holds a file lock next to the output directory, re-checks inside it, and
  swaps the finished set in with a rename. pytest generates its own PKI in a temporary directory per run.
- `task compose:up` generates the certs and starts `docker-compose.yml` with mutual TLS and attestation on.
  A one-shot `certs-init` container copies the server certificate, server key and attestation keys into the
  `ingestion-certs` volume, owned by the service user (keys 0400), on every start, so it works for any host
  uid under rootless podman or docker and never serves a stale set. The CA key and client key are not copied.
  `compose:up` passes `--force-recreate`: podman-compose otherwise keeps containers whose compose config is
  unchanged, so after regenerating the PKI the service would keep trusting the old CA. Verifiers use
  `.test-output/certs/attestation.pub`; callers use `client.pem`, `client.key` and `ca.pem`.

### Attestation keys

Attestations are signed with the key at `ATTESTATION_PRIVATE_KEY_PATH` and are verifiable with the key at
`ATTESTATION_PUBLIC_KEY_PATH`. There is no ephemeral-key fallback. Generate a pair with, for example:

```bash
openssl ecparam -name prime256v1 -genkey -noout | openssl pkcs8 -topk8 -nocrypt -out attestation.key
openssl pkey -in attestation.key -pubout -out attestation.pub
```

Keys on curves the signer (securesystemslib) does not support, such as secp256k1, are rejected at startup.

### Identifiers

`tenant_id` must match `^[a-z][a-z0-9-]{1,50}[a-z0-9]$` (3-52 characters: lowercase letters, digits, hyphens;
starts with a letter, does not end with a hyphen). It is used in storage paths and NATS subjects, so other
values are rejected with `INVALID_ARGUMENT`. `GetDocument` requires the `document_id` returned by
`ConvertDocument` (a UUID).

## Container Build

```bash
# Standard build (downloads torch and the Docling models; slow and large)
podman build -t crosscodex-ingestion:latest .

# FIPS build (UBI9)
podman build -t crosscodex-ingestion:fips -f Dockerfile.fips .

# Run the container (see "Limits and resource sizing" for the memory limit)
podman run --memory 8g -p 8080:8080 \
  crosscodex-ingestion:latest
```

Without a volume, converted output is stored inside the container (`/data/documents`) and lost when it is
removed. Mount a volume at `/data/documents` to keep it. The container needs the TLS and attestation key
files mounted (see "gRPC authentication" and "Attestation keys"); for a throwaway local run add
`-e GRPC_TLS_MODE=off -e ATTESTATION_ENABLED=false`.

`task test:container` builds both images, runs each image's `HEALTHCHECK` against a service started with mutual
TLS and attestation, and converts `tests/fixtures/documents/sample.pdf` in a container started with
`--network=none`. It needs `podman`, is slow (it downloads torch and the models), and runs in CI on every PR.
If `podman build` fails with "mounting an overlay over build context directory" (podman nested inside another
container), point `TMPDIR` at a directory on a non-overlay filesystem.

## Architecture

The service has three primary components:

1. **gRPC Service** (`service.py`, `server.py`): Handles `ConvertDocument`, `GetDocument`, and `ListDocuments` RPCs.
2. **Dispatch Layer** (`dispatch.py`): Routes jobs to either embedded workers or NATS JetStream.
3. **Worker** (`worker.py`): Fetches, validates, converts, extracts, stores, and attests documents.

```
gRPC Request → Dispatch → Worker → [Converter → Extractor | Structured] → Output schema check → Storage → State → Response
```

**Security Modules:**
- `security.py`: SSRF prevention, URL validation, content type detection, resource limit enforcement. URL fetches connect directly and ignore `HTTP_PROXY`/`HTTPS_PROXY`/`ALL_PROXY`: the address actually connected to is checked against the blocked ranges, and through a proxy that check would only see the proxy.
- `converter.py`: Async subprocess-based Docling execution with rlimits and timeout; the subprocess's process group is killed on timeout or cancellation
- `extractor.py`: Structured element extraction from Docling output
- `html_backend.py`: Docling HTML backend that drops navigation and, on pages with a main landmark, page-level `<header>`/`<footer>` before conversion, and applies the HTML profile a page matches (design: [docs/html-profiles.md](docs/html-profiles.md))
- `output_schema.py`: Validates every output against the versioned output schema before it is stored (design: [docs/output-schema.md](docs/output-schema.md))
- `quality.py`: Measures structure signals (`metadata.structure`) from the extracted elements
- `html_profile.py`, `html_profiles/`: Load, validate and register HTML profiles at startup; the packaged profiles are the `*.yaml` files in `html_profiles/` (maintainer notes: [docs/html-profiles.md](docs/html-profiles.md))
- `mapping.py`: Loads, validates and registers structured-source mappings at startup
- `structured.py`: Reads JSON, CSV and XLSX through a mapping into elements, in the structured worker subprocess (design: [docs/structured-sources.md](docs/structured-sources.md))

**Supporting Modules:**
- `state.py`: Document record state management (async interface; memory, or Redis via `redis.asyncio`)
- `storage.py`: Blob storage (filesystem or S3)
- `attestation.py`: In-toto link metadata generation
- `audit.py`: NATS JetStream audit event publisher
- `config.py`: Pydantic settings with environment variable overrides

## Testing

```bash
# Unit tests (fast, no external dependencies)
task test

# Integration tests (slow; downloads the pinned models on first run, see `task test:models`)
task test:integration

# Container tests (needs podman; builds both images, health check, offline conversion)
task test:container

# Lint (ruff + ruff format --check + mypy)
task lint
```

## gRPC API

See `proto/crosscodex/v1/ingestion.proto` for the full API definition.

**Key RPCs:**
- `ConvertDocument`: Submit a document for asynchronous conversion.
- `GetDocument`: Retrieve conversion status and results.
- `ListDocuments`: Paginate through documents for a tenant.

## Development Workflow

1. Make changes to `src/crosscodex_ingestion/`
2. Add tests to `tests/unit/` or `tests/integration/`
3. Run `task lint` and `task test`
4. Commit changes (see commit message style in git log)
5. Build container with `podman build -t crosscodex-ingestion:latest .`

## Support

- Issues: [GitHub Issues](https://github.com/complytime-labs/crosscodex-ingestion/issues)
- Docling: [Document extraction library](https://github.com/DS4SD/docling)
