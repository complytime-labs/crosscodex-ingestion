# Provenance of the fallback structurer fixtures

Each case directory holds an ingestion output document `input.json` (schema 1.3, layout elements only) and
`golden.json`: what crosscodex's Go structurer returns for it. The Python fallback structurer is tested
against these goldens.

## Oracle

- crosscodex commit: `80f764fe` (`pkg/oscal/structurer*.go`, `pkg/oscal/decompose.go`,
  `internal/catalog/ingestion_adapter.go`).
- Capture command: `task fixtures:fallback-goldens CROSSCODEX_DIR=<crosscodex checkout>`. The program and
  its behaviour are in `capture/README.md`.
- `golden.json` tier `none` means Go found no structure (`ErrStructureFailed` or only the paragraph
  fallback tier). `no-structure-empty` has no elements; Go's adapter rejects that before the cascade, and
  the capture program records it as `none`.

## Real inputs

Layouts captured at ingestion commit `d68ecb0` (Docling 2.148.0 from `uv.lock`) with
`DOCLING_ARTIFACTS_PATH=<models dir> uv run python tests/fixtures/fallback/capture/capture_layout.py
<source> <input.json>`. The script is runnable only at that commit.

| Case | Source | Source SHA-256 | Terms |
| --- | --- | --- | --- |
| `nist-sp800-171r3-excerpt` | `tests/fixtures/documents/nist-sp800-171r3-excerpt.pdf`, PDF pages 13-17 of NIST SP 800-171 Rev. 3 (May 2024), <https://nvlpubs.nist.gov/nistpubs/SpecialPublications/NIST.SP.800-171r3.pdf>, retrieved 2026-11-19 | `61f5d194715293bb7debdc5d8efc8285ca9f9593a3e0abbba2799a231bbbb07b` | Work of the U.S. Government, not subject to copyright in the United States (17 U.S.C. 105) |
| `eurlex-nis2-32022L2555` | `tests/fixtures/documents/eurlex-nis2-32022L2555.html`, trimmed excerpt of Directive (EU) 2022/2555 (NIS2, CELEX 32022L2555), <https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX:32022L2555>, retrieved 2026-11-18 | `6aa347de4e94c8f8f4e1b5264eeae462d748d1ac99df9368def5447f23851e51` | (c) European Union; reuse under Commission Decision 2011/833/EU with acknowledgement of the source (see `tests/fixtures/documents/NOTICE`) |

The fresh layouts give 13 items (SP 800-171r3) and 36 items (NIS2), the counts of crosscodex's frozen
fixtures. The `input.json` metadata keeps the converter's `processing_time_ms`, which the Go adapter ignores.

## Synthetic inputs

All other cases are authored for this repository under its licence. Each pins one boundary of the Go
cascade:

- `table-tier`: first cell of 49 versus 50 bytes, 25 two-byte characters (50 bytes), empty first cell, empty
  row, escaped pipe, a second table (row numbering continues), a table ending in a newline, a header-only table.
- `pattern-each-01` to `pattern-each-12`: three lines of each pattern, in Go's order; the golden IDs show
  which pattern won. `pattern-two-lines`: 2 lines do not win. `pattern-priority`: patterns 1 and 12 both reach 3,
  1 wins. `pattern-priority-fallthrough`: pattern 1 has 2, so 12 wins.
- `heading-repeats-3` (kept) and `heading-repeats-4` (dropped, IDs keep their position);
  `heading-all-furniture`: every heading repeats 4 times, so the pattern tier wins.
- `decompose-39-words`, `-40-words`, `-41-words`: the minimum-word boundary. `decompose-each-tier`: one
  section per decomposition tier. `decompose-item-min-words`: 8 versus 7 words per item for tiers 2 to 5, and
  a 7-word item dropped beside kept ones. `decompose-nested`: three levels. `decompose-repeated-labels`:
  `-n` ID suffixes.
- `no-title`: `metadata.title` null, text before the first heading, a paragraph with no parent, an empty section.
- `probe-nbsp`, `probe-vtab`, `probe-nel`, `probe-u2028`, `probe-u3000`: a character that Go treats as
  space but RE2 `\s` does not match, as a label separator, before a label, in a word count, inside and at the
  end of a clause, and around a short text. `probe-nonascii-digits`: Arabic-Indic and full-width digits do
  not match `\d`. `probe-pattern-lines`: `TrimSpace` of pattern lines (U+001C is not trimmed by Go).
  `probe-trailing-newline`: titles keep a trailing newline and count as different headings.
  `probe-crlf`, `probe-blank-line`: carriage returns, blank-but-not-empty lines, a label with no text.
- `no-structure-empty`, `no-structure-paragraphs`: no structure.

## Cases

| Case | Tier | Items |
| --- | --- | --- |
| `decompose-39-words` | heading | 1 |
| `decompose-40-words` | heading | 2 |
| `decompose-41-words` | heading | 2 |
| `decompose-each-tier` | heading | 24 |
| `decompose-item-min-words` | heading | 10 |
| `decompose-nested` | heading | 6 |
| `decompose-repeated-labels` | heading | 9 |
| `eurlex-nis2-32022L2555` | heading | 36 |
| `heading-all-furniture` | pattern | 4 |
| `heading-repeats-3` | heading | 5 |
| `heading-repeats-4` | heading | 2 |
| `nist-sp800-171r3-excerpt` | heading | 13 |
| `no-structure-empty` | none | 0 |
| `no-structure-paragraphs` | none | 0 |
| `no-title` | heading | 3 |
| `pattern-each-01` | pattern | 3 |
| `pattern-each-02` | pattern | 3 |
| `pattern-each-03` | pattern | 3 |
| `pattern-each-04` | pattern | 3 |
| `pattern-each-05` | pattern | 3 |
| `pattern-each-06` | pattern | 3 |
| `pattern-each-07` | pattern | 3 |
| `pattern-each-08` | pattern | 3 |
| `pattern-each-09` | pattern | 3 |
| `pattern-each-10` | pattern | 3 |
| `pattern-each-11` | pattern | 3 |
| `pattern-each-12` | pattern | 3 |
| `pattern-priority-fallthrough` | pattern | 3 |
| `pattern-priority` | pattern | 3 |
| `pattern-two-lines` | none | 0 |
| `probe-blank-line` | heading | 10 |
| `probe-crlf` | heading | 5 |
| `probe-nbsp` | heading | 9 |
| `probe-nel` | heading | 9 |
| `probe-nonascii-digits` | heading | 4 |
| `probe-pattern-lines` | pattern | 6 |
| `probe-trailing-newline` | heading | 7 |
| `probe-u2028` | heading | 9 |
| `probe-u3000` | heading | 9 |
| `probe-vtab` | heading | 9 |
| `table-tier` | table | 9 |
