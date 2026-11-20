#!/usr/bin/env python3
"""Fail if the installed packages differ from a `uv export` requirements file.

Usage: check_lock_parity.py <requirements.txt> <freeze.txt>

<requirements.txt> is `uv export --format requirements.txt` output; <freeze.txt> is
`uv pip freeze` output for the environment to check. Names are compared after PEP 503
normalization and versions exactly as written (so local versions such as `2.15.0+cpu` must
match too). Requirements whose environment marker does not hold for the running interpreter
are skipped, as the installer skipped them. The caller excludes packages that are not part of
the lock (the project itself, base-image tooling) from the freeze.

Exit status: 0 on parity, 1 with a diff of mismatches on stderr, 2 on unparseable input.
"""

import sys
from pathlib import Path

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name


def parse_pins(text: str, source: str, *, honor_markers: bool) -> dict[str, str]:
    """Map normalized name to exact version for every `name==version` line in `text`."""
    pins: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip().removesuffix("\\").strip()
        if not line or line.startswith(("#", "--")):
            continue
        try:
            req = Requirement(line)
        except InvalidRequirement as exc:
            raise ValueError(f"{source}: cannot parse {line!r}: {exc}") from exc
        specs = list(req.specifier)
        if len(specs) != 1 or specs[0].operator != "==":
            raise ValueError(f"{source}: {line!r} is not an exact `name==version` pin")
        if honor_markers and req.marker is not None and not req.marker.evaluate():
            continue
        pins[canonicalize_name(req.name)] = specs[0].version
    return pins


def diff_pins(locked: dict[str, str], installed: dict[str, str]) -> list[str]:
    """Return one human-readable line per difference; empty when the sets are identical."""
    problems = []
    for name in sorted(locked.keys() | installed.keys()):
        want, have = locked.get(name), installed.get(name)
        if want is None:
            problems.append(f"+ {name}=={have}  installed but not in the lock")
        elif have is None:
            problems.append(f"- {name}=={want}  in the lock but not installed")
        elif want != have:
            problems.append(f"~ {name}: lock {want}, installed {have}")
    return problems


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    try:
        locked = parse_pins(Path(argv[1]).read_text(), argv[1], honor_markers=True)
        installed = parse_pins(Path(argv[2]).read_text(), argv[2], honor_markers=False)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    problems = diff_pins(locked, installed)
    if problems:
        print("installed packages differ from the lock (uv.lock export):", file=sys.stderr)
        print("\n".join(problems), file=sys.stderr)
        return 1
    print(f"installed packages match the lock ({len(locked)} pinned)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
