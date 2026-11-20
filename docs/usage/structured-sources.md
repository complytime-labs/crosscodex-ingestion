# Structured sources

For operators who convert JSON, CSV or XLSX sources and write or register mappings. Back to the [README](../../README.md); how it works inside is in [docs/structured-sources.md](../structured-sources.md).

Control frameworks are often published as data: OWASP ASVS as JSON, MITRE ATT&CK and NIST SP 800-53 as
spreadsheets. The service reads JSON, CSV and XLSX through a **mapping**, a small reviewed YAML file that
says where the nodes are and which fields hold each node's ID, title, text and properties. The output is the
same element format as for documents, plus the source IDs and nesting.

## Requesting a conversion

Set `mapping` on `ConvertDocumentRequest` to the ID of a mapping registered on the server, and send the
file as `content` or `source_uri` as usual. This example sets `$G` as in
[Convert a document](../../README.md#convert-a-document) and sends the
[ASVS 5.0.0 release JSON](https://github.com/OWASP/ASVS/releases/download/v5.0.0_release/OWASP_Application_Security_Verification_Standard_5.0.0_en.json):

```bash
printf '{"tenant_context": {"tenant_id": "default"}, "content": "%s", "mapping": "owasp-asvs-5.0.0-json"}' \
  "$(base64 -w0 OWASP_Application_Security_Verification_Standard_5.0.0_en.json)" |
  $G -d @ localhost:8080 crosscodex.v1.IngestionService/ConvertDocument
```

The request goes in on stdin (`-d @`) because a shell limits a single argument to 128 KiB, and the ASVS file is
larger once base64-encoded.

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
  `metadata.mime_type` (`application/json`, `text/csv`, the XLSX type) or the URL extension, so JSON- or
  CSV-looking text sent as `text/plain` (or from a `.txt` URL) is still converted as plain text. Bare JSON
  or CSV with neither fails with "Cannot determine file type"; the message says to name a mapping.

Registered with the package (`src/crosscodex_ingestion/mappings/`):

| Mapping ID | Source |
|---|---|
| `owasp-asvs-5.0.0-json` | OWASP ASVS 5.0.0 release JSON (`…_5.0.0_en.json`) |
| `mitre-attack-enterprise-17.1-techniques-xlsx` | MITRE ATT&CK Enterprise v17.1 techniques spreadsheet (sheet `techniques`) |
| `nist-sp800-53r5-catalog-xlsx` | NIST SP 800-53 Rev. 5 control catalog spreadsheet |
| `nist-csf-2.0-core-xlsx` | NIST Cybersecurity Framework 2.0 Core spreadsheet from the CSF 2.0 Reference Tool (sheet `CSF 2.0`) |
| `owasp-asvs-5.0.0-csv` | OWASP ASVS 5.0.0 release CSV (`…_5.0.0_en.csv`) |

## How nodes become elements

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

## Writing a mapping

A mapping is a YAML file named `<id>.yaml`. It is validated against
[`src/crosscodex_ingestion/schemas/mapping-v1.schema.json`](../../src/crosscodex_ingestion/schemas/mapping-v1.schema.json),
which rejects unknown keys; the loader rejects duplicate keys. Include the publisher's release in the `id`: a
new release gets a new mapping.

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
`GV`, title `GOVERN`, text `The…`. A cell without the separator is a label with no text.

A row's depth is the
column it fills, and it nests under the nearest earlier row of the level above; it is an error if there is
none, or if a row fills zero or several level columns.

A row that repeats an earlier node of the same level
(same ID, same title, no text, same parent) re-enters that node rather than adding one, because the CSF
spreadsheet closes each Function's group with its bare label; any other repeated ID is a duplicate. The
mapping does not match patterns: it uses only the fixed separator and bracket text.

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

## Registering a mapping

Put reviewed mapping files (`*.yaml`) in a directory and point `MAPPINGS_PATH` at it; they are added to the
packaged ones. At startup the service validates every mapping and **refuses to start** if any is invalid
(with the file, and for YAML errors the line and column), if `MAPPINGS_PATH` is not a directory, or if two
files register the same ID. Restart the service to pick up changes. Each output's `metadata.source.mapping`
and the attestation's byproducts (`mapping_id`, `mapping_sha256`) record the mapping file's SHA-256.

In distributed mode the service and the workers must run the same mapping files: a worker whose mapping is
missing or differs from the one the request was accepted with fails the conversion with
`ERROR_CODE_UNAVAILABLE`; resubmit once the deployment is consistent.

## Errors

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

## Limits

Structured input is untrusted. It runs in its own resource-limited worker subprocess (not a security sandbox: it runs as the service's uid), which imports no Docling or
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

The full sources of the `owasp-asvs-5.0.0-json`, `mitre-attack-enterprise-17.1-techniques-xlsx` and
`nist-sp800-53r5-catalog-xlsx` mappings convert in 0.5 to 1.6 s each within these limits.

## Proto change

`ConvertDocumentRequest.mapping` is field 5 of `proto/crosscodex/v1/ingestion.proto`. It is additive and
wire-compatible; the copy of the proto in the CrossCodex Go monorepo needs the same field to send it.
