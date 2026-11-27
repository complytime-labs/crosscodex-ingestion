"""Capture a PDF's DoclingDocument for the layout-repair specs.

Usage: python capture_docling.py <pdf> <case dir>

Writes `<case dir>/docling.json` (`doc.export_to_dict()`) and `<case dir>/export.md`
(`doc.export_to_markdown()`). The conversion matches the PDF path of `converter._WORKER_SCRIPT`: a
`DocumentConverter` allowed only PDF, with `PdfPipelineOptions(artifacts_path=...)` and nothing else
changed. Set DOCLING_ARTIFACTS_PATH to the models directory (task test:models). Like the worker, which
runs with HF_HUB_OFFLINE=1 in its environment, the script sets HF_HUB_OFFLINE=1 before importing Docling,
so no model is fetched from the network. Any status other than SUCCESS fails the capture, as it fails the
worker.
"""

import json
import os
import sys
from pathlib import Path

# Set before Docling (and huggingface_hub) is imported: the hub reads it at import time.
os.environ["HF_HUB_OFFLINE"] = "1"

from docling.datamodel.base_models import ConversionStatus, InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.document_converter import DocumentConverter, PdfFormatOption


def main() -> None:
    if len(sys.argv) != 3:
        sys.exit(f"Usage: {sys.argv[0]} <pdf> <case dir>")
    source, case_dir = Path(sys.argv[1]), Path(sys.argv[2])
    artifacts_path = os.environ.get("DOCLING_ARTIFACTS_PATH")
    if not artifacts_path:
        sys.exit("DOCLING_ARTIFACTS_PATH is not set: point it at the models directory (task test:models).")
    pdf_options = PdfPipelineOptions(artifacts_path=artifacts_path)
    converter = DocumentConverter(
        allowed_formats=[InputFormat.PDF],
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pdf_options)},
    )
    result = converter.convert(source, raises_on_error=False)
    if result.status != ConversionStatus.SUCCESS:
        errors = "".join(f"\n{error.error_message}" for error in result.errors)
        sys.exit(f"conversion of {source} ended with status {result.status}{errors}")
    doc = result.document
    case_dir.mkdir(parents=True, exist_ok=True)
    (case_dir / "docling.json").write_text(
        json.dumps(doc.export_to_dict(), indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (case_dir / "export.md").write_text(doc.export_to_markdown(), encoding="utf-8")


if __name__ == "__main__":
    main()
