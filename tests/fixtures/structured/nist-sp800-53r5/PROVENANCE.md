# NIST SP 800-53 Revision 5 control catalog spreadsheet (excerpt)

| | |
|---|---|
| Original | <https://csrc.nist.gov/files/pubs/sp/800/53/r5/upd1/final/docs/sp800-53r5-control-catalog.xlsx> |
| Original SHA-256 | `1777ed6f839bccd55516e6e703bfe75e3cf63efb2ec80bb9309ed960af2c9ea4` |
| `input.xlsx` SHA-256 | `aa09006220f808e9234e9401181ac043fa088eda51b013fa952ea449ab255885` |
| License | Work of the U.S. Government, public domain in the United States (see `../NOTICE`) |
| Mapping | `src/crosscodex_ingestion/mappings/nist-sp800-53r5-catalog-xlsx.yaml` |

The sheet `SP 800-53 Revision 5` has one row per control and control enhancement. An enhancement's
ID is its control's ID followed by `(n)`, so the mapping derives the parent with the ID-prefix rule
(separator `(`).

## Trim recipe

The one sheet with its header row and all 5 columns, and these rows in catalog order:
`AC-1, AC-2, AC-2(1), AC-2(2), AC-2(3), AC-3`. Cell values are copied as openpyxl reads them
(`data_only=True`) into a new workbook (same script as `../mitre-attack-enterprise-17.1/PROVENANCE.md`,
with sheet `SP 800-53 Revision 5` and these IDs).

`expected.json` is the output of `convert_source(input.xlsx, mapping)` without
`processing_time_ms`, reviewed by hand and compared exactly by `tests/unit/test_structured.py`.
