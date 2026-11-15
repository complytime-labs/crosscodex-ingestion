"""Convert a document inside a container that has no network, to prove the baked models suffice.

Usage (run by `task test:container`, never on the host): python offline_convert.py <document>

Exits non-zero, with a traceback or a message on stderr, if the network is reachable, a model
directory is missing, or the conversion fails or yields no text.
"""

import asyncio
import socket
import sys
from pathlib import Path

from crosscodex_ingestion.config import IngestionSettings
from crosscodex_ingestion.converter import convert_document

# Directories `scripts/download_models.py` must leave in the models directory.
EXPECTED_MODEL_DIRS = [
    "docling-project--docling-layout-heron",
    "docling-project--docling-layout-heron-onnx",
    "docling-project--docling-models",
    "docling-project--CodeFormulaV2",
    "docling-project--DocumentFigureClassifier-v2.5",
    "RapidOcr",
]


def assert_network_is_off() -> None:
    try:
        socket.create_connection(("1.1.1.1", 443), timeout=5).close()
    except OSError:
        return
    sys.exit("network is reachable: run this check with `--network=none`")


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit(f"Usage: {sys.argv[0]} <document>")
    document = Path(sys.argv[1])

    assert_network_is_off()

    settings = IngestionSettings()  # DOCLING_ARTIFACTS_PATH comes from the image environment
    models = Path(settings.docling_artifacts_path)
    missing = [name for name in EXPECTED_MODEL_DIRS if not (models / name).is_dir()]
    if missing:
        sys.exit(f"models missing from {models}: {', '.join(missing)}")

    result = asyncio.run(convert_document(str(document), document.suffix.lstrip("."), settings))
    elements = result["elements"]
    if not elements:
        sys.exit(f"{document.name}: conversion returned no elements")
    sys.stdout.write(f"OK: {document.name} converted offline into {len(elements)} elements\n")


if __name__ == "__main__":
    main()
