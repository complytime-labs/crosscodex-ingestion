# Output schema: design notes

For maintainers. The consumer-facing description of the format and its guarantees is the README section
"Output format"; this page explains how the contract is enforced and how to change it.

## Where each part of the output comes from

| Step       | Module                                                                                                             | Process                     | Adds                                                        |
|------------|--------------------------------------------------------------------------------------------------------------------|-----------------------------|-------------------------------------------------------------|
| Extraction | `extractor.py` (documents) or `structured.py` (structured sources, [structured-sources.md](structured-sources.md)) | sandboxed worker subprocess | `elements`, `metadata` except `processing_time_ms`          |
| Collection | `converter.py` (`convert_document`, `convert_structured`)                                                          | service                     | `metadata.processing_time_ms`, `schema_version` (first key) |
| Validation | `worker.py` (`run_conversion`) via `output_schema.validate_output`                                                 | service                     | nothing; raises `OutputSchemaError`                         |
| Storage    | `worker.py`                                                                                                        | service                     | nothing                                                     |

## Decisions

**Validate in the service, right before storage.** The subprocess parses untrusted documents, so its
output is untrusted input to the service. Validating the final dict (after the service adds its own
fields) means the object that is checked is exactly the object that is stored. No validation happens in
the subprocess: it would duplicate the check, and a compromised worker could skip it.

**One schema file, compiled once.** `schemas/structured-output-v1.json` is package data (hatch includes
non-Python files under `src/crosscodex_ingestion/`). `output_schema.py` loads it with
`importlib.resources`, runs `check_schema` and builds one `Draft202012Validator` at import, so a broken
schema fails service start-up, not a conversion.

**One file per major; it validates every released minor.** The schema's `schema_version` is an `enum` of
every released 1.x, not a `const`. Outputs already stored under 1.0 must keep validating after 1.1 ships,
and they do, because a minor only adds optional fields. The version the service emits is the separate
constant `SCHEMA_VERSION` in `output_schema.py`. Import fails if the enum doesn't list it, so the two can't
drift apart.

**Rules JSON Schema can't express are checked in code.** JSON Schema can't relate one array item to
another, or compare a value with an array's length. So unique `element_id`, "a non-null `parent_id` names
an earlier element", "non-null source `id` values are unique" (1.1) and "`metadata.element_count` equals
the number of elements" live in `_reference_violations`. It runs only when the schema passes, so it can rely
on every field being present and typed. Further cross-element rules belong beside it.

**The schema is closed; tolerance is the reader's job.** Every object has `additionalProperties: false`.
The schema states exactly what one version emits, so a field added in code without a schema change fails
the service's own validation and tests instead of reaching consumers unannounced. Forward compatibility
comes from the version policy instead: minors only add optional fields, and readers must ignore fields
they don't know (README "Versions"). The cost: a consumer that validates with the schema needs a copy at
least as new as the output, because a 1.0-era copy rejects fields that 1.1 adds.

**A non-conforming output is `ERROR_CODE_INTERNAL`, with a specific message.** It is a bug in the
service, not in the request, so it is not `INVALID_ARGUMENT`. Unlike other internal errors its message
is returned in full. It is built from locations, schema keywords and schema values. Element content is
tenant data, and the message reaches the client, the logs and the audit stream, so the only values
taken from the output are these:
- An `element_id` or `parent_id`, quoted only when it has the generated form `e-` plus 3 to 30 digits.
  The schema enforces that form, so after the schema passes every id is safe to quote.
- An unexpected property's name, quoted only when it matches `^[a-z_]{1,64}$`. Other unexpected
  properties are only counted.

A source `id` (1.1) is tenant data and is never quoted: a duplicate is reported as "duplicates the id of
elements[N]".

The schema's id pattern ends in `(?![\s\S])` rather than `$`, because Python's `$` also matches before
a trailing newline. The first violation is also returned as `Error.details` (`schema_version`,
`location`, `element_index`, `element_id`).

**Nothing is dropped silently.** The extractor lists what it doesn't emit in `metadata.dropped`:
blank headings, tables whose Markdown export raised or came out empty, and paragraphs the plain-text
fallback skips for having fewer than 5 words. Text before the first heading or section is emitted as
top-level paragraphs (`parent_id: null`), not dropped. `run_conversion` logs each
entry at WARNING with the document id; the subprocess can't log usefully, because its stderr is read
only when it fails. A drop's `reason` names only the exception type, since exception messages may
quote document text.

**Blank headings are skipped without touching the heading stack.** That attaches the content under one to
the nearest non-blank heading above, and it keeps a blank heading from closing the sections above it.

**Markdown is exported only for the fallback, and drops survive it.** `DoclingDocument.export_to_markdown`
renders tables with the same `MarkdownDocSerializer` as `TableItem.export_to_markdown`. When that
serializer raises on a table, the document export raises too; this was reproduced on a real
`DoclingDocument` (`tests/integration/test_table_drop_e2e.py`). So the worker passes `markdown=None`, and
`extract_elements` exports the document's Markdown only when the typed pass yields no elements. If that
export raises, the conversion fails; it is never silent. For the same reason the fallback's Markdown
can't contain a table the typed pass dropped, so `metadata.dropped` is kept when the fallback runs.

## Changing the schema

1. Decide minor or major by the README "Versions" rules. Adding an optional field is minor; anything that
   could break an existing reader is major.
2. For a minor: edit `schemas/structured-output-v1.json`, add the new fields as optional (never to
   `required`), append the new version to the `schema_version` `enum` (never remove one), and bump
   `SCHEMA_VERSION` in `output_schema.py`. For a major: create a new file and `$id` for the new major
   with a fresh `enum`. Keep the old file, so outputs already stored can still be validated, and point
   `output_schema.py` at the new one.
3. Make the producer emit the change, and update `tests/unit/output_samples.py` so it still conforms.
4. Add positive and negative cases to `tests/unit/test_output_schema.py`, and run
   `task test:integration` (`tests/integration/test_output_schema_e2e.py` validates real conversions of
   every fixture document).
5. Update the README "Output format" section: field table, guarantees, and the example.

## 1.1: what structured sources added

`id`, `title`, `properties` on elements and `metadata.source` are optional, so 1.0 outputs still validate and
1.0 readers still work. The Docling path emits none of them: it has no source IDs, and emitting `id: null`
on every element would only add bytes. `title` is forbidden on headings (`"title": false` in the schema's
`then` branch), because a heading's title is its `content`. Property names follow the same identifier rule
as mapping property names; property values are non-blank strings.

## 1.2: structure signals and HTML profiles

`metadata.structure` and `metadata.html_profile` are optional, so 1.0 and 1.1 outputs still validate and
1.1 readers still work: they never read these fields. A strict copy of the 1.1 schema would reject them,
which is why consumers must validate against a schema at least as new as the output.

- `metadata.structure` holds five non-negative counts (`heading_count`, `table_count`,
  `max_heading_depth`, `unparented_count`, `fragment_count`) and `warnings`, all required together. It is
  measured from the emitted elements by `quality.measure_structure`. `warnings` is an array of codes
  matching `^[a-z][a-z0-9_]*$`. It is deliberately not an enum, so adding a warning code is not a schema
  change.
- `metadata.html_profile` is `{id, sha256}` and is present only when an HTML profile was applied. It
  mirrors `metadata.source.mapping`: the SHA-256 identifies the exact profile file used.
