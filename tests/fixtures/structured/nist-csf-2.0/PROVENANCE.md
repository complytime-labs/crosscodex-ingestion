# NIST CSF 2.0 Core spreadsheet (excerpt)

| | |
|---|---|
| Original | <https://csrc.nist.gov/extensions/nudp/services/json/csf/download?olirids=all&download_format=xlsx> (CSF 2.0 Reference Tool export, downloaded 2026-11-17) |
| Original SHA-256 | Not reproducible: the export embeds its generation time (sheet `Introduction`), so each download differs. The one trimmed here was 146 546 bytes. |
| `input.xlsx` SHA-256 | `e2e527db922f0af2ea4e2d21188a3cebbe3d9e60a78211ce40f4b68105d4d649` (openpyxl output; rewriting it changes the hash) |
| License | Work of the U.S. Government, public domain in the United States (see `../NOTICE`) |
| Mapping | `src/crosscodex_ingestion/mappings/nist-csf-2.0-core-xlsx.yaml` |

Shape of the release: sheet `CSF 2.0`; row 1 is a banner, row 2 the header `Function, Category, Subcategory,
Implementation Examples, Informative References`. Each later row fills exactly one of the first three columns
with `LABEL: description`. A Function or Category label is `Name (ID)` (`GOVERN (GV)`), a Subcategory label is
its ID (`GV.OC-01`). Each Function's group ends with a bare `NAME (ID)` row with no description.

## Trim recipe

Keep rows 1 and 2, then, in order: Function GV (its row, categories GV.OC and GV.RM with all their
subcategories, and its closing bare row) and Function ID (its row, category ID.AM, subcategories ID.AM-01 and
ID.AM-02, and its closing bare row). Cell values are copied as openpyxl reads them (`data_only=True`) into a new
workbook with the sheet named `CSF 2.0`.

```python
import openpyxl

sheet = openpyxl.load_workbook("csf-2.0.xlsx", data_only=True)["CSF 2.0"]
rows = list(sheet.iter_rows(values_only=True))
label = lambda r: next((c for c in r[:3] if c), "")
starts = (
    "GOVERN (GV)",
    "IDENTIFY (ID)",
    "Organizational Context (GV.OC)",
    "Risk Management Strategy (GV.RM)",
    "Asset Management (ID.AM)",
    "ID.AM-01:",
    "ID.AM-02:",
    "GV.OC-",
    "GV.RM-",
)
keep = rows[:2] + [r for r in rows[2:] if label(r).startswith(starts)]
out = openpyxl.Workbook()
out.active.title = "CSF 2.0"
for r in keep:
    out.active.append(list(r))
out.save("input.xlsx")
```

`expected.json` is the output of `convert_source(input.xlsx, mapping)` without `processing_time_ms`, reviewed by
hand: 19 nodes, the two closing bare rows re-enter their Function instead of adding a node. Regenerate it only
for an intended output change, and review the diff.
