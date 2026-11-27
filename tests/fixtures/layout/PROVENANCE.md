# Provenance of the layout-repair fixtures

Each case directory holds a DoclingDocument captured from an excerpt PDF in `tests/fixtures/documents/`
(`docling.json`, from `export_to_dict()`) and the Markdown Docling exported with it (`export.md`).
`tests/unit/test_layout_fixtures.py` reloads them with `DoclingDocument.model_validate` and runs the
extractor's layout repair on them, so the specs need docling-core but no converter or models. Licences and
source URLs are in `tests/fixtures/documents/NOTICE`.

## Capture

- Docling 2.148.0 (docling-core 2.106.0, docling-ibm-models 4.0.4, docling-parse 7.24.2), as pinned in
  `uv.lock`; models from `task test:models`. Captured 2026-11-26 at ingestion commit `badb937`.
- Command, from the repository root:

  ```sh
  DOCLING_ARTIFACTS_PATH=<models dir> uv run python tests/fixtures/layout/capture/capture_docling.py \
    tests/fixtures/documents/<case>.pdf tests/fixtures/layout/<case>
  ```

- `capture/capture_docling.py` converts as the PDF path of `converter._WORKER_SCRIPT` does: a
  `DocumentConverter` allowed only PDF, with `PdfPipelineOptions(artifacts_path=...)`. It does not pass the
  worker's `max_num_pages`, which only rejects long documents and does not change a conversion. Like the
  worker, it runs with `HF_HUB_OFFLINE=1` (it sets that itself before importing Docling).
- Docling's output was reproducible: two conversions of the full ASVS and CPG PDFs, from the earlier probe
  and from this capture, gave identical documents.

## Cases

| Case | Source pages (PDF, from 1) | Excerpt SHA-256 | What the pages cover |
| --- | --- | --- | --- |
| `owasp-asvs-5.0.0-excerpt` | ASVS 5.0.0 pages 66-70, 110-112, 119-120 (source `f3e9a2f1…d30c6`) | `c72db430c1c9afb8e8e43e02b8f1b919c2e1ec90c59422bbd66938d05f2eaa47` | V10.4 over 3 pages (a 3-part chain, repeated header); V10.5 over pages 68-69 (repeated header); V10.6 to V10.7, the same header with a heading between (69-70); the "Hash Functions for General Use" table over 110-112, whose width Docling reads as 3, 4, 3 columns (differing header and width, nothing between); the header-less contributor list over 119-120 |
| `cisa-cpg-2.0-excerpt` | CPG 2.0 pages 24-31 (source `44ace92a…9f85e`) | `d23ae0c1d570e934e2b994a8c2cbeaebdfe60d091cee8057cc58e25342c21cc8` | the `CISA` logo text on 29-31; `OUTCOME` and `RECOMMENDED ACTION` in the top margin on 24, 26 and 27 and mid-page elsewhere; the `COST`/`IMPACT`/`EASE OF IMPLEMENTATION` tables on 27 and 28, the same header with headings between |
| `nist-sp800-53b-excerpt` | SP 800-53B pages 65-68 (source `8a9dae90…e02fa`) | `67b0d19d16b0522b744cc923b4f9de913ec0914e71ff2c948bbd307f7624ec89` | the SI baseline table over 65-67, a 3-part chain with a repeated 2-row header; its continuation on 68, whose header Docling reads differently; an underscore rule in the margin of every page |
| `nist-sp800-171r3-excerpt` | the existing `nist-sp800-171r3-excerpt.pdf` (SP 800-171r3 pages 13-17) | `61f5d194715293bb7debdc5d8efc8285ca9f9593a3e0abbba2799a231bbbb07b` | a document with no split tables and no unlabelled running text |

Excerpts were trimmed with `qpdf --empty --pages <source> <pages> -- <excerpt>` (qpdf 11.8.0).

### `oracle.json`

`owasp-asvs-5.0.0-excerpt/oracle.json` maps each requirement printed on the excerpt pages to its section,
`{Shortcode: section Shortcode}`, from the ASVS 5.0.0 JSON release (SHA-256 `bcdbec21…26cb35`): V10.3.4 and
V10.3.5 (the rest of V10.3 is on page 65) and every requirement of V10.4 to V10.7, 28 in all. Pages 110-112
and 119-120 hold no requirements. The PDF prints a Shortcode without its `V`. Generated with:

```sh
uv run python tests/fixtures/layout/capture/make_oracle.py \
  <dir>/OWASP_Application_Security_Verification_Standard_5.0.0_en.json \
  tests/fixtures/layout/owasp-asvs-5.0.0-excerpt/oracle.json \
  V10.3.4 V10.3.5 V10.4 V10.5 V10.6 V10.7
```

The release JSON is
<https://github.com/OWASP/ASVS/releases/download/v5.0.0_release/OWASP_Application_Security_Verification_Standard_5.0.0_en.json>;
check its SHA-256 before use. A section Shortcode selects all its requirements; the output lists them in
release order.

## Feasibility gate

Each excerpt's conversion must give the same joins and the same `row` and `furniture` entries as the same
pages within the full PDF, mapped to source pages (a join or `row` entry counts only when both its pages are
in the excerpt). `capture/gate.py` checks this on two captures (see "Re-capturing" for the commands). All
pass:

| Case | Joins on the excerpt | Joins on the same pages of the full PDF | `dropped` |
| --- | --- | --- | --- |
| ASVS | 4: 66-67, 67-68, 68-69, 119-120 | 4, the same | 3 `row` (pages 67, 68, 69), equal |
| CPG | 0 | 0 | none, equal |
| SP 800-53B | 2: 65-66, 66-67 | 2, the same | 4 `row` (2 each on 66 and 67) and `furniture` on 65-68, equal |
| SP 800-171r3 | 0 | (excerpt only) | none |

A `furniture` reason counts the pages its text occurs on, so it differs by construction: 4 on the
SP 800-53B excerpt, 83 on the full PDF.

The excerpt makes pages adjacent that are not in the source (ASVS 70-110 and 112-119). Neither forms a
join: a heading lies between the parts.

## Re-capturing after a Docling upgrade

A changed `uv.lock` Docling pin can change Docling's per-page detection, and with it every count below.
Re-capture and re-check each case from the repository root, with `DOCLING_ARTIFACTS_PATH` set:

1. Check each source PDF's SHA-256 against `tests/fixtures/documents/NOTICE`, and capture the full PDF into a
   scratch directory outside the repository (several minutes per PDF):

   ```sh
   uv run python tests/fixtures/layout/capture/capture_docling.py <source>.pdf <scratch>/<case>-full
   ```

2. Re-capture the excerpt with the command under "Capture".
3. Run the gate. It must print `GATE PASS` and exit 0; if it fails, choose other pages.

   ```sh
   uv run python tests/fixtures/layout/capture/gate.py tests/fixtures/layout/<case>/docling.json \
     <scratch>/<case>-full/docling.json <source pages>
   ```

   `<source pages>`: `66,67,68,69,70,110,111,112,119,120` (ASVS), `24,25,26,27,28,29,30,31` (CPG),
   `65,66,67,68` (SP 800-53B). SP 800-171r3 is excerpt-only, and its spec asserts no join and nothing dropped.
4. Regenerate `oracle.json` with the `make_oracle.py` command under "`oracle.json`" (it depends only on the
   release, so it changes only if the excerpt pages do), and update the pinned counts in
   `tests/unit/test_layout_fixtures.py` and in this file to what the extractor now produces.

## Pinned results

- **ASVS:** 3 joined tables (`row_pages` 5+9+3, 4+2 and 8+18 rows by page), `table_rejoin_count` 4,
  `table_count` 10, 3 `row` entries, warning `table_rejoined`, no `furniture`.
- **CPG:** no join, no `dropped` entry, no warning.
- **SP 800-53B:** 1 joined table (34+42+41 rows by page, 2 header rows), `table_rejoin_count` 2,
  `table_count` 2, 4 `row` and 4 `furniture` entries, warning `table_rejoined`.
- **SP 800-171r3:** no join, no `dropped` entry.

## Findings that differ from the design's probe

- **CPG `CISA` is never in the layout.** It is the text of the CISA logo, a child of a picture on each of
  its 12 pages, and `iterate_items()` does not enter pictures. The extractor never emitted it, and
  Markdown export omits it too, so there is no unlabelled furniture to remove in CPG. Its fixture pins
  that: no `furniture` entry, and `CISA` in no element. It also sits at the top of its page, not the bottom.
- **ASVS contents pages are not tables to the walk.** Docling labels the 2-column contents tables on pages
  2-7 `document_index`, which the extractor already skips, so their continuations never reach a join.
- **SP 800-53B real splits that do not join:** 10 pairs of consecutive pages hold one baseline table with
  nothing between, but Docling reads the repeated header differently on one page (an empty or truncated
  `PRIVACY CONTROL BASELINE` cell, `CONTROL NAME` split from `CONTROL ENHANCEMENT NAME`, an empty
  `CONTROL NUMBER`), or reads a different width (pages 51-52). Ruling 3 compares headers exactly, so these
  fail closed to "no join"; none is a false join.

## Full-PDF runs (recorded, not committed specs)

- **ASVS 5.0.0, against the JSON release:** 38 joins in 35 chains, from 136 body tables to 98 table
  elements.
  - 30 joins are in requirement tables (29 chains); each chain's requirements share one parent section.
  - No section's requirements are split across two table elements.
  - The other 8 joins are appendix tables checked by hand, each one table in the PDF: the random-number
    generators (105-106), the symmetric algorithms (106-107), the password-storage hash functions
    (112-113), the password-based KDFs (113-114), the signature algorithms (116-117) and the contributor
    list (119-122, header-less).
  - 343 of the 345 requirements are read from an ID cell. V11.5.1 and V11.5.2 (page 75) are not, because
    Docling merges their ID into the description cell. That is unrelated to rejoin.
- **CPG 2.0:** 34 body tables, no join, no `dropped` entry.
- **SP 800-53B:** 18 joins (37 table elements): the revision history (13-14), 7 control-family baseline
  tables (10 joins), the standards references (71-72) and the glossary (73-79, header-less). Each joins one table of
  the PDF. An underscore rule is removed as furniture on 83 pages.
