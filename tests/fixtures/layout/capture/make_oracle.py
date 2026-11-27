"""Write an ASVS oracle: each selected requirement's Shortcode mapped to its section's Shortcode.

Usage: python make_oracle.py <release.json> <oracle.json> <shortcode> [<shortcode> ...]

<release.json> is an ASVS JSON release (Requirements -> chapter Items -> section Items -> requirements).
Each <shortcode> names a section (for example V10.4), which selects all of its requirements, or a single
requirement (for example V10.3.4). The oracle lists requirements in release order, so the output is
the same whatever order the shortcodes are given in. A shortcode the release does not hold fails the run.
"""

import json
import sys
from pathlib import Path


def main() -> None:
    if len(sys.argv) < 4:
        sys.exit(f"Usage: {sys.argv[0]} <release.json> <oracle.json> <shortcode> [<shortcode> ...]")
    release = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    wanted = set(sys.argv[3:])
    oracle: dict[str, str] = {}
    known: set[str] = set()
    for chapter in release["Requirements"]:
        for section in chapter["Items"]:
            known.add(section["Shortcode"])
            for requirement in section["Items"]:
                known.add(requirement["Shortcode"])
                if section["Shortcode"] in wanted or requirement["Shortcode"] in wanted:
                    oracle[requirement["Shortcode"]] = section["Shortcode"]
    unknown = sorted(wanted - known)
    if unknown:
        sys.exit(f"the release holds no section or requirement {', '.join(unknown)}")
    Path(sys.argv[2]).write_text(json.dumps(oracle, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
