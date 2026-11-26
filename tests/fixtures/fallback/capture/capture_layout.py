"""Write the layout conversion of a document as JSON, the input of the Go capture tool.

Usage: python capture_layout.py <source> <out.json>

Runnable ONLY at ingestion commit d68ecb0 (the merge base of the fallback-structure work). It calls
`convert_document` as the worker does, with default settings, and relies on that function still
returning layout elements for every document. From the commit that makes the converter emit roles
(plan Task 5) it returns structured elements instead, so the real-layout inputs under
tests/fixtures/fallback/ must not be recaptured from a later commit. Set DOCLING_ARTIFACTS_PATH to the
models directory (task test:models).
"""

import asyncio
import json
import sys
from pathlib import Path

from crosscodex_ingestion.config import IngestionSettings
from crosscodex_ingestion.converter import convert_document
from crosscodex_ingestion.html_profile import load_html_profiles


def main() -> None:
    if len(sys.argv) != 3:
        sys.exit(f"Usage: {sys.argv[0]} <source> <out.json>")
    source, out = Path(sys.argv[1]), Path(sys.argv[2])
    settings = IngestionSettings()
    profiles = load_html_profiles(settings.html_profiles_path)
    result = asyncio.run(convert_document(str(source), source.suffix.lstrip("."), settings, profiles))
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
