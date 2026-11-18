# OWASP ASVS 5.0.0 release CSV (excerpt)

| | |
|---|---|
| Original | <https://github.com/OWASP/ASVS/releases/download/v5.0.0_release/OWASP_Application_Security_Verification_Standard_5.0.0_en.csv> (release `v5.0.0_release`) |
| Original SHA-256 | `6124dba176dc563f66363a11ae0c47f9b86b8a4a84c66a793670bd196ed86cd5` (105 446 bytes) |
| `input.csv` SHA-256 | `8f63b7cce417a4c0e2d5d02f24d07a53585e60322c7bc990f7767cec3ef9dba4` |
| License | CC BY-SA 4.0, OWASP Foundation (see `../NOTICE`) |
| Mapping | `src/crosscodex_ingestion/mappings/owasp-asvs-5.0.0-csv.yaml` |

Shape of the release: header `chapter_id, chapter_name, section_id, section_name, req_id, req_description, L`;
one row per requirement, every column filled, CRLF line endings.

## Trim recipe

Keep the header, section V1.1 of chapter V1 and chapter V6 whole: the same excerpt as
`../owasp-asvs-5.0.0/` (49 requirements). Values are not changed.

```python
import csv

with open("OWASP_Application_Security_Verification_Standard_5.0.0_en.csv", encoding="utf-8", newline="") as f:
    rows = [r for r in csv.reader(f) if len(r) == 7]
keep = [rows[0]] + [r for r in rows[1:] if r[2] == "V1.1" or r[0] == "V6"]
with open("input.csv", "w", encoding="utf-8", newline="") as f:
    csv.writer(f, lineterminator="\r\n").writerows(keep)
```

`expected.json` is the output of `convert_source(input.csv, mapping)` without `processing_time_ms`, reviewed
by hand. Its elements equal those of `../owasp-asvs-5.0.0/expected.json` except that headings carry no
`short_name` property (the CSV has no such column); `tests/unit/test_structured.py` checks this. Regenerate it
only for an intended output change, and review the diff.
