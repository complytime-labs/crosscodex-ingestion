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
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, cast

from crosscodex_ingestion.config import IngestionSettings
from crosscodex_ingestion.html_profile import HtmlProfileRegistry
from crosscodex_ingestion.mapping import Mapping, MappingRegistry
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

# Worker exit code meaning "Docling's backend could not parse input of the format the service validated";
# the worker leaves the reason in ERROR_FILE_NAME.
INPUT_PARSE_EXIT_CODE = 4

# Worker exit code meaning "the fallback structurer found no controls in the layout"; the worker writes no
# error file, so nothing the subprocess says reaches the client.
NO_STRUCTURE_EXIT_CODE = 5

# Worker exit code meaning "the fallback structurer's controls repeat an ID"; the error file holds only the
# winning tier's name, which the parent accepts only if it is one of _GUESSED_METHODS.
REPEATED_IDS_EXIT_CODE = 6

# The fallback structurer's tiers, as metadata.structure_method names them.
_GUESSED_METHODS = ("heading", "table", "pattern")

# Longest parse-failure reason relayed to the client; Docling's own messages can be long tracebacks' worth.
_MAX_PARSE_REASON = 200

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


def _mapping_remedy(detected_format: str, mapping_ids: Sequence[str]) -> str:
    registered = ", ".join(mapping_ids) if mapping_ids else "none yet"
    return f"Convert it with a document mapping for {detected_format}; registered: {registered}."


class NoDocumentStructureError(ValueError):
    """Raised when the fallback structurer finds no controls in a document (the caller's to fix).

    The message is built from the validated format and registry mapping IDs only, never from document text.
    """

    def __init__(self, detected_format: str, mapping_ids: Sequence[str]) -> None:
        super().__init__(
            f"No controls found in this {detected_format} document: it has no headings, tables or numbered lines "
            f"the fallback structurer recognises. {_mapping_remedy(detected_format, mapping_ids)}"
        )


class RepeatedControlIdsError(ValueError):
    """Raised when the controls the fallback structurer found repeat an ID, so they are no catalog.

    The message is built from the validated format, the validated tier name and registry mapping IDs only.
    """

    def __init__(self, detected_format: str, method: str, mapping_ids: Sequence[str]) -> None:
        super().__init__(
            f"The fallback structurer found controls in this {detected_format} document (method {method}), but "
            f"their identifiers repeat, so they do not form a catalog. {_mapping_remedy(detected_format, mapping_ids)}"
        )


# Worker script executed in the subprocess
_WORKER_SCRIPT = (
    """
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
    import re
    from pathlib import Path

    from crosscodex_ingestion.extractor import extract_elements
    from crosscodex_ingestion.fallback import NoStructure, structure_layout, to_tree
    from crosscodex_ingestion.quality import measure_structure
    from docling.datamodel.base_models import ConversionStatus, InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, HTMLFormatOption, PdfFormatOption
    from docling_core.types.doc import DocItemLabel, DoclingDocument
    from crosscodex_ingestion.html_backend import html_backend_for
    from crosscodex_ingestion.html_profile import HtmlProfile
    from crosscodex_ingestion.structured import ERROR_FILE_NAME, emit_elements

    # The profiles are JSON on stdin; the backend class records which one a conversion applied.
    backend = html_backend_for([HtmlProfile.from_json(profile) for profile in json.load(sys.stdin)])

    if format_hint == "txt":
        # No Docling format reads text verbatim: its Markdown backend would turn "# x" into a heading
        # and its sniffer would call CSV-looking text a table. Plain text is paragraphs.
        doc = DoclingDocument(name=Path(input_path).stem)
        with open(input_path, encoding="utf-8", errors="replace") as f:
            for paragraph in re.split(r"\\n\\s*\\n", f.read()):
                if paragraph.strip():
                    doc.add_text(label=DocItemLabel.TEXT, text=paragraph.strip())
    else:
        # Models come from the pre-downloaded artifacts directory; HF_HUB_OFFLINE=1 in the
        # environment guarantees nothing is fetched from the network.
        pdf_options = PdfPipelineOptions(artifacts_path=artifacts_path)
        # Only the format the service validated is allowed, so Docling cannot sniff another one.
        input_format = {"pdf": InputFormat.PDF, "docx": InputFormat.DOCX, "html": InputFormat.HTML}[format_hint]
        converter = DocumentConverter(
            allowed_formats=[input_format],
            format_options={
                InputFormat.PDF: PdfFormatOption(pipeline_options=pdf_options),
                InputFormat.HTML: HTMLFormatOption(backend=backend),
            },
        )
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
            # The service validated the format, so what failed is parsing this input. Relay the
            # first error's first line, minus the temp directory it may name, for the caller to fix.
            reason = result.errors[0].error_message if result.errors else ""
            reason = reason.replace(str(Path(input_path).parent), "").strip()
            reason = reason.splitlines()[0] if reason else "unknown error"
            Path(output_path).with_name(ERROR_FILE_NAME).write_text(json.dumps({"reason": reason}))
            sys.exit(@INPUT_PARSE_EXIT_CODE@)
        doc = result.document

    # Extract elements; the extractor exports Markdown itself, only if it needs the fallback.
    extraction_result = extract_elements(doc, None, format_hint)
    # The quality signals describe the layout, so they are measured before the structure replaces it.
    layout_structure = measure_structure(extraction_result.elements)
    # A LayoutError (our extractor's bug) is left to crash the worker: the parent reports it as internal.
    try:
        items, method = structure_layout(extraction_result.elements)
    except NoStructure:
        sys.exit(@NO_STRUCTURE_EXIT_CODE@)
    if len({item.id for item in items}) != len(items):
        Path(output_path).with_name(ERROR_FILE_NAME).write_text(method)
        sys.exit(@REPEATED_IDS_EXIT_CODE@)
    elements = emit_elements(to_tree(items))
    metadata = extraction_result.metadata
    metadata["element_count"] = len(elements)
    metadata["structure_method"] = method
    metadata["structure"] = layout_structure

    # Write output
    output_data = {"elements": elements, "metadata": metadata}
    if backend.applied:
        output_data["metadata"]["html_profile"] = {"id": backend.applied.id, "sha256": backend.applied.sha256}
    if backend.ambiguous:
        output_data["conversion_warnings"] = ["html_profile_ambiguous"]

    with open(output_path, "w") as f:
        json.dump(output_data, f, separators=(",", ":"))


if __name__ == "__main__":
    main()
""".replace("@PAGE_LIMIT_EXIT_CODE@", str(PAGE_LIMIT_EXIT_CODE))
    .replace("@INPUT_PARSE_EXIT_CODE@", str(INPUT_PARSE_EXIT_CODE))
    .replace("@NO_STRUCTURE_EXIT_CODE@", str(NO_STRUCTURE_EXIT_CODE))
    .replace("@REPEATED_IDS_EXIT_CODE@", str(REPEATED_IDS_EXIT_CODE))
    .replace("@CPU_KILL_GRACE_SECONDS@", str(CPU_KILL_GRACE_SECONDS))
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
            "`uv run python scripts/download_models.py <dir>` and set DOCLING_ARTIFACTS_PATH=<dir>."
        )
    if not Path(artifacts_path).is_dir():
        raise ValueError(
            f"DOCLING_ARTIFACTS_PATH {artifacts_path!r} is not a directory. Download the Docling models with "
            "`uv run python scripts/download_models.py <dir>` and point DOCLING_ARTIFACTS_PATH at <dir>."
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
    """Run *script* on a copy of *input_path* in a resource-limited subprocess; return what it left behind.

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

        # Minimal environment: HF_HUB_OFFLINE stops Docling fetching models; it is not a network block.
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


async def convert_document(
    input_path: str,
    format_hint: str,
    settings: IngestionSettings,
    html_profiles: HtmlProfileRegistry,
    mappings: MappingRegistry,
) -> dict[str, Any]:
    """Convert a document using Docling in an isolated subprocess.

    The subprocess:
    1. Sets resource limits (RLIMIT_AS, RLIMIT_CPU, RLIMIT_FSIZE)
    2. Runs Docling conversion, with the HTML profiles read from stdin
    3. Calls extract_elements(), measures the layout's structure signals (metadata.structure)
    4. Structures the layout with the fallback structurer (metadata.structure_method) and emits its nodes
    5. Writes result JSON to temp directory

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
    html_profiles : HtmlProfileRegistry
        HTML profiles the worker may apply; sent to it on stdin.
    mappings : MappingRegistry
        The registry whose mappings for *format_hint* the structure errors name as the remedy.

    Returns
    -------
    dict
        Dictionary with 'schema_version', 'elements' and 'metadata' keys, plus a
        'conversion_warnings' list when the worker raised any (run_conversion consumes it).

    Raises
    ------
    ConversionTimeout
        When subprocess exceeds max_processing_time.
    DocumentLimitError
        When the document exceeds max_pages or the output exceeds max_extraction_length.
    ValueError
        When Docling cannot parse input of *format_hint* (the client sees the reason).
    NoDocumentStructureError
        When the fallback structurer finds no controls in the document.
    RepeatedControlIdsError
        When the controls the fallback structurer found repeat an ID.
    ConversionError
        When subprocess fails (non-zero exit code), including on a malformed layout.
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
        stdin_data=json.dumps([profile.to_json() for profile in html_profiles.profiles]).encode(),
    )
    if result.returncode == PAGE_LIMIT_EXIT_CODE:
        raise DocumentLimitError(f"Document exceeds the maximum of {settings.max_pages} pages")
    # The format is the one this service validated, never anything the worker wrote.
    if result.returncode == NO_STRUCTURE_EXIT_CODE:
        raise NoDocumentStructureError(format_hint, mappings.ids(format_hint))
    if result.returncode == REPEATED_IDS_EXIT_CODE:
        raise RepeatedControlIdsError(format_hint, _guessed_method(result.error_file), mappings.ids(format_hint))
    if result.returncode == INPUT_PARSE_EXIT_CODE:
        raise _parse_error(result.error_file, format_hint)
    _raise_for_exit(result, limits)
    return _stamp(result)


def _stamp(result: _WorkerResult) -> dict[str, Any]:
    """The worker's output with the service's fields: processing_time_ms, and schema_version first."""
    output = cast("dict[str, Any]", result.output)
    # Only this process sees the whole worker run: interpreter start, imports, conversion.
    output["metadata"]["processing_time_ms"] = result.processing_time_ms
    # The version leads the document so a reader sees it before anything it must interpret.
    return {"schema_version": SCHEMA_VERSION, **output}


def _guessed_method(error_file: bytes | None) -> str:
    """The tier the worker's error file names; ConversionError unless it is exactly one of _GUESSED_METHODS."""
    for method in _GUESSED_METHODS:
        if error_file == method.encode():
            return method
    raise ConversionError("The Docling worker reported repeated control IDs without naming a fallback tier")


def _parse_error(error_file: bytes | None, format_hint: str) -> ValueError:
    """The client-facing error for a Docling parse failure; ConversionError if the worker's reason is unusable.

    The reason file is the worker's output on untrusted input, so it is size-checked and truncated here.
    """
    try:
        if error_file is None:
            raise ValueError("no reason file")
        if len(error_file) > _MAX_ERROR_FILE_BYTES:
            raise ValueError("reason file too large")
        reason = json.loads(error_file)["reason"]
        if not isinstance(reason, str) or not reason:
            raise TypeError("reason is not a non-empty string")
    except (ValueError, KeyError, TypeError) as exc:
        raise ConversionError(f"The Docling worker reported a parse failure with no reason ({exc})") from exc
    return ValueError(f"The input could not be parsed as {format_hint}: {reason[:_MAX_PARSE_REASON]}")


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
