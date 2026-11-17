# MITRE ATT&CK Enterprise v17.1 techniques spreadsheet (excerpt)

| | |
|---|---|
| Original | <https://attack.mitre.org/docs/attack-excel-files/v17.1/enterprise-attack/enterprise-attack-v17.1-techniques.xlsx> |
| Original SHA-256 | `85062e039a0b44db7b260bab3ebcca5c301c5e400c4ca3058e8d95ded6ef4dea` (2 989 243 bytes) |
| `input.xlsx` SHA-256 | `13a028af2a50d6043f21a149014ac8c5492efccf9bef47a08afe61741455ed45` |
| License | ATT&CK Terms of Use; © 2026 The MITRE Corporation. This work is reproduced and distributed with the permission of The MITRE Corporation. (Full notice and license in `../NOTICE`.) |
| Mapping | `src/crosscodex_ingestion/mappings/mitre-attack-enterprise-17.1-techniques-xlsx.yaml` |

The `techniques` sheet has one row per technique and sub-technique; a sub-technique names its
parent technique in the `sub-technique of` column, which is blank for a technique.

## Trim recipe

Only the `techniques` sheet, with its header row and all 19 columns. Nine rows, in this order: three
techniques with two sub-techniques each, and one sub-technique (`T1087.001`) moved **above** its
parent to test that the parent column, not row order, decides nesting:

`T1548, T1548.002, T1548.004, T1134, T1134.002, T1134.001, T1087.001, T1087, T1087.004`

Cell values are copied as openpyxl reads them (`data_only=True`) into a new workbook; formatting
and the other sheets are not kept.

```python
import openpyxl

ids = ["T1548", "T1548.002", "T1548.004", "T1134", "T1134.002", "T1134.001", "T1087.001", "T1087", "T1087.004"]
src = openpyxl.load_workbook(
    "enterprise-attack-v17.1-techniques.xlsx", read_only=True, data_only=True, keep_links=False
)
rows = src["techniques"].iter_rows(values_only=True)
header = next(rows)
by_id = {row[0]: row for row in rows}
out = openpyxl.Workbook()
out.active.title = "techniques"
out.active.append(header)
for row_id in ids:
    out.active.append(by_id[row_id])
out.save("input.xlsx")
```

`expected.json` is the output of `convert_source(input.xlsx, mapping)` without
`processing_time_ms`, reviewed by hand and compared exactly by `tests/unit/test_structured.py`.
