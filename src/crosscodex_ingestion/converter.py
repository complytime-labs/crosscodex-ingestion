"""
Document converter — subprocess isolation for Docling and for structured sources.

Runs each conversion in an isolated subprocess with configurable resource limits
(RLIMIT_AS, RLIMIT_CPU, RLIMIT_FSIZE) and a wall-clock timeout. The subprocess writes results to
a temp directory, which the parent reads and cleans up. Documents go to a Docling worker;
structured sources (JSON, CSV, XLSX with a mapping) to a lighter worker that runs
structured.run_job, imports no Docling or torch, and has its own lower limits.
"""

import asyncio
import contextlib
import json
import os
import shutil
import signal
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, cast

from crosscodex_ingestion.config import IngestionSettings
from crosscodex_ingestion.mapping import Mapping
from crosscodex_ingestion.output_schema import SCHEMA_VERSION
from crosscodex_ingestion.structured import (
    ERROR_FILE_NAME,
    INPUT_ERROR_EXIT_CODE,
    MAX_LISTED_PROBLEMS,
    StructuredLimits,
    StructuredSourceError,
)

# The structured worker's error file holds at most 20 problems with short source excerpts.
_MAX_ERROR_FILE_BYTES = 65_536
# Longest message or problem accepted from it; generated ones quote at most a few 64-character excerpts.
_MAX_ERROR_TEXT = 2048


class ConversionError(Exception):
    """Raised when document conversion fails."""


class ConversionTimeout(Exception):
    """Raised when document conversion exceeds timeout."""


# Worker exit code meaning "Docling rejected the document for exceeding max_pages".
PAGE_LIMIT_EXIT_CODE = 3

# The worker's hard CPU limit sits this far above the soft one. The kernel sends SIGXCPU at the
# soft limit, so a CPU death is told apart from a SIGKILL (OOM killer) by its signal.
CPU_KILL_GRACE_SECONDS = 5


class DocumentLimitError(ValueError):
    """Raised when a document exceeds max_pages or its output exceeds max_extraction_length.

    Subclasses ValueError because the message is safe and actionable for the client.
    *redacted_message* is the message without the caller's data, for the audit stream and logs; it
    is the message itself when that quotes none.
    """

    def __init__(self, message: str, redacted_message: str | None = None) -> None:
        super().__init__(message)
        self.redacted_message = redacted_message or message


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
        resource.setrlimit(resource.RLIMIT_CPU, (rlimit_cpu, rlimit_cpu + @CPU_KILL_GRACE_SECONDS@))
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

    # Extract elements; the extractor exports Markdown itself, only if it needs the fallback.
    extraction_result = extract_elements(doc, None, format_hint)

    # Write output
    output_data = {
        "elements": extraction_result.elements,
        "metadata": extraction_result.metadata,
    }

    with open(output_path, "w") as f:
        json.dump(output_data, f, separators=(",", ":"))


if __name__ == "__main__":
    main()
""".replace("@PAGE_LIMIT_EXIT_CODE@", str(PAGE_LIMIT_EXIT_CODE)).replace(
    "@CPU_KILL_GRACE_SECONDS@", str(CPU_KILL_GRACE_SECONDS)
)

# Structured-source worker: limits first, then structured.run_job with the job read from stdin.
_STRUCTURED_WORKER_SCRIPT = """
import json
import resource
import sys


def main():
    input_path, output_path = sys.argv[1], sys.argv[2]
    rlimit_as, rlimit_cpu, rlimit_fsize = (int(v) for v in sys.argv[3:6])
    if rlimit_as > 0:
        resource.setrlimit(resource.RLIMIT_AS, (rlimit_as, rlimit_as))
    if rlimit_cpu > 0:
        resource.setrlimit(resource.RLIMIT_CPU, (rlimit_cpu, rlimit_cpu + @CPU_KILL_GRACE_SECONDS@))
    if rlimit_fsize > 0:
        resource.setrlimit(resource.RLIMIT_FSIZE, (rlimit_fsize, rlimit_fsize))

    from crosscodex_ingestion.structured import run_job

    sys.exit(run_job(input_path, output_path, json.load(sys.stdin)))


if __name__ == "__main__":
    main()
""".replace("@CPU_KILL_GRACE_SECONDS@", str(CPU_KILL_GRACE_SECONDS))


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


@dataclass(frozen=True)
class _WorkerLimits:
    """Resource limits of one kind of worker, and the settings that name them in error messages."""

    name: str
    timeout: int
    timeout_setting: str
    rlimit_as_mb: int
    rlimit_as_setting: str
    rlimit_cpu: int
    rlimit_cpu_setting: str


@dataclass(frozen=True)
class _WorkerResult:
    returncode: int
    stderr: str
    processing_time_ms: float
    output: dict[str, Any] | None  # the parsed output file, when the worker exited 0
    error_file: bytes | None  # the worker's error file (at most _MAX_ERROR_FILE_BYTES + 1 bytes), if any


async def _run_worker(
    script: str,
    input_path: str,
    args: list[str],
    limits: _WorkerLimits,
    settings: IngestionSettings,
    stdin_data: bytes | None = None,
) -> _WorkerResult:
    """Run *script* on a copy of *input_path* in a sandboxed subprocess; return what it left behind.

    The command is [python, script, input, output, *args]; *args* include the worker's rlimits
    (see _rlimits). The subprocess runs in its own session so that on
    timeout or cancellation the whole process group is SIGKILLed and reaped before this coroutine
    exits; the temp directory is removed either way.

    Raises ConversionTimeout after limits.timeout seconds. When the worker exits 0 it must have
    written the output file, no larger than max_extraction_length (ConversionError,
    DocumentLimitError otherwise); any other exit is for the caller to interpret.
    """
    temp_dir = tempfile.mkdtemp(prefix="crosscodex_conv_")
    try:
        temp_input_path = Path(temp_dir) / Path(input_path).name
        await asyncio.to_thread(shutil.copy2, input_path, temp_input_path)
        output_path = Path(temp_dir) / "output.json"
        worker_script_path = Path(temp_dir) / "worker.py"
        worker_script_path.write_text(script)

        cmd = [
            sys.executable,
            str(worker_script_path),
            str(temp_input_path),
            str(output_path),
            *args,
        ]

        # Minimal environment: HF_HUB_OFFLINE keeps the sandboxed worker off the network.
        env = {"PATH": os.environ.get("PATH", ""), "HF_HUB_OFFLINE": "1"}
        # The worker must import the same packages as this process; the FIPS image finds them
        # only via PYTHONPATH.
        if "PYTHONPATH" in os.environ:
            env["PYTHONPATH"] = os.environ["PYTHONPATH"]

        # Run subprocess in its own session/process group so the whole tree can be killed.
        started = time.monotonic()
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE if stdin_data is not None else None,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            start_new_session=True,
        )
        try:
            async with asyncio.timeout(limits.timeout):
                _, stderr_bytes = await proc.communicate(input=stdin_data)
        except TimeoutError as e:
            raise ConversionTimeout(f"Conversion timed out after {limits.timeout} seconds") from e
        finally:
            # Runs on success, timeout, error and cancellation: never leave the worker running.
            if proc.returncode is None:
                _kill_process_group(proc.pid)
                await proc.wait()
        processing_time_ms = round((time.monotonic() - started) * 1000, 2)
        # The finally block above reaps the worker, so returncode is always set here.
        returncode = cast("int", proc.returncode)
        stderr = stderr_bytes.decode(errors="replace")

        output: dict[str, Any] | None = None
        if returncode == 0:
            if not output_path.exists():
                raise ConversionError("Conversion subprocess completed but did not create output file")
            # The worker serializes compactly, so the file size is the serialized output length.
            output_size = output_path.stat().st_size
            if output_size > settings.max_extraction_length:
                raise DocumentLimitError(
                    f"Extracted output ({output_size} bytes) exceeds the maximum of "
                    f"{settings.max_extraction_length} bytes"
                )
            with output_path.open() as f:
                output = json.load(f)

        error_path = Path(temp_dir) / ERROR_FILE_NAME
        error_file = None
        if error_path.exists():
            with error_path.open("rb") as f:
                error_file = f.read(_MAX_ERROR_FILE_BYTES + 1)
        return _WorkerResult(returncode, stderr, processing_time_ms, output, error_file)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def _rlimits(limits: _WorkerLimits, settings: IngestionSettings) -> list[str]:
    """RLIMIT_AS, RLIMIT_CPU and RLIMIT_FSIZE for the worker's command line, in bytes and seconds (0: none)."""
    return [str(limits.rlimit_as_mb * 1024 * 1024), str(limits.rlimit_cpu), str(settings.rlimit_fsize_mb * 1024 * 1024)]


def _raise_for_exit(result: _WorkerResult, limits: _WorkerLimits) -> None:
    """Raise for a worker that did not exit 0; CPU deaths are limit errors, other deaths internal errors."""
    returncode = result.returncode
    if returncode == -signal.SIGXCPU:
        raise DocumentLimitError(
            f"Conversion exceeded the CPU limit of {limits.rlimit_cpu} CPU-seconds ({limits.rlimit_cpu_setting})"
        )
    if returncode < 0:
        signum = -returncode
        try:
            signal_name = signal.Signals(signum).name
        except ValueError:
            signal_name = f"signal {signum}"
        if signum == signal.SIGKILL:
            cause = "; most likely out of memory (container memory limit or kernel OOM killer)"
        elif signum == signal.SIGSEGV and limits.rlimit_as_mb > 0:
            cause = (
                f"; {limits.name} crashes this way when {limits.rlimit_as_setting} ({limits.rlimit_as_mb}) is too low"
            )
        else:
            cause = ""
        raise ConversionError(f"Conversion subprocess was killed by {signal_name}{cause}. stderr: {result.stderr}")
    if returncode != 0:
        raise ConversionError(f"Conversion subprocess failed with exit code {returncode}. stderr: {result.stderr}")


async def convert_document(input_path: str, format_hint: str, settings: IngestionSettings) -> dict[str, Any]:
    """Convert a document using Docling in an isolated subprocess.

    The subprocess:
    1. Sets resource limits (RLIMIT_AS, RLIMIT_CPU, RLIMIT_FSIZE)
    2. Runs Docling conversion
    3. Calls extract_elements()
    4. Writes result JSON to temp directory

    The parent reads the JSON, adds processing_time_ms and schema_version, and returns the
    dict. It does not validate it: run_conversion does, right before storing it.

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
        Dictionary with 'schema_version', 'elements' and 'metadata' keys.

    Raises
    ------
    ConversionTimeout
        When subprocess exceeds max_processing_time.
    DocumentLimitError
        When the document exceeds max_pages or the output exceeds max_extraction_length.
    ConversionError
        When subprocess fails (non-zero exit code).
    """
    limits = _WorkerLimits(
        "Docling",
        settings.max_processing_time,
        "MAX_PROCESSING_TIME",
        settings.rlimit_as_mb,
        "RLIMIT_AS_MB",
        settings.rlimit_cpu,
        "RLIMIT_CPU",
    )
    result = await _run_worker(
        _WORKER_SCRIPT,
        input_path,
        [format_hint, *_rlimits(limits, settings), settings.docling_artifacts_path, str(settings.max_pages)],
        limits,
        settings,
    )
    if result.returncode == PAGE_LIMIT_EXIT_CODE:
        raise DocumentLimitError(f"Document exceeds the maximum of {settings.max_pages} pages")
    _raise_for_exit(result, limits)
    return _stamp(result)


def _stamp(result: _WorkerResult) -> dict[str, Any]:
    """The worker's output with the service's fields: processing_time_ms, and schema_version first."""
    output = cast("dict[str, Any]", result.output)
    # Only this process sees the whole worker run: interpreter start, imports, conversion.
    output["metadata"]["processing_time_ms"] = result.processing_time_ms
    # The version leads the document so a reader sees it before anything it must interpret.
    return {"schema_version": SCHEMA_VERSION, **output}


def _input_error(error_file: bytes, mapping: Mapping) -> ValueError:
    """The client-facing error for the structured worker's error file; ConversionError if it is malformed.

    The worker parses untrusted input, so its error file is untrusted too: it must be small, and
    the message the client sees is rebuilt here from the listed problems.
    """
    try:
        if len(error_file) > _MAX_ERROR_FILE_BYTES:
            raise ValueError("too large")
        error = json.loads(error_file)
        kind, message, details = error["kind"], error["message"], error["details"]
        if not isinstance(message, str) or not isinstance(details, dict):
            raise TypeError("wrong types")
        if kind == "limit":
            redacted = error["redacted_message"]
            if not isinstance(redacted, str) or max(len(message), len(redacted)) > _MAX_ERROR_TEXT:
                raise ValueError("bad limit message")
            return DocumentLimitError(message, redacted)
        if kind != "source" or details.get("mapping") != mapping.id:
            raise ValueError("unknown kind or mapping")
        count = int(details["count"])
        listed = range(1, min(count, MAX_LISTED_PROBLEMS) + 1)
        problems = [details[f"problem.{n}"] for n in listed]
        locations = [details[f"location.{n}"] for n in listed]
        if count < 1 or not all(isinstance(t, str) and len(t) <= _MAX_ERROR_TEXT for t in problems + locations):
            raise ValueError("bad problems")
        return StructuredSourceError(mapping.id, problems, locations, count)
    except (ValueError, KeyError, TypeError) as exc:
        raise ConversionError(f"The structured worker wrote a malformed error file ({exc})") from exc


async def convert_structured(input_path: str, mapping: Mapping, settings: IngestionSettings) -> dict[str, Any]:
    """Convert a structured source through *mapping* in an isolated subprocess (structured.run_job).

    The worker imports no Docling or torch and runs under the STRUCTURED_* limits. The job (mapping
    document, sha256, input limits) goes to it on stdin.

    Raises
    ------
    StructuredSourceError
        When the source does not fit the mapping (the client sees the problems).
    DocumentLimitError
        When the source exceeds an input limit, the CPU limit, or the output exceeds max_extraction_length.
    ConversionTimeout
        When the worker exceeds structured_max_processing_time.
    ConversionError
        When the worker fails otherwise.
    """
    limits = _WorkerLimits(
        "the structured worker",
        settings.structured_max_processing_time,
        "STRUCTURED_MAX_PROCESSING_TIME",
        settings.structured_rlimit_as_mb,
        "STRUCTURED_RLIMIT_AS_MB",
        settings.structured_rlimit_cpu,
        "STRUCTURED_RLIMIT_CPU",
    )
    job = {
        "mapping": mapping.document,
        "mapping_sha256": mapping.sha256,
        "limits": asdict(StructuredLimits.from_settings(settings)),
    }
    result = await _run_worker(
        _STRUCTURED_WORKER_SCRIPT,
        input_path,
        _rlimits(limits, settings),
        limits,
        settings,
        stdin_data=json.dumps(job).encode(),
    )
    if result.returncode == INPUT_ERROR_EXIT_CODE and result.error_file is not None:
        raise _input_error(result.error_file, mapping)
    _raise_for_exit(result, limits)
    return _stamp(result)
