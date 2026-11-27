"""Pinned layout-repair results of the excerpt PDFs, shared by the unit specs (captured layouts) and the live specs.

Both convert the same excerpts, so both must see these values. A difference after a Docling upgrade means
the layouts must be re-captured (see PROVENANCE.md).
"""

from typing import Any

ASVS = "owasp-asvs-5.0.0-excerpt"
CPG = "cisa-cpg-2.0-excerpt"
NIST_171R3 = "nist-sp800-171r3-excerpt"
NIST_53B = "nist-sp800-53b-excerpt"
CASES = [ASVS, CPG, NIST_171R3, NIST_53B]

ROW_REASON = "repeated header row of a table continued from page {}; removed when the parts were rejoined"
FURNITURE_REASON = "text repeated in the page margin on {} pages; removed as an unlabelled running header or footer"

# The table_rejoin_count of measure_structure, and the measured table count where a spec pins it.
TABLE_REJOIN_COUNT = {ASVS: 4, CPG: 0, NIST_171R3: 0, NIST_53B: 2}
TABLE_COUNT = {ASVS: 10, NIST_53B: 2}

WARNINGS = {ASVS: ["table_rejoined"], CPG: [], NIST_171R3: [], NIST_53B: ["table_rejoined"]}

DROPPED: dict[str, list[dict[str, Any]]] = {
    ASVS: [{"type": "row", "page": page, "reason": ROW_REASON.format(page - 1)} for page in (2, 3, 4)],
    CPG: [],
    NIST_171R3: [],
    NIST_53B: [
        {"type": "furniture", "page": 1, "reason": FURNITURE_REASON.format(4)},
        {"type": "row", "page": 2, "reason": ROW_REASON.format(1)},
        {"type": "row", "page": 2, "reason": ROW_REASON.format(1)},
        {"type": "row", "page": 3, "reason": ROW_REASON.format(2)},
        {"type": "row", "page": 3, "reason": ROW_REASON.format(2)},
        {"type": "furniture", "page": 2, "reason": FURNITURE_REASON.format(4)},
        {"type": "furniture", "page": 3, "reason": FURNITURE_REASON.format(4)},
        {"type": "furniture", "page": 4, "reason": FURNITURE_REASON.format(4)},
    ],
}
