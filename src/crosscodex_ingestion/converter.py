"""
Document converter — subprocess isolation for Docling.

Runs Docling conversion in an isolated subprocess with configurable resource limits
(RLIMIT_AS, RLIMIT_CPU, RLIMIT_FSIZE). The subprocess writes results to a temp
directory, which the parent reads and cleans up.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from crosscodex_ingestion.config import IngestionSettings


class ConversionError(Exception):
    """Raised when document conversion fails."""

    pass


class ConversionTimeout(Exception):
    """Raised when document conversion exceeds timeout."""

    pass


# Worker script executed in the subprocess
_WORKER_SCRIPT = """
import json
import resource
import sys


def main():
    if len(sys.argv) < 7:
        print(
            "Usage: worker.py <input_path> <output_path> <format_hint> "
            "<rlimit_as> <rlimit_cpu> <rlimit_fsize>",
            file=sys.stderr,
        )
        sys.exit(1)

    input_path = sys.argv[1]
    output_path = sys.argv[2]
    format_hint = sys.argv[3]
    rlimit_as = int(sys.argv[4])
    rlimit_cpu = int(sys.argv[5])
    rlimit_fsize = int(sys.argv[6])

    # Set resource limits before importing Docling
    if rlimit_as > 0:
        resource.setrlimit(resource.RLIMIT_AS, (rlimit_as, rlimit_as))
    if rlimit_cpu > 0:
        resource.setrlimit(resource.RLIMIT_CPU, (rlimit_cpu, rlimit_cpu))
    if rlimit_fsize > 0:
        resource.setrlimit(resource.RLIMIT_FSIZE, (rlimit_fsize, rlimit_fsize))

    # Import after setting rlimits
    from crosscodex_ingestion.extractor import extract_elements
    from docling.document_converter import DocumentConverter

    # Run Docling conversion
    converter = DocumentConverter()
    result = converter.convert(input_path)
    doc = result.document
    markdown = doc.export_to_markdown()

    # Extract elements
    extraction_result = extract_elements(doc, markdown, format_hint)

    # Write output
    output_data = {
        "elements": extraction_result.elements,
        "metadata": extraction_result.metadata,
    }

    with open(output_path, "w") as f:
        json.dump(output_data, f)


if __name__ == "__main__":
    main()
"""


def convert_document(input_path: str, format_hint: str, settings: IngestionSettings) -> dict[str, Any]:
    """Convert a document using Docling in an isolated subprocess.

    The subprocess:
    1. Sets resource limits (RLIMIT_AS, RLIMIT_CPU, RLIMIT_FSIZE)
    2. Runs Docling conversion
    3. Calls extract_elements()
    4. Writes result JSON to temp directory

    The parent reads the JSON and returns the parsed dict.

    Parameters
    ----------
    input_path : str
        Path to the input document.
    format_hint : str
        Format hint (e.g., 'pdf', 'html', 'txt').
    settings : IngestionSettings
        Configuration settings.

    Returns
    -------
    dict
        Dictionary with 'elements' and 'metadata' keys.

    Raises
    ------
    ConversionTimeout
        When subprocess exceeds max_processing_time.
    ConversionError
        When subprocess fails (non-zero exit code).
    """
    temp_dir = None
    try:
        # Create temp directory
        temp_dir = tempfile.mkdtemp(prefix="crosscodex_conv_")

        # Copy input file to temp dir
        input_filename = Path(input_path).name
        temp_input_path = Path(temp_dir) / input_filename
        shutil.copy2(input_path, temp_input_path)

        # Output path for JSON result
        output_path = Path(temp_dir) / "output.json"

        # Write worker script to temp file
        worker_script_path = Path(temp_dir) / "worker.py"
        worker_script_path.write_text(_WORKER_SCRIPT)

        # Calculate rlimit values in bytes/seconds
        rlimit_as_bytes = settings.rlimit_as_mb * 1024 * 1024
        rlimit_cpu_seconds = settings.rlimit_cpu
        rlimit_fsize_bytes = settings.rlimit_fsize_mb * 1024 * 1024

        # Build subprocess command
        cmd = [
            sys.executable,
            str(worker_script_path),
            str(temp_input_path),
            str(output_path),
            format_hint,
            str(rlimit_as_bytes),
            str(rlimit_cpu_seconds),
            str(rlimit_fsize_bytes),
        ]

        # Minimal environment (PATH only)
        env = {"PATH": os.environ.get("PATH", "")}

        # Run subprocess
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                timeout=settings.max_processing_time,
                env=env,
                text=True,
            )
        except subprocess.TimeoutExpired as e:
            raise ConversionTimeout(f"Conversion timed out after {settings.max_processing_time} seconds") from e

        # Check exit code
        if result.returncode != 0:
            raise ConversionError(
                f"Conversion subprocess failed with exit code {result.returncode}. stderr: {result.stderr}"
            )

        # Read output JSON
        if not output_path.exists():
            raise ConversionError("Conversion subprocess completed but did not create output file")

        with open(output_path) as f:
            output_data: dict[str, Any] = json.load(f)

        return output_data

    finally:
        # Clean up temp directory
        if temp_dir:
            shutil.rmtree(temp_dir, ignore_errors=True)
