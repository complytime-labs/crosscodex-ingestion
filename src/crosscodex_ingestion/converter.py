"""
Document converter — subprocess isolation for Docling.

Runs Docling conversion in an isolated subprocess with configurable resource limits
(RLIMIT_AS, RLIMIT_CPU, RLIMIT_FSIZE). The subprocess writes results to a temp
directory, which the parent reads and cleans up.
"""

import asyncio
import contextlib
import json
import os
import shutil
import signal
import sys
import tempfile
from pathlib import Path
from typing import Any

from crosscodex_ingestion.config import IngestionSettings


class ConversionError(Exception):
    """Raised when document conversion fails."""


class ConversionTimeout(Exception):
    """Raised when document conversion exceeds timeout."""


# Worker exit code meaning "Docling rejected the document for exceeding max_pages".
PAGE_LIMIT_EXIT_CODE = 3


class DocumentLimitError(ValueError):
    """Raised when a document exceeds max_pages or its output exceeds max_extraction_length.

    Subclasses ValueError because the message is safe and actionable for the client.
    """


# Worker script executed in the subprocess
_WORKER_SCRIPT = """
import json
import resource
import sys


def main():
    if len(sys.argv) < 9:
        print(
            "Usage: worker.py <input_path> <output_path> <format_hint> "
            "<rlimit_as> <rlimit_cpu> <rlimit_fsize> <artifacts_path> <max_pages>",
            file=sys.stderr,
        )
        sys.exit(1)

    input_path = sys.argv[1]
    output_path = sys.argv[2]
    format_hint = sys.argv[3]
    rlimit_as = int(sys.argv[4])
    rlimit_cpu = int(sys.argv[5])
    rlimit_fsize = int(sys.argv[6])
    artifacts_path = sys.argv[7]
    max_pages = int(sys.argv[8])

    # Set resource limits before importing Docling
    if rlimit_as > 0:
        resource.setrlimit(resource.RLIMIT_AS, (rlimit_as, rlimit_as))
    if rlimit_cpu > 0:
        resource.setrlimit(resource.RLIMIT_CPU, (rlimit_cpu, rlimit_cpu))
    if rlimit_fsize > 0:
        resource.setrlimit(resource.RLIMIT_FSIZE, (rlimit_fsize, rlimit_fsize))

    # Import after setting rlimits
    from crosscodex_ingestion.extractor import extract_elements
    from docling.datamodel.base_models import ConversionStatus, InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    # Models come from the pre-downloaded artifacts directory; HF_HUB_OFFLINE=1 in the
    # environment guarantees nothing is fetched from the network.
    pdf_options = PdfPipelineOptions(artifacts_path=artifacts_path)
    converter = DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pdf_options)})
    # Docling fails (rather than truncates) a document longer than max_num_pages. Any status
    # other than SUCCESS is a failure: a PARTIAL_SUCCESS would silently drop pages.
    result = converter.convert(input_path, max_num_pages=max_pages, raises_on_error=False)
    if result.status != ConversionStatus.SUCCESS:
        for error in result.errors:
            print(error.error_message, file=sys.stderr)
        # Docling's POLICY category also covers "file format not allowed", so check the
        # page count itself rather than reporting every policy failure as the page limit.
        if result.input.page_count > max_pages:
            sys.exit(@PAGE_LIMIT_EXIT_CODE@)
        sys.exit(1)
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
        json.dump(output_data, f, separators=(",", ":"))


if __name__ == "__main__":
    main()
""".replace("@PAGE_LIMIT_EXIT_CODE@", str(PAGE_LIMIT_EXIT_CODE))


def validate_artifacts_path(artifacts_path: str) -> None:
    """Raise ValueError unless *artifacts_path* is an existing directory.

    Called at server startup so a missing models directory fails closed with instructions
    instead of failing every conversion at request time.
    """
    if not artifacts_path:
        raise ValueError(
            "DOCLING_ARTIFACTS_PATH is not set. Download the Docling models with "
            "`docling-tools models download -o <dir>` and set DOCLING_ARTIFACTS_PATH=<dir>."
        )
    if not Path(artifacts_path).is_dir():
        raise ValueError(
            f"DOCLING_ARTIFACTS_PATH {artifacts_path!r} is not a directory. Download the Docling models with "
            "`docling-tools models download -o <dir>` and point DOCLING_ARTIFACTS_PATH at <dir>."
        )


def _kill_process_group(pid: int) -> None:
    """SIGKILL the process group led by *pid* (the worker started its own session)."""
    with contextlib.suppress(ProcessLookupError):  # group already gone
        os.killpg(pid, signal.SIGKILL)


async def convert_document(input_path: str, format_hint: str, settings: IngestionSettings) -> dict[str, Any]:
    """Convert a document using Docling in an isolated subprocess.

    The subprocess runs in its own session so that on timeout or cancellation the whole
    process group (Docling may spawn helpers) is SIGKILLed and reaped before this coroutine
    exits; the temp directory is removed either way.

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
    DocumentLimitError
        When the document exceeds max_pages or the output exceeds max_extraction_length.
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
        await asyncio.to_thread(shutil.copy2, input_path, temp_input_path)

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
            settings.docling_artifacts_path,
            str(settings.max_pages),
        ]

        # Minimal environment: HF_HUB_OFFLINE keeps the sandboxed worker off the network.
        env = {"PATH": os.environ.get("PATH", ""), "HF_HUB_OFFLINE": "1"}
        # The worker must import the same packages as this process; the FIPS image finds them
        # only via PYTHONPATH.
        if "PYTHONPATH" in os.environ:
            env["PYTHONPATH"] = os.environ["PYTHONPATH"]

        # Run subprocess in its own session/process group so the whole tree can be killed.
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            start_new_session=True,
        )
        try:
            async with asyncio.timeout(settings.max_processing_time):
                _, stderr_bytes = await proc.communicate()
        except TimeoutError as e:
            raise ConversionTimeout(f"Conversion timed out after {settings.max_processing_time} seconds") from e
        finally:
            # Runs on success, timeout, error and cancellation: never leave the worker running.
            if proc.returncode is None:
                _kill_process_group(proc.pid)
                await proc.wait()
        returncode = proc.returncode
        stderr = stderr_bytes.decode(errors="replace")

        if returncode == PAGE_LIMIT_EXIT_CODE:
            raise DocumentLimitError(f"Document exceeds the maximum of {settings.max_pages} pages")

        if returncode != 0:
            raise ConversionError(f"Conversion subprocess failed with exit code {returncode}. stderr: {stderr}")

        # Read output JSON
        if not output_path.exists():
            raise ConversionError("Conversion subprocess completed but did not create output file")

        # The worker serializes compactly, so the file size is the serialized output length.
        output_size = output_path.stat().st_size
        if output_size > settings.max_extraction_length:
            raise DocumentLimitError(
                f"Extracted output ({output_size} bytes) exceeds the maximum of {settings.max_extraction_length} bytes"
            )

        with Path(output_path).open() as f:
            output_data: dict[str, Any] = json.load(f)

        return output_data

    finally:
        # Clean up temp directory
        if temp_dir:
            shutil.rmtree(temp_dir, ignore_errors=True)
