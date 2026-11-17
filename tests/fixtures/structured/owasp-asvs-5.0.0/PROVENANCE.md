# OWASP ASVS 5.0.0 release JSON (excerpt)

| | |
|---|---|
| Original | <https://github.com/OWASP/ASVS/releases/download/v5.0.0_release/OWASP_Application_Security_Verification_Standard_5.0.0_en.json> (release `v5.0.0_release`) |
| Original SHA-256 | `bcdbec214d70abcfad9284a31d4f9e5134305831d628aad3aa85d7e26626cb35` (149 407 bytes) |
| `input.json` SHA-256 | `2fe177f4e19098a4197829a8cd61fdb5a21e933934aa9b8ff40f6131ba15e368` |
| License | CC BY-SA 4.0, OWASP Foundation (see `../NOTICE`) |
| Mapping | `src/crosscodex_ingestion/mappings/owasp-asvs-5.0.0-json.yaml` |

Shape of the release: root `{Name, ShortName, Version, Description, Requirements[]}`; chapter
`{Shortcode, Ordinal, ShortName, Name, Items}`; section `{Shortcode, Ordinal, Name, Items}`;
requirement `{Shortcode, Ordinal, Description, L}`.

## Trim recipe

Keep the root fields; keep chapter V6 whole (8 sections, 47 requirements) and chapter V1 with only
its first section, V1.1. Every other chapter is removed. Values are not changed.

```python
import json

with open("OWASP_Application_Security_Verification_Standard_5.0.0_en.json", encoding="utf-8") as f:
    doc = json.load(f)
v1 = next(c for c in doc["Requirements"] if c["Shortcode"] == "V1")
v6 = next(c for c in doc["Requirements"] if c["Shortcode"] == "V6")
v1["Items"] = [s for s in v1["Items"] if s["Shortcode"] == "V1.1"]
doc["Requirements"] = [v1, v6]
with open("input.json", "w", encoding="utf-8") as f:
    json.dump(doc, f, indent=2, ensure_ascii=False)
    f.write("\n")
```

`expected.json` is the output of `convert_source(input.json, mapping)` without
`processing_time_ms`; it was reviewed by hand and is compared exactly by
`tests/unit/test_structured.py`. Regenerate it only for an intended output change, and review the diff.
