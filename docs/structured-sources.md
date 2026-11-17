# Structured sources: design notes

For maintainers. How to request a conversion, write and register a mapping, and read the errors is in the
README section "Structured sources"; this page explains how it works and why.

## Flow

| Step | Module | Process | What happens |
|---|---|---|---|
| Startup | `mapping.load_registry` (from `server.serve`) | service | Loads `mappings/*.yaml` (package data) and `MAPPINGS_PATH/*.yaml`, validates each against `schemas/mapping-v1.schema.json`, hashes each file. Any problem: `SystemExit`. |
| Submission | `service.ConvertDocument` | service | `request.mapping` must be registered (else `INVALID_ARGUMENT`, synchronously). Records `mapping_id` and `mapping_sha256` in the job metadata. |
| Dispatch | `dispatch.py` | service | The metadata carries the mapping, through NATS as well. |
| Resolve | `worker._request_mapping` | worker | Looks the mapping up in the worker's own registry; a missing mapping or another hash is `MappingSkewError` (`UNAVAILABLE`, retryable). |
| Fetch / format | `worker.run_conversion` (`_check_format`, in a thread), `security.confirm_structured_format` | worker | With a mapping its format is the expected one: uploads need no MIME type, and URL downloads skip the 512-byte sniff (`fetch_url(expected_format=…)`); the complete bytes must confirm the format, which must be in `ALLOWED_FORMATS`. Without a mapping, a JSON/CSV/XLSX input is recognised only by the workbook's ZIP contents, `metadata.mime_type` or the URL extension, and fails with the list of registered mappings; bare JSON or CSV without either fails as "Cannot determine file type", whose message says JSON and CSV need a mapping. |
| Convert | `converter.convert_structured` -> `structured.run_job` | sandboxed structured worker | Reader -> `build_tree` -> `emit_elements` (below). |
| Validate, store, attest | `worker.run_conversion` | worker | The same `validate_output` as for documents; the attestation's byproducts add `mapping_id` and `mapping_sha256`. |

Inside the structured worker (`structured.convert_source`):

1. **Read.** `read_json`, `read_csv` or `read_xlsx` parse the bytes and return `SourceNode`s in source order:
   a location string and the raw fields. JSON nodes also carry the index of their enclosing node.
2. **Build the tree.** `build_tree` coerces each node's ID, title, text and properties, resolves parents
   (JSON nesting, the parent column, or the ID-prefix rule) after all IDs are known, so a child may come
   before its parent, and checks IDs, titles, dangling parents, cycles (`_report_cycles`) and depth.
3. **Emit.** `emit_elements` walks the roots depth first, in source order, so every `parent_id` names an
   earlier element. Traversals are iterative: depth is bounded by checks, not by Python's recursion limit.

## Decisions

**Mappings are reviewed files, named by ID.** No inline mappings and no mapping URLs: a mapping decides
what the service extracts and stores, so it gets the review that code gets, and fetching one would be
another SSRF surface. The registry is fixed at startup, so a request can only name a mapping that was valid
then. A file must be named `<id>.yaml` (not in the original design): it makes the ID findable from the
directory listing and rules out two files claiming one ID by accident (that is also an error).

**The mapping hash travels with the job.** `mapping_id` and `mapping_sha256` go into the job metadata
rather than new dispatch parameters, so they reach a NATS consumer with no payload change and stay on the
`DocumentRecord`. In embedded mode the service and the worker share one registry and the check can't fail;
it exists for distributed mode and rollouts, where failing retryable is the only safe answer.

**Its own worker, its own limits.** `convert_structured` shares the subprocess lifecycle with
`convert_document` (`_run_worker`: temp dir, own session, rlimits, timeout, process-group kill, output size
check; `_raise_for_exit`: signal and exit-code reporting) but runs `_STRUCTURED_WORKER_SCRIPT`, which imports
only `structured.py` (no Docling, no torch; a unit test asserts this). So it gets far lower defaults:
4 GB address space and 300 CPU-seconds instead of 64 GB and 6 hours. The job (validated mapping document,
its hash, the input limits) goes on stdin; the worker validates the mapping again.

**Input errors reach the client; other failures don't.** A generic worker failure is an internal error
with a generic message (CWE-209). Source problems must instead be returned in full, so the worker exits with
`INPUT_ERROR_EXIT_CODE` (4) and writes `error.json` (`kind` `source` or `limit`, `message`, `details`). The
worker parses untrusted input, so the service treats that file as untrusted too: it reads at most 64 KiB,
checks the shape and that `details.mapping` is the requested mapping, and **rebuilds** the
`StructuredSourceError` from the listed problems and their locations instead of passing the worker's
message through. A limit
error's message is passed through. Each problem and the limit message may be at most 2048 characters
(`_MAX_ERROR_TEXT`; generated ones are far shorter), so a compromised worker can't flood the client or the
logs. Anything else is `ConversionError`.

**Fail only, and report everything found.** A problem never drops a node or makes it top-level; it fails
the conversion. Problems are collected rather than raised at the first one, because a mapping author fixes
a source or mapping in one pass. They are reported in source order even though checks run in phases
(`_Problems.position` files each problem under the node being checked), the first 20 listed, all counted.
Only those 20 are kept (a bounded heap), so a source with millions of problems can't exhaust memory.
A follow-on problem is suppressed when it would only restate one already reported (a wrong-typed title
isn't also "missing"). Reader problems that make further reading meaningless (invalid JSON, path or column
not found, a bad header) stop at once. Limits are fatal at once and are `RESOURCE_EXHAUSTED`, not
`INVALID_ARGUMENT`, like the document limits. Fully blank table rows are skipped: they carry nothing, and
spreadsheets often end with formatted empty rows.

**Element shape.** A node with children is a heading (`content` = title, `level` = depth); its text, if
any, becomes a following paragraph with `id: null`, because `id` must be unique and the heading already has
it. A leaf's `content` is its text, or its title when it has no text; `title` is set only when both exist, so
the title is never stored twice. Node elements always carry `id` and `properties` (possibly `{}`), so a
reader doesn't need to tell absent from empty. `metadata.title` is the mapping's `title`.

**Values.** Only strings and numbers are accepted: a boolean or a date has no canonical text, and an object
or array means the mapping points at the wrong field. Integral floats become integers because spreadsheets
store `5` as `5.0`. Echoed values go through `repr` after truncation to 64 characters, so newlines and
control characters can't forge log lines or messages; IDs therefore appear quoted (`Node 'V6.2' …`).

**Format detection stays conservative.** Without a mapping, JSON and CSV are recognised only from the
Content-Type or the URL extension, never from the bytes, so text starting with `[` or containing commas is
still `txt` and still converts. XLSX and DOCX are told apart by the ZIP directory (`xl/workbook.xml`,
`word/document.xml`), which also catches a DOCX renamed `.xlsx`. With a mapping, the mapping's format is
expected and `confirm_structured_format` checks the whole file. `fetch_url(expected_format=…)` skips the
first-bytes sniff, because release assets are commonly served as `application/octet-stream` from URLs
without an extension (GitHub release downloads redirect to such URLs).

## Security model

All structured input is untrusted. Defences, in the order they apply:

- **Service process** (not sandboxed): `MAX_DOCUMENT_SIZE` / `MAX_URL_BYTES`. Untrusted bytes are never
  handed to `zipfile` here: it walks every record within the directory size the end record declares,
  whatever entry count that record gives, so a forged end record makes it build a million `ZipInfo`
  objects. `zipdir.read_zip_directory` reads only the names, stops after 10 000 records
  (`security._MAX_SNIFF_ZIP_ENTRIES`), refuses ZIP64 (real workbooks below 4 GB and 65 535 entries don't use
  it) and ignores extract versions, since nothing is decompressed. It must read exactly what `zipfile` (and
  so openpyxl) would: `zipfile` ignores the end record's offset and reads the directory-size bytes before
  it, so zipdir requires the directory to end exactly at the end record (no prepended or appended data),
  and refuses names `zipfile` would rewrite (a NUL, which truncates, or an Info-ZIP Unicode Path field
  0x7075, which renames) instead of emulating them. All real workbooks and documents checked meet these
  rules. Sniffing and confirmation run in a thread
  (`asyncio.to_thread`), not on the event loop. The CSV-vs-JSON check is a match on JSON's first token, not
  a parse. Nothing else is parsed here.
- **Worker sandbox**: separate process and session, `STRUCTURED_RLIMIT_AS_MB`, `STRUCTURED_RLIMIT_CPU`,
  `RLIMIT_FSIZE_MB`, `STRUCTURED_MAX_PROCESSING_TIME`, minimal environment.
- **JSON**: strict UTF-8; a depth scan before parsing (`MAX_JSON_DEPTH`), one left-to-right tokenizing
  pass in which brackets inside strings don't count and an unterminated string ends the scan (retrying
  the string pattern from every later quote would be quadratic), so `json` never recurses deeply; a
  `RecursionError` is still caught as a depth-limit error, as a backstop; duplicate keys rejected
  (`object_pairs_hook`), because which one wins is parser-specific; `NaN`/`Infinity` rejected
  (`parse_constant`); oversized integers (Python's int-to-str limit) are an input error.
- **XLSX**: before zipfile touches it, `zipdir` counts the directory records (`MAX_XLSX_ENTRIES`), and
  zipfile must then list the same number of entries; an
  extract version zipfile doesn't support is an input error. Before openpyxl touches it, the ZIP directory
  is checked: entry count, total uncompressed size,
  per-entry compression ratio, encryption flag. `zipfile` never returns more than an entry's declared size,
  so the declared sizes bound the decompression work. openpyxl then runs with `read_only=True`,
  `data_only=True` (cached values, no formulas evaluated) and `keep_links=False`, and rows are iterated with
  `values_only=True`, stopping past `MAX_TABLE_ROWS`. The sheet's declared `<dimension>` is discarded
  (`reset_dimensions`): read-only openpyxl otherwise truncates every row and column to it (a stale one
  would silently drop data) and pads every row to it (`A1:XFD100000` would cost 16 384 cells per row). Only
  worksheets can be read; a chart sheet is reported as such.
- **XML entities**: openpyxl 3.1.5 uses `defusedxml` when importable (`openpyxl.DEFUSEDXML`) for `iterparse`
  (worksheets, shared strings). Its `fromstring` is defusedxml's, or, when lxml is installed (it is, through
  Docling), lxml's with `resolve_entities=False`. Because of that split, `_require_defused_openpyxl` checks
  the attribute **and** probes both functions with an internal-entity document; if either expands it, the
  worker refuses to read XLSX (`RuntimeError`, an internal error). A workbook with entity declarations is
  reported as such (openpyxl wraps the parser error, so the exception chain is searched).
- **CSV**: `csv.reader` with the mapping's delimiter, `strict=True`, no `Sniffer`; `csv.field_size_limit` is
  `MAX_CELL_LENGTH`; NUL characters rejected; duplicate or blank headers rejected.
- **Everything**: `MAX_STRUCTURED_NODES`, `MAX_TABLE_COLUMNS`, `MAX_CELL_LENGTH`, `MAX_ID_LENGTH`, nesting of
  at most 100 levels; the output size limit; then `validate_output` on the final dict.

**Source values stay with the caller.** Problem messages quote the caller's data (IDs, parent values,
keys). The full error goes to the state record, which only the submitting tenant reads through
`GetDocument`. The audit event and the service log get `StructuredSourceError.redacted()` instead: code,
mapping, count and each problem's location (a JSON path, `row 37`, `sheet "x" row 37`, `header row` or
`source`), which are built from the mapping and positions only. The log line is a WARNING without a
traceback, since the traceback would repeat the message. A limit message that quotes a source value (the
ZIP entry name in the compression-ratio message) carries a `redacted_message` the same way, through the
error file and `DocumentLimitError.redacted_message`; the others quote none and are their own redacted
form.

The mapping files themselves are operator-supplied and reviewed, but are still loaded with a safe YAML
loader (a `yaml.SafeLoader` subclass that also rejects duplicate keys) and validated against a closed schema.

## Deviations from the approved design

- YAML is loaded with a `SafeLoader` subclass instead of `yaml.safe_load`, to reject duplicate keys; it
  constructs the same safe types.
- A mapping file must be named `<id>.yaml`.
- The worker gets the job on stdin rather than as a file, so no extra file needs cleaning up.
- Limit errors are fatal and `RESOURCE_EXHAUSTED` rather than collected problems.
- The defusedxml check probes behaviour as well as `openpyxl.DEFUSEDXML`, because with lxml installed
  openpyxl's `fromstring` is not defusedxml's.
- Without a mapping, JSON is not recognised from its first byte (only from Content-Type or extension), so
  existing plain-text conversions keep working.
- CSV has no `header_row`: its header is always row 1, as the design's CSV options listed.
- `Error.details` carries `location.N` beside each `problem.N`.
- An `id_prefix` separator that occurs in no ID is a problem (it would nest nothing); this was not in the
  design and came from a real run (ATT&CK with `(`).
- The `ALLOWED_FORMATS` default now includes `json,csv,xlsx`; a request still needs a registered mapping.

## Fixtures and tests

`tests/fixtures/structured/<source>/` holds a trimmed excerpt of a real release (`input.*`), its
`PROVENANCE.md` (URL, original SHA-256, license, trim recipe) and `expected.json`, the reviewed output.
`tests/fixtures/structured/NOTICE` carries the attributions the licenses require. The tests use the
registered mappings, not copies:

- `tests/unit/test_structured.py`: golden outputs, every error message, the JSON/XLSX/CSV hardening (the
  zip bomb is built at test time and never committed), the worker entry point and the CLI.
- `tests/unit/test_mapping.py`, `test_security.py`, `test_converter.py`, `test_worker.py`,
  `test_service.py`, `test_server.py`: loading, format confirmation, the worker subprocess and its error
  file, routing and hash skew, submission, startup refusal.
- `tests/integration/test_structured_e2e.py`: the fixtures through the real server, storage and attestation.

To add a source: write and try the mapping with `python -m crosscodex_ingestion.structured`, add it under
`src/crosscodex_ingestion/mappings/`, add a fixture directory as above, and add it to the golden lists in
`test_structured.py` and `test_structured_e2e.py`. `expected.json` is generated from the implementation, so
review it by hand before committing it.
