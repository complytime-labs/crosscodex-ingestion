"""Tests for converter module — subprocess isolation."""

import asyncio
import io
import json
import os
import runpy
import signal
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from crosscodex_ingestion import converter
from crosscodex_ingestion.config import IngestionSettings
from crosscodex_ingestion.converter import (
    PAGE_LIMIT_EXIT_CODE,
    ConversionError,
    ConversionTimeout,
    DocumentLimitError,
    NoDocumentStructureError,
    RepeatedControlIdsError,
    convert_document,
    convert_structured,
    validate_artifacts_path,
)
from crosscodex_ingestion.fallback import LayoutError
from crosscodex_ingestion.html_profile import HtmlProfile, HtmlProfileRegistry, load_html_profiles
from crosscodex_ingestion.mapping import Mapping, MappingRegistry, load_registry
from crosscodex_ingestion.output_schema import SCHEMA_VERSION
from crosscodex_ingestion.structured import ERROR_FILE_NAME, INPUT_ERROR_EXIT_CODE, StructuredSourceError
from tests.unit.process_helpers import SLEEPER_SCRIPT, is_running, wait_for_pids

PROFILES = load_html_profiles("")
MAPPINGS = load_registry("")


def _proc(returncode: int, stderr: str = "") -> Mock:
    """Fake asyncio subprocess that exits immediately with *returncode*."""
    proc = Mock(returncode=returncode, pid=1)
    proc.communicate = AsyncMock(return_value=(b"", stderr.encode()))
    return proc


class TestConvertDocument:
    """Test the convert_document function."""

    @patch("crosscodex_ingestion.converter.asyncio.create_subprocess_exec", new_callable=AsyncMock)
    @patch("crosscodex_ingestion.converter.shutil.copy2")
    async def test_successful_conversion_returns_dict_with_elements_and_metadata(self, mock_copy2, mock_subprocess_run):
        """Successful conversion returns dict with elements and metadata keys."""

        # Arrange
        # Mock the subprocess to write output file
        def write_output_file(*cmd, **kwargs):
            # Extract output path from command args
            # cmd format: [python, script_path, input_path, output_path, format_hint, rlimits...]
            output_path = Path(cmd[3])
            output_data = {
                "elements": [{"element_id": "e-001", "type": "heading", "content": "Test"}],
                "metadata": {"element_count": 1, "detected_format": "pdf", "processing_time_ms": 123.45},
            }
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with Path(output_path).open("w") as f:
                json.dump(output_data, f)
            return _proc(returncode=0)

        mock_subprocess_run.side_effect = write_output_file

        settings = IngestionSettings()

        # Act
        result = await convert_document("/path/to/doc.pdf", "pdf", settings, PROFILES, MAPPINGS)

        # Assert
        assert "elements" in result
        assert "metadata" in result
        assert len(result["elements"]) == 1
        assert result["elements"][0]["element_id"] == "e-001"
        assert result["metadata"]["element_count"] == 1

    @patch("crosscodex_ingestion.converter.asyncio.create_subprocess_exec", new_callable=AsyncMock)
    @patch("crosscodex_ingestion.converter.shutil.copy2")
    async def test_processing_time_covers_the_whole_worker_run(self, mock_copy2, mock_subprocess_run):
        """processing_time_ms is the worker's wall time, not a sub-step the worker timed itself."""
        worker_seconds = 0.2

        def slow_worker(*cmd, **kwargs):
            Path(cmd[3]).write_text(json.dumps({"elements": [], "metadata": {"processing_time_ms": 1.0}}))
            proc = _proc(returncode=0)

            async def communicate(input=None):  # noqa: A002 (asyncio.subprocess.Process.communicate)
                await asyncio.sleep(worker_seconds)
                return b"", b""

            proc.communicate = communicate
            return proc

        mock_subprocess_run.side_effect = slow_worker

        result = await convert_document("/path/to/doc.pdf", "pdf", IngestionSettings(), PROFILES, MAPPINGS)

        assert result["metadata"]["processing_time_ms"] >= worker_seconds * 1000

    @patch("crosscodex_ingestion.converter.asyncio.create_subprocess_exec", new_callable=AsyncMock)
    @patch("crosscodex_ingestion.converter.shutil.copy2")
    async def test_output_is_stamped_with_the_schema_version_first(self, mock_copy2, mock_subprocess_run):
        """The parent, not the sandboxed worker, declares which schema version the output follows."""

        def worker(*cmd, **kwargs):
            Path(cmd[3]).write_text(json.dumps({"elements": [], "metadata": {}}))
            return _proc(returncode=0)

        mock_subprocess_run.side_effect = worker

        result = await convert_document("/path/to/doc.pdf", "pdf", IngestionSettings(), PROFILES, MAPPINGS)

        assert next(iter(result)) == "schema_version"
        assert result["schema_version"] == SCHEMA_VERSION

    @patch("crosscodex_ingestion.converter.asyncio.create_subprocess_exec", new_callable=AsyncMock)
    @patch("crosscodex_ingestion.converter.shutil.copy2")
    async def test_subprocess_crash_raises_conversion_error(self, mock_copy2, mock_subprocess_run):
        """Subprocess crash (non-zero exit) raises ConversionError."""
        # Arrange
        mock_subprocess_run.return_value = _proc(returncode=137, stderr="Memory limit exceeded")

        settings = IngestionSettings()

        # Act & Assert
        with pytest.raises(ConversionError) as exc_info:
            await convert_document("/path/to/doc.pdf", "pdf", settings, PROFILES, MAPPINGS)

        assert "exit code 137" in str(exc_info.value).lower()

    @patch("crosscodex_ingestion.converter.asyncio.create_subprocess_exec", new_callable=AsyncMock)
    @patch("crosscodex_ingestion.converter.shutil.copy2")
    async def test_temp_directory_cleaned_up_after_success(self, mock_copy2, mock_subprocess_run):
        """Temp directory is cleaned up after successful conversion."""
        # Arrange
        temp_dirs_created = []

        original_mkdtemp = tempfile.mkdtemp

        def track_mkdtemp(*args, **kwargs):
            temp_dir = original_mkdtemp(*args, **kwargs)
            temp_dirs_created.append(temp_dir)
            return temp_dir

        def write_output_file(*cmd, **kwargs):
            output_path = Path(cmd[3])
            output_data = {"elements": [], "metadata": {}}
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with Path(output_path).open("w") as f:
                json.dump(output_data, f)
            return _proc(returncode=0)

        mock_subprocess_run.side_effect = write_output_file

        with patch("crosscodex_ingestion.converter.tempfile.mkdtemp", side_effect=track_mkdtemp):
            settings = IngestionSettings()

            # Act
            await convert_document("/path/to/doc.pdf", "pdf", settings, PROFILES, MAPPINGS)

        # Assert - temp directory should not exist
        assert len(temp_dirs_created) == 1
        assert not Path(temp_dirs_created[0]).exists()

    @patch("crosscodex_ingestion.converter.asyncio.create_subprocess_exec", new_callable=AsyncMock)
    @patch("crosscodex_ingestion.converter.shutil.copy2")
    async def test_temp_directory_cleaned_up_after_failure(self, mock_copy2, mock_subprocess_run):
        """Temp directory is cleaned up after conversion failure."""
        # Arrange
        temp_dirs_created = []

        original_mkdtemp = tempfile.mkdtemp

        def track_mkdtemp(*args, **kwargs):
            temp_dir = original_mkdtemp(*args, **kwargs)
            temp_dirs_created.append(temp_dir)
            return temp_dir

        mock_subprocess_run.return_value = _proc(returncode=1, stderr="Error")

        with patch("crosscodex_ingestion.converter.tempfile.mkdtemp", side_effect=track_mkdtemp):
            settings = IngestionSettings()

            # Act & Assert
            with pytest.raises(ConversionError):
                await convert_document("/path/to/doc.pdf", "pdf", settings, PROFILES, MAPPINGS)

        # Assert - temp directory should not exist
        assert len(temp_dirs_created) == 1
        assert not Path(temp_dirs_created[0]).exists()

    @patch("crosscodex_ingestion.converter.asyncio.create_subprocess_exec", new_callable=AsyncMock)
    @patch("crosscodex_ingestion.converter.shutil.copy2")
    async def test_rlimits_passed_to_subprocess_when_nonzero(self, mock_copy2, mock_subprocess_run):
        """Rlimits are passed to subprocess script when non-zero."""

        # Arrange
        def capture_subprocess_args(*cmd, **kwargs):
            output_path = Path(cmd[3])
            output_data = {"elements": [], "metadata": {}}
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with Path(output_path).open("w") as f:
                json.dump(output_data, f)
            return _proc(returncode=0)

        mock_subprocess_run.side_effect = capture_subprocess_args

        settings = IngestionSettings(rlimit_as_mb=2048, rlimit_cpu=120, rlimit_fsize_mb=50)

        # Act
        await convert_document("/path/to/doc.pdf", "pdf", settings, PROFILES, MAPPINGS)

        # Assert
        mock_subprocess_run.assert_called_once()
        call_args = mock_subprocess_run.call_args

        # Verify the command includes rlimit values
        cmd = call_args[0]
        assert sys.executable in cmd
        # Args should include: input_path, output_path, format_hint, rlimit_as, rlimit_cpu, rlimit_fsize
        # The rlimit values should be in bytes for AS and FSIZE
        args_list = cmd
        # Check that rlimit values appear in the args (converted to bytes)
        rlimit_as_bytes = str(2048 * 1024 * 1024)
        rlimit_cpu_seconds = "120"
        rlimit_fsize_bytes = str(50 * 1024 * 1024)

        assert rlimit_as_bytes in args_list
        assert rlimit_cpu_seconds in args_list
        assert rlimit_fsize_bytes in args_list

    @patch("crosscodex_ingestion.converter.asyncio.create_subprocess_exec", new_callable=AsyncMock)
    @patch("crosscodex_ingestion.converter.shutil.copy2")
    async def test_rlimits_set_to_zero_when_unlimited(self, mock_copy2, mock_subprocess_run):
        """Rlimits are passed as 0 when settings specify unlimited (0)."""

        # Arrange
        def capture_subprocess_args(*cmd, **kwargs):
            output_path = Path(cmd[3])
            output_data = {"elements": [], "metadata": {}}
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with Path(output_path).open("w") as f:
                json.dump(output_data, f)
            return _proc(returncode=0)

        mock_subprocess_run.side_effect = capture_subprocess_args

        settings = IngestionSettings(rlimit_as_mb=0, rlimit_cpu=0, rlimit_fsize_mb=0)

        # Act
        await convert_document("/path/to/doc.pdf", "pdf", settings, PROFILES, MAPPINGS)

        # Assert
        mock_subprocess_run.assert_called_once()
        call_args = mock_subprocess_run.call_args
        cmd = call_args[0]

        # Should have three "0" values for the rlimits
        # The args are: script_path, input_path, output_path, format_hint, rlimit_as, rlimit_cpu, rlimit_fsize
        # The three rlimits precede the artifacts path and should be "0"
        assert list(cmd[-5:-2]) == ["0", "0", "0"]

    @patch("crosscodex_ingestion.converter.asyncio.create_subprocess_exec", new_callable=AsyncMock)
    @patch("crosscodex_ingestion.converter.shutil.copy2")
    async def test_minimal_environment_passed_to_child(self, mock_copy2, mock_subprocess_run):
        """Minimal environment is passed to child (no HOME, USER, etc.)."""

        # Arrange
        def capture_env(*cmd, **kwargs):
            output_path = Path(cmd[3])
            output_data = {"elements": [], "metadata": {}}
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with Path(output_path).open("w") as f:
                json.dump(output_data, f)
            return _proc(returncode=0)

        mock_subprocess_run.side_effect = capture_env
        settings = IngestionSettings()

        # Act
        await convert_document("/path/to/doc.pdf", "pdf", settings, PROFILES, MAPPINGS)

        # Assert
        mock_subprocess_run.assert_called_once()
        call_kwargs = mock_subprocess_run.call_args[1]

        # Verify env is passed and minimal
        assert "env" in call_kwargs
        env = call_kwargs["env"]

        # Should have PATH but not HOME, USER, etc.
        assert "PATH" in env
        assert "HOME" not in env
        assert "USER" not in env
        assert "LOGNAME" not in env
        assert "SHELL" not in env

    def test_real_subprocess_ipc_mechanism(self):
        """Integration test: verify IPC mechanism works with real subprocess."""
        # This test uses a real subprocess to validate the complete flow
        with tempfile.TemporaryDirectory() as temp_dir:
            # Create a minimal worker script
            worker_script = Path(temp_dir) / "test_worker.py"
            output_file = Path(temp_dir) / "output.json"

            worker_code = f"""
import json
import sys

# Write test output
output_data = {{"test": "success", "args": sys.argv[1:]}}
with open("{output_file}", "w") as f:
    json.dump(output_data, f)
"""
            worker_script.write_text(worker_code)

            # Run the worker script
            result = subprocess.run(
                [sys.executable, str(worker_script), "arg1", "arg2"],
                capture_output=True,
                timeout=5,
                check=False,
                env={"PATH": os.environ.get("PATH", "")},
            )

            # Verify the output file was created
            assert result.returncode == 0
            assert output_file.exists()

            # Verify the content
            with Path(output_file).open() as f:
                data = json.load(f)

            assert data["test"] == "success"
            assert data["args"] == ["arg1", "arg2"]


class TestExceptionClasses:
    """Test custom exception classes."""

    def test_conversion_error_is_exception(self):
        """ConversionError is an Exception."""
        exc = ConversionError("test error")
        assert isinstance(exc, Exception)
        assert str(exc) == "test error"

    def test_conversion_timeout_is_exception(self):
        """ConversionTimeout is an Exception."""
        exc = ConversionTimeout("test timeout")
        assert isinstance(exc, Exception)
        assert str(exc) == "test timeout"


def _capture_cmd_and_env(mock_subprocess_run):
    """Make the mocked subprocess write a valid output file; return the recorded call kwargs."""

    def write_output_file(*cmd, **kwargs):
        Path(cmd[3]).write_text(json.dumps({"elements": [], "metadata": {}}))
        return _proc(returncode=0)

    mock_subprocess_run.side_effect = write_output_file


class TestOfflineModels:
    """The sandboxed worker must use pre-downloaded models and never reach the network."""

    @patch("crosscodex_ingestion.converter.asyncio.create_subprocess_exec", new_callable=AsyncMock)
    @patch("crosscodex_ingestion.converter.shutil.copy2")
    async def test_artifacts_path_passed_to_worker(self, mock_copy2, mock_subprocess_run):
        _capture_cmd_and_env(mock_subprocess_run)
        settings = IngestionSettings(docling_artifacts_path="/opt/docling-models")

        await convert_document("/path/to/doc.pdf", "pdf", settings, PROFILES, MAPPINGS)

        assert "/opt/docling-models" in mock_subprocess_run.call_args[0]

    @patch("crosscodex_ingestion.converter.asyncio.create_subprocess_exec", new_callable=AsyncMock)
    @patch("crosscodex_ingestion.converter.shutil.copy2")
    async def test_worker_env_forces_hf_offline_and_leaks_nothing_else(
        self, mock_copy2, mock_subprocess_run, monkeypatch
    ):
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "should-not-leak")
        _capture_cmd_and_env(mock_subprocess_run)

        await convert_document(
            "/path/to/doc.pdf", "pdf", IngestionSettings(docling_artifacts_path="/m"), PROFILES, MAPPINGS
        )

        env = mock_subprocess_run.call_args.kwargs["env"]
        assert env["HF_HUB_OFFLINE"] == "1"
        assert set(env) <= {"PATH", "HF_HUB_OFFLINE", "PYTHONPATH"}

    @patch("crosscodex_ingestion.converter.asyncio.create_subprocess_exec", new_callable=AsyncMock)
    @patch("crosscodex_ingestion.converter.shutil.copy2")
    async def test_worker_inherits_pythonpath_so_it_can_import_the_package(
        self, mock_copy2, mock_subprocess_run, monkeypatch
    ):
        """The FIPS image locates the package and Docling only through PYTHONPATH."""
        monkeypatch.setenv("PYTHONPATH", "/opt/app-root/lib/python3.12/site-packages")
        _capture_cmd_and_env(mock_subprocess_run)

        await convert_document(
            "/path/to/doc.pdf", "pdf", IngestionSettings(docling_artifacts_path="/m"), PROFILES, MAPPINGS
        )

        assert mock_subprocess_run.call_args.kwargs["env"]["PYTHONPATH"] == "/opt/app-root/lib/python3.12/site-packages"

    @patch("crosscodex_ingestion.converter.asyncio.create_subprocess_exec", new_callable=AsyncMock)
    @patch("crosscodex_ingestion.converter.shutil.copy2")
    async def test_worker_env_has_no_pythonpath_when_parent_has_none(
        self, mock_copy2, mock_subprocess_run, monkeypatch
    ):
        monkeypatch.delenv("PYTHONPATH", raising=False)
        _capture_cmd_and_env(mock_subprocess_run)

        await convert_document(
            "/path/to/doc.pdf", "pdf", IngestionSettings(docling_artifacts_path="/m"), PROFILES, MAPPINGS
        )

        assert "PYTHONPATH" not in mock_subprocess_run.call_args.kwargs["env"]


class TestValidateArtifactsPath:
    def test_unset_path_is_refused_with_download_instructions(self):
        with pytest.raises(ValueError, match=r"DOCLING_ARTIFACTS_PATH.*scripts/download_models.py"):
            validate_artifacts_path("")

    def test_missing_directory_is_refused(self, tmp_path):
        with pytest.raises(ValueError, match="not a directory"):
            validate_artifacts_path(str(tmp_path / "absent"))

    def test_regular_file_is_refused(self, tmp_path):
        file_path = tmp_path / "f"
        file_path.write_text("x")
        with pytest.raises(ValueError, match="not a directory"):
            validate_artifacts_path(str(file_path))

    def test_existing_directory_is_accepted(self, tmp_path):
        validate_artifacts_path(str(tmp_path))


class TestDocumentLimits:
    """max_pages and max_extraction_length are enforced, never silently truncated."""

    @patch("crosscodex_ingestion.converter.asyncio.create_subprocess_exec", new_callable=AsyncMock)
    @patch("crosscodex_ingestion.converter.shutil.copy2")
    async def test_max_pages_passed_to_worker(self, mock_copy2, mock_subprocess_run):
        _capture_cmd_and_env(mock_subprocess_run)

        await convert_document("/path/to/doc.pdf", "pdf", IngestionSettings(max_pages=42), PROFILES, MAPPINGS)

        assert mock_subprocess_run.call_args[0][-1] == "42"

    @patch("crosscodex_ingestion.converter.asyncio.create_subprocess_exec", new_callable=AsyncMock)
    @patch("crosscodex_ingestion.converter.shutil.copy2")
    async def test_page_limit_exit_code_raises_limit_error_without_leaking_stderr(
        self, mock_copy2, mock_subprocess_run
    ):
        mock_subprocess_run.return_value = _proc(returncode=PAGE_LIMIT_EXIT_CODE, stderr="Traceback /srv/secret/path")

        with pytest.raises(DocumentLimitError) as exc_info:
            await convert_document("/path/to/doc.pdf", "pdf", IngestionSettings(max_pages=10), PROFILES, MAPPINGS)

        assert "10 pages" in str(exc_info.value)
        assert "/srv/secret" not in str(exc_info.value)
        assert isinstance(exc_info.value, ValueError)

    @patch("crosscodex_ingestion.converter.asyncio.create_subprocess_exec", new_callable=AsyncMock)
    @patch("crosscodex_ingestion.converter.shutil.copy2")
    async def test_sigxcpu_death_is_reported_as_cpu_limit_with_the_setting(self, mock_copy2, mock_subprocess_run):
        mock_subprocess_run.return_value = _proc(returncode=-signal.SIGXCPU)

        with pytest.raises(DocumentLimitError) as exc_info:
            await convert_document("/path/to/doc.pdf", "pdf", IngestionSettings(rlimit_cpu=600), PROFILES, MAPPINGS)

        assert "CPU limit" in str(exc_info.value)
        assert "RLIMIT_CPU" in str(exc_info.value)
        assert "600" in str(exc_info.value)

    @patch("crosscodex_ingestion.converter.asyncio.create_subprocess_exec", new_callable=AsyncMock)
    @patch("crosscodex_ingestion.converter.shutil.copy2")
    async def test_sigkill_death_names_memory_as_the_likely_limit(self, mock_copy2, mock_subprocess_run):
        """CPU deaths arrive as SIGXCPU, so an unexplained SIGKILL is most likely the OOM killer."""
        mock_subprocess_run.return_value = _proc(returncode=-signal.SIGKILL)

        with pytest.raises(ConversionError) as exc_info:
            await convert_document("/path/to/doc.pdf", "pdf", IngestionSettings(), PROFILES, MAPPINGS)

        assert not isinstance(exc_info.value, DocumentLimitError)
        assert "SIGKILL" in str(exc_info.value)
        assert "memory" in str(exc_info.value)

    @patch("crosscodex_ingestion.converter.asyncio.create_subprocess_exec", new_callable=AsyncMock)
    @patch("crosscodex_ingestion.converter.shutil.copy2")
    async def test_sigsegv_death_names_the_address_space_limit(self, mock_copy2, mock_subprocess_run):
        mock_subprocess_run.return_value = _proc(returncode=-signal.SIGSEGV)

        with pytest.raises(ConversionError) as exc_info:
            await convert_document("/path/to/doc.pdf", "pdf", IngestionSettings(), PROFILES, MAPPINGS)

        assert "SIGSEGV" in str(exc_info.value)
        assert "RLIMIT_AS_MB" in str(exc_info.value)

    @patch("crosscodex_ingestion.converter.asyncio.create_subprocess_exec", new_callable=AsyncMock)
    @patch("crosscodex_ingestion.converter.shutil.copy2")
    async def test_sigsegv_without_address_space_limit_does_not_blame_it(self, mock_copy2, mock_subprocess_run):
        mock_subprocess_run.return_value = _proc(returncode=-signal.SIGSEGV)

        with pytest.raises(ConversionError) as exc_info:
            await convert_document("/path/to/doc.pdf", "pdf", IngestionSettings(rlimit_as_mb=0), PROFILES, MAPPINGS)

        assert "SIGSEGV" in str(exc_info.value)
        assert "RLIMIT_AS_MB" not in str(exc_info.value)

    @patch("crosscodex_ingestion.converter.asyncio.create_subprocess_exec", new_callable=AsyncMock)
    @patch("crosscodex_ingestion.converter.shutil.copy2")
    async def test_other_nonzero_exit_is_not_a_limit_error(self, mock_copy2, mock_subprocess_run):
        mock_subprocess_run.return_value = _proc(returncode=1, stderr="boom")

        with pytest.raises(ConversionError):
            await convert_document("/path/to/doc.pdf", "pdf", IngestionSettings(), PROFILES, MAPPINGS)

    @patch("crosscodex_ingestion.converter.asyncio.create_subprocess_exec", new_callable=AsyncMock)
    @patch("crosscodex_ingestion.converter.shutil.copy2")
    async def test_output_over_max_extraction_length_is_rejected(self, mock_copy2, mock_subprocess_run):
        payload = json.dumps({"elements": [], "metadata": {}}, separators=(",", ":"))

        def write_output(*cmd, **kwargs):
            Path(cmd[3]).write_text(payload)
            return _proc(returncode=0)

        mock_subprocess_run.side_effect = write_output

        with pytest.raises(DocumentLimitError, match="exceeds the maximum of"):
            await convert_document(
                "/path/to/doc.pdf", "pdf", IngestionSettings(max_extraction_length=len(payload) - 1), PROFILES, MAPPINGS
            )

    @patch("crosscodex_ingestion.converter.asyncio.create_subprocess_exec", new_callable=AsyncMock)
    @patch("crosscodex_ingestion.converter.shutil.copy2")
    async def test_output_exactly_at_max_extraction_length_is_accepted(self, mock_copy2, mock_subprocess_run):
        payload = json.dumps({"elements": [], "metadata": {}}, separators=(",", ":"))

        def write_output(*cmd, **kwargs):
            Path(cmd[3]).write_text(payload)
            return _proc(returncode=0)

        mock_subprocess_run.side_effect = write_output

        result = await convert_document(
            "/path/to/doc.pdf", "pdf", IngestionSettings(max_extraction_length=len(payload)), PROFILES, MAPPINGS
        )

        assert result["elements"] == []

    @patch("crosscodex_ingestion.converter.asyncio.create_subprocess_exec", new_callable=AsyncMock)
    @patch("crosscodex_ingestion.converter.shutil.copy2")
    async def test_profiles_sent_on_stdin(self, mock_copy2, mock_subprocess_run):
        """The worker gets the registry's profiles as JSON on stdin, not on the command line."""
        proc = _proc(returncode=0)

        async def write_output_file(*cmd, **kwargs):
            Path(cmd[3]).write_text(json.dumps({"elements": [], "metadata": {}}))
            return proc

        mock_subprocess_run.side_effect = write_output_file

        await convert_document("/path/to/doc.html", "html", IngestionSettings(), PROFILES, MAPPINGS)

        sent = json.loads(proc.communicate.call_args.kwargs["input"])
        assert [(p["id"], p["sha256"]) for p in sent] == [(p.id, p.sha256) for p in PROFILES.profiles]
        assert [p["id"] for p in sent] == ["eur-lex-oj"]


class TestWorkerLifecycle:
    """The worker process tree must die with its conversion and leave no temp directory."""

    async def test_cancellation_kills_worker_and_grandchildren_and_removes_temp_dir(self, sleeper_worker):
        input_path, pidfile = sleeper_worker
        created: list[str] = []
        real_mkdtemp = tempfile.mkdtemp

        def tracking_mkdtemp(*args, **kwargs):
            created.append(real_mkdtemp(*args, **kwargs))
            return created[-1]

        with patch("crosscodex_ingestion.converter.tempfile.mkdtemp", side_effect=tracking_mkdtemp):
            task = asyncio.create_task(
                convert_document(str(input_path), "pdf", IngestionSettings(), PROFILES, MAPPINGS)
            )
            pids = await wait_for_pids(pidfile)
            assert all(is_running(p) for p in pids)

            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        assert not any(is_running(p) for p in pids)
        assert len(created) == 1
        assert not Path(created[0]).exists()

    async def test_timeout_kills_worker_and_grandchildren(self, sleeper_worker):
        input_path, pidfile = sleeper_worker

        with pytest.raises(ConversionTimeout, match="timed out after 1 seconds"):
            await convert_document(str(input_path), "pdf", IngestionSettings(max_processing_time=1), PROFILES, MAPPINGS)

        assert not any(is_running(p) for p in await wait_for_pids(pidfile))


STRUCTURED_FIXTURES = Path(__file__).parent.parent / "fixtures" / "structured"
ASVS = load_registry("").get("owasp-asvs-5.0.0-json")


def _error_worker(error: object) -> AsyncMock:
    """Fake structured worker that writes *error* (JSON-encoded unless bytes) to the error file and exits 4."""

    def run(*cmd, **kwargs):
        content = error if isinstance(error, bytes) else json.dumps(error).encode()
        (Path(cmd[3]).parent / "error.json").write_bytes(content)
        return _proc(returncode=INPUT_ERROR_EXIT_CODE)

    return AsyncMock(side_effect=run)


class TestConvertStructured:
    """The structured worker: same isolation as Docling's, its own lower limits, actionable input errors."""

    async def test_real_worker_converts_the_asvs_fixture(self, tmp_path):
        source = STRUCTURED_FIXTURES / "owasp-asvs-5.0.0" / "input.json"

        result = await convert_structured(str(source), ASVS, IngestionSettings())

        expected = json.loads((STRUCTURED_FIXTURES / "owasp-asvs-5.0.0" / "expected.json").read_text())
        assert next(iter(result)) == "schema_version"
        assert result["schema_version"] == SCHEMA_VERSION
        assert result["elements"] == expected["elements"]
        assert result["metadata"]["processing_time_ms"] > 0
        assert result["metadata"]["source"] == expected["metadata"]["source"]

    async def test_real_worker_reports_source_problems_with_details(self, tmp_path):
        source = tmp_path / "bad.json"
        source.write_text('{"Requirements": [{"Description": "no id"}]}')

        with pytest.raises(StructuredSourceError) as exc_info:
            await convert_structured(str(source), ASVS, IngestionSettings())

        assert str(exc_info.value) == (
            f"Problem in source (mapping {ASVS.id}): Node at Requirements[0] has no ID: field 'Shortcode' is "
            "missing or blank."
        )
        assert exc_info.value.details == {
            "mapping": ASVS.id,
            "count": "1",
            "problem.1": "Node at Requirements[0] has no ID: field 'Shortcode' is missing or blank.",
            "location.1": "Requirements[0]",
        }

    @patch("crosscodex_ingestion.converter.shutil.copy2")
    async def test_limit_error_keeps_its_redacted_message(self, mock_copy2):
        error = {
            "kind": "limit",
            "message": "ZIP entry 'x' is big",
            "redacted_message": "A ZIP entry is big",
            "details": {},
        }
        with (
            patch("crosscodex_ingestion.converter.asyncio.create_subprocess_exec", _error_worker(error)),
            pytest.raises(DocumentLimitError) as exc_info,
        ):
            await convert_structured("/path/to/source.json", ASVS, IngestionSettings())

        assert (str(exc_info.value), exc_info.value.redacted_message) == ("ZIP entry 'x' is big", "A ZIP entry is big")

    def test_document_limit_error_without_redacted_form_is_its_own(self):
        assert DocumentLimitError("Document exceeds the maximum of 3 pages").redacted_message == (
            "Document exceeds the maximum of 3 pages"
        )

    async def test_real_worker_reports_limits_as_limit_errors(self, tmp_path):
        source = tmp_path / "deep.json"
        source.write_text("[" * 10 + "]" * 10)

        with pytest.raises(DocumentLimitError, match=r"MAX_JSON_DEPTH \(5\)"):
            await convert_structured(str(source), ASVS, IngestionSettings(max_json_depth=5))

    @patch("crosscodex_ingestion.converter.asyncio.create_subprocess_exec", new_callable=AsyncMock)
    @patch("crosscodex_ingestion.converter.shutil.copy2")
    async def test_worker_gets_the_mapping_and_limits_on_stdin_and_its_own_rlimits(
        self, mock_copy2, mock_subprocess_run
    ):
        proc = _proc(returncode=1)
        mock_subprocess_run.return_value = proc
        settings = IngestionSettings(
            structured_rlimit_as_mb=512, structured_rlimit_cpu=30, rlimit_fsize_mb=7, max_table_rows=9
        )

        with pytest.raises(ConversionError):
            await convert_structured("/path/to/source.json", ASVS, settings)

        cmd = mock_subprocess_run.call_args[0]
        assert cmd[4:] == (str(512 * 1024 * 1024), "30", str(7 * 1024 * 1024))
        job = json.loads(proc.communicate.call_args.kwargs["input"])
        assert job["mapping"] == ASVS.document
        assert job["mapping_sha256"] == ASVS.sha256
        assert job["limits"]["max_table_rows"] == 9
        assert mock_subprocess_run.call_args.kwargs["stdin"] == asyncio.subprocess.PIPE

    @patch("crosscodex_ingestion.converter.shutil.copy2")
    async def test_source_error_is_rebuilt_from_the_problems(self, mock_copy2):
        error = {
            "kind": "source",
            "message": "ignored: the service rebuilds the message",
            "details": {
                "mapping": ASVS.id,
                "count": "2",
                "problem.1": "A.",
                "problem.2": "B.",
                "location.1": "row 2",
                "location.2": "row 3",
            },
        }
        with (
            patch("crosscodex_ingestion.converter.asyncio.create_subprocess_exec", _error_worker(error)),
            pytest.raises(StructuredSourceError) as exc_info,
        ):
            await convert_structured("/path/to/source.json", ASVS, IngestionSettings())

        assert str(exc_info.value) == f"2 problems in source (mapping {ASVS.id}): 1) A. 2) B."
        assert exc_info.value.locations == ["row 2", "row 3"]

    @pytest.mark.parametrize(
        "error",
        [
            b"not json",
            {"kind": "other", "message": "x", "details": {}},
            {
                "kind": "source",
                "message": "x",
                "details": {"mapping": "another-mapping", "count": "1", "problem.1": "A"},
            },
            {"kind": "source", "message": "x", "details": {"mapping": "owasp-asvs-5.0.0-json", "count": "2"}},
            {"kind": "limit", "message": 5, "details": {}},
            {"kind": "limit", "message": "x" * 3000, "redacted_message": "x", "details": {}},
            {"kind": "limit", "message": "x", "details": {}},
            {"kind": "limit", "message": "x", "redacted_message": "x" * 3000, "details": {}},
            {
                "kind": "source",
                "message": "x",
                "details": {"mapping": "owasp-asvs-5.0.0-json", "count": "1", "problem.1": "A"},
            },
            {
                "kind": "source",
                "message": "x",
                "details": {"mapping": "owasp-asvs-5.0.0-json", "count": "1", "problem.1": "x" * 3000},
            },
            b"{" + b" " * 70_000 + b"}",
        ],
    )
    @patch("crosscodex_ingestion.converter.shutil.copy2")
    async def test_malformed_error_file_is_an_internal_error(self, mock_copy2, error):
        with (
            patch("crosscodex_ingestion.converter.asyncio.create_subprocess_exec", _error_worker(error)),
            pytest.raises(ConversionError, match="error file"),
        ):
            await convert_structured("/path/to/source.json", ASVS, IngestionSettings())

    @patch("crosscodex_ingestion.converter.asyncio.create_subprocess_exec", new_callable=AsyncMock)
    @patch("crosscodex_ingestion.converter.shutil.copy2")
    async def test_cpu_death_names_the_structured_setting(self, mock_copy2, mock_subprocess_run):
        mock_subprocess_run.return_value = _proc(returncode=-signal.SIGXCPU)

        with pytest.raises(DocumentLimitError, match=r"60 CPU-seconds \(STRUCTURED_RLIMIT_CPU\)"):
            await convert_structured("/path/to/source.json", ASVS, IngestionSettings(structured_rlimit_cpu=60))

    @patch("crosscodex_ingestion.converter.asyncio.create_subprocess_exec", new_callable=AsyncMock)
    @patch("crosscodex_ingestion.converter.shutil.copy2")
    async def test_segfault_names_the_structured_address_space_setting(self, mock_copy2, mock_subprocess_run):
        mock_subprocess_run.return_value = _proc(returncode=-signal.SIGSEGV)

        with pytest.raises(ConversionError, match="STRUCTURED_RLIMIT_AS_MB"):
            await convert_structured("/path/to/source.json", ASVS, IngestionSettings())

    @patch("crosscodex_ingestion.converter.asyncio.create_subprocess_exec", new_callable=AsyncMock)
    @patch("crosscodex_ingestion.converter.shutil.copy2")
    async def test_output_over_max_extraction_length_is_rejected(self, mock_copy2, mock_subprocess_run):
        def write_output(*cmd, **kwargs):
            Path(cmd[3]).write_text('{"elements":[],"metadata":{}}')
            return _proc(returncode=0)

        mock_subprocess_run.side_effect = write_output

        with pytest.raises(DocumentLimitError, match="exceeds the maximum of"):
            await convert_structured("/path/to/source.json", ASVS, IngestionSettings(max_extraction_length=5))

    async def test_timeout_uses_the_structured_limit_and_kills_the_worker(self, tmp_path, monkeypatch):
        pidfile = tmp_path / "pids"
        monkeypatch.setattr(converter, "_STRUCTURED_WORKER_SCRIPT", SLEEPER_SCRIPT.replace("@PIDFILE@", str(pidfile)))
        source = tmp_path / "source.json"
        source.write_text("{}")

        with pytest.raises(ConversionTimeout, match="timed out after 1 seconds"):
            await convert_structured(str(source), ASVS, IngestionSettings(structured_max_processing_time=1))

        assert not any(is_running(p) for p in await wait_for_pids(pidfile))


class TestRealWorkerProfileProvenance:
    """The real Docling worker subprocess applies a profile and says so; no models are needed for HTML."""

    SETTINGS_ARTIFACTS = "/nonexistent-models"

    async def _convert(self, fixture: str, profiles: HtmlProfileRegistry = PROFILES):
        path = Path(__file__).parent.parent / "fixtures" / "documents" / fixture
        return await convert_document(
            str(path), "html", IngestionSettings(docling_artifacts_path=self.SETTINGS_ARTIFACTS), profiles, MAPPINGS
        )

    async def test_real_worker_reports_profile_provenance(self):
        result = await self._convert("eurlex-gdpr-32016R0679.html")

        assert result["metadata"]["html_profile"] == {"id": "eur-lex-oj", "sha256": PROFILES.profiles[0].sha256}
        assert "conversion_warnings" not in result

    async def test_real_worker_reports_no_profile_for_plain_html(self):
        result = await self._convert("sample.html")

        assert "html_profile" not in result["metadata"]
        assert "conversion_warnings" not in result

    async def test_real_worker_reports_ambiguity_and_applies_no_profile(self):
        eur_lex = PROFILES.profiles[0]
        twin = HtmlProfile(id="eur-lex-twin", sha256="0" * 64, document={**eur_lex.document, "id": "eur-lex-twin"})

        result = await self._convert(
            "eurlex-gdpr-32016R0679.html", HtmlProfileRegistry({eur_lex.id: eur_lex, twin.id: twin})
        )

        assert result["conversion_warnings"] == ["html_profile_ambiguous"]
        assert "html_profile" not in result["metadata"]


class TestPlainText:
    """text/plain is paragraphs: Docling's sniffer must not turn CSV-, HTML- or Markdown-looking text into structure.

    Documents emit their structure, not their layout, so the layout shows through metadata.structure (measured on
    it) and through the fallback structurer finding nothing.
    """

    @staticmethod
    async def _convert_text(tmp_path: Path, name: str, text: str) -> dict:
        source = tmp_path / name
        source.write_text(text)
        return await convert_document(str(source), "txt", IngestionSettings(), PROFILES, MAPPINGS)

    @pytest.mark.parametrize(
        ("name", "text"),
        [
            ("people.txt", "name,role\nalice,admin\nbob,user\n"),
            ("page.txt", "<h1>x</h1>\n"),
            ("notes.txt", "# Title\n\n| a | b |\n|---|---|\n| 1 | 2 |\n"),
        ],
        ids=["csv", "html", "markdown"],
    )
    async def test_markup_looking_text_has_no_heading_or_table_to_structure(self, tmp_path, name, text):
        with pytest.raises(NoDocumentStructureError):
            await self._convert_text(tmp_path, name, text)

    async def test_blank_lines_separate_paragraphs(self, tmp_path):
        result = await self._convert_text(
            tmp_path, "two.txt", "1. first line\nsecond line\n\n\n  \n2. two\n\n3. three\n"
        )

        assert result["metadata"]["structure"]["unparented_count"] == 3

    async def test_windows_line_endings_separate_paragraphs_too(self, tmp_path):
        source = tmp_path / "crlf.txt"
        source.write_bytes(b"1. first\r\n\r\n2. second\r\n\r\n3. third\r\n")

        result = await convert_document(str(source), "txt", IngestionSettings(), PROFILES, MAPPINGS)

        assert result["metadata"]["structure"]["unparented_count"] == 3
        assert [e["text"] for e in result["elements"]] == ["1. first", "2. second", "3. third"]

    async def test_two_kilobyte_json_array_completes(self, tmp_path):
        body = json.dumps([{"id": n, "name": f"item-{n}"} for n in range(120)])
        assert len(body) > 2048

        with pytest.raises(NoDocumentStructureError):
            await self._convert_text(tmp_path, "data.txt", body)

    async def test_empty_text_has_no_structure(self, tmp_path):
        with pytest.raises(NoDocumentStructureError):
            await self._convert_text(tmp_path, "empty.txt", "\n\n")


class TestInputParseFailure:
    """A Docling backend failure on input whose format the service validated is the caller's problem."""

    async def test_corrupt_docx_is_an_invalid_argument_naming_the_format(self, tmp_path):
        source = tmp_path / "broken.docx"
        source.write_bytes(b"PK\x03\x04 not really a zip archive")

        with pytest.raises(ValueError, match=r"^The input could not be parsed as docx: .+") as excinfo:
            await convert_document(str(source), "docx", IngestionSettings(), PROFILES, MAPPINGS)

        assert str(tmp_path) not in str(excinfo.value)

    @patch("crosscodex_ingestion.converter.asyncio.create_subprocess_exec", new_callable=AsyncMock)
    @patch("crosscodex_ingestion.converter.shutil.copy2")
    async def test_unexpected_worker_exit_stays_a_conversion_error(self, mock_copy2, mock_subprocess_run):
        mock_subprocess_run.return_value = _proc(1, "Traceback: boom")

        with pytest.raises(ConversionError):
            await convert_document("/tmp/x.pdf", "pdf", IngestionSettings(), PROFILES, MAPPINGS)

    @patch("crosscodex_ingestion.converter.asyncio.create_subprocess_exec", new_callable=AsyncMock)
    @patch("crosscodex_ingestion.converter.shutil.copy2")
    async def test_parse_exit_without_a_reason_file_is_a_conversion_error(self, mock_copy2, mock_subprocess_run):
        mock_subprocess_run.return_value = _proc(converter.INPUT_PARSE_EXIT_CODE)

        with pytest.raises(ConversionError, match="no reason"):
            await convert_document("/tmp/x.pdf", "pdf", IngestionSettings(), PROFILES, MAPPINGS)

    @pytest.mark.parametrize("message", ["", "  \n ", None])
    def test_worker_falls_back_to_unknown_error_for_a_blank_docling_message(self, tmp_path, monkeypatch, message):
        """Docling can fail with an empty message; the worker must still exit as a parse failure, not crash."""
        failed = MagicMock()
        failed.status = "failure"
        failed.input.page_count = 1
        failed.errors = [MagicMock(error_message=message)] if message is not None else []
        fake_docling = {
            name: MagicMock()
            for name in (
                "docling",
                "docling.datamodel",
                "docling.datamodel.base_models",
                "docling.datamodel.pipeline_options",
                "docling.document_converter",
                "docling_core",
                "docling_core.types",
                "docling_core.types.doc",
                "crosscodex_ingestion.extractor",
                "crosscodex_ingestion.html_backend",
            )
        }
        fake_docling["docling.document_converter"].DocumentConverter.return_value.convert.return_value = failed
        for name, module in fake_docling.items():
            monkeypatch.setitem(sys.modules, name, module)
        output = tmp_path / "out.json"
        argv = ["worker.py", str(tmp_path / "in.pdf"), str(output), "pdf", "0", "0", "0", str(tmp_path), "10"]
        monkeypatch.setattr(sys, "argv", argv)
        monkeypatch.setattr(sys, "stdin", io.StringIO("[]"))

        script = tmp_path / "worker.py"
        script.write_text(converter._WORKER_SCRIPT)

        with pytest.raises(SystemExit) as excinfo:
            runpy.run_path(str(script), run_name="__main__")

        assert excinfo.value.code == converter.INPUT_PARSE_EXIT_CODE
        reason_file = tmp_path / ERROR_FILE_NAME
        assert json.loads(reason_file.read_text()) == {"reason": "unknown error"}


CANARY = "canary-7f3a9c"
NO_STRUCTURE_PDF = (
    "No controls found in this pdf document: it has no headings, tables or numbered lines the fallback structurer "
    "recognises. Convert it with a document mapping for pdf; registered: none yet."
)


def _fake_mapping(mapping_id: str, fmt: str) -> Mapping:
    return Mapping(id=mapping_id, title="T", format=fmt, mapping_version=1, sha256="0" * 64, document={})


def _document_worker(returncode: int, error_file: bytes | None = None) -> AsyncMock:
    """Fake Docling worker that exits *returncode*, after writing *error_file* to the error file if given."""

    def run(*cmd, **kwargs):
        if error_file is not None:
            (Path(cmd[3]).parent / ERROR_FILE_NAME).write_bytes(error_file)
        return _proc(returncode, f"stderr quoting the document: {CANARY}")

    return AsyncMock(side_effect=run)


class TestNoStructure:
    """A document the fallback structurer finds no catalog in is the caller's to fix, with a mapping."""

    def test_message_for_a_format_without_mappings(self):
        assert str(NoDocumentStructureError("pdf", [])) == NO_STRUCTURE_PDF

    def test_message_lists_the_format_mappings(self):
        assert str(NoDocumentStructureError("json", ["a-json", "b-json"])) == (
            "No controls found in this json document: it has no headings, tables or numbered lines the fallback "
            "structurer recognises. Convert it with a document mapping for json; registered: a-json, b-json."
        )

    @pytest.mark.parametrize("method", ["heading", "table", "pattern"])
    def test_repeated_ids_message_names_the_method(self, method):
        assert str(RepeatedControlIdsError("pdf", method, [])) == (
            f"The fallback structurer found controls in this pdf document (method {method}), but their identifiers "
            "repeat, so they do not form a catalog. Convert it with a document mapping for pdf; registered: none yet."
        )

    @patch("crosscodex_ingestion.converter.shutil.copy2")
    async def test_no_structure_exit_names_the_validated_format_and_only_its_mappings(self, mock_copy2):
        registry = MappingRegistry(
            {
                m.id: m
                for m in (
                    _fake_mapping("b-json", "json"),
                    _fake_mapping("a-json", "json"),
                    _fake_mapping("c-csv", "csv"),
                )
            }
        )

        with (
            patch(
                "crosscodex_ingestion.converter.asyncio.create_subprocess_exec",
                _document_worker(converter.NO_STRUCTURE_EXIT_CODE),
            ),
            pytest.raises(NoDocumentStructureError) as excinfo,
        ):
            await convert_document(f"/tmp/{CANARY}.json", "json", IngestionSettings(), PROFILES, registry)

        assert str(excinfo.value) == str(NoDocumentStructureError("json", ["a-json", "b-json"]))
        assert CANARY not in str(excinfo.value)

    @patch("crosscodex_ingestion.converter.shutil.copy2")
    async def test_no_structure_exit_ignores_an_error_file(self, mock_copy2):
        worker = _document_worker(converter.NO_STRUCTURE_EXIT_CODE, f'{{"reason": "{CANARY}"}}'.encode())

        with (
            patch("crosscodex_ingestion.converter.asyncio.create_subprocess_exec", worker),
            pytest.raises(NoDocumentStructureError) as excinfo,
        ):
            await convert_document("/tmp/x.pdf", "pdf", IngestionSettings(), PROFILES, MAPPINGS)

        assert str(excinfo.value) == NO_STRUCTURE_PDF

    @patch("crosscodex_ingestion.converter.shutil.copy2")
    async def test_repeated_ids_exit_names_the_method_from_the_error_file(self, mock_copy2):
        worker = _document_worker(converter.REPEATED_IDS_EXIT_CODE, b"table")

        with (
            patch("crosscodex_ingestion.converter.asyncio.create_subprocess_exec", worker),
            pytest.raises(RepeatedControlIdsError) as excinfo,
        ):
            await convert_document("/tmp/x.pdf", "pdf", IngestionSettings(), PROFILES, MAPPINGS)

        assert str(excinfo.value) == str(RepeatedControlIdsError("pdf", "table", []))

    @pytest.mark.parametrize(
        "error_file",
        [None, b"", b"mapping", b"table\n", b"Table", CANARY.encode(), b"pattern" * 10_000],
        ids=["missing", "empty", "mapping", "trailing_newline", "wrong_case", "document_text", "oversized"],
    )
    @patch("crosscodex_ingestion.converter.shutil.copy2")
    async def test_repeated_ids_exit_without_a_guessed_method_is_an_internal_error(self, mock_copy2, error_file):
        worker = _document_worker(converter.REPEATED_IDS_EXIT_CODE, error_file)

        with (
            patch("crosscodex_ingestion.converter.asyncio.create_subprocess_exec", worker),
            pytest.raises(ConversionError) as excinfo,
        ):
            await convert_document("/tmp/x.pdf", "pdf", IngestionSettings(), PROFILES, MAPPINGS)

        assert CANARY not in str(excinfo.value)

    def test_worker_lets_a_layout_error_escape_as_a_crash(self, tmp_path, monkeypatch):
        """A malformed layout is the extractor's bug: the worker must not exit with a caller-error code for it."""
        fake = {
            name: MagicMock()
            for name in (
                "docling",
                "docling.datamodel",
                "docling.datamodel.base_models",
                "docling.datamodel.pipeline_options",
                "docling.document_converter",
                "docling_core",
                "docling_core.types",
                "docling_core.types.doc",
                "crosscodex_ingestion.extractor",
                "crosscodex_ingestion.html_backend",
            )
        }
        converted = MagicMock()
        converted.status = fake["docling.datamodel.base_models"].ConversionStatus.SUCCESS
        fake["docling.document_converter"].DocumentConverter.return_value.convert.return_value = converted
        orphan = {"element_id": "e-001", "type": "paragraph", "content": "x", "page": 1, "level": None}
        fake["crosscodex_ingestion.extractor"].extract_elements.return_value = MagicMock(
            elements=[{**orphan, "parent_id": "e-999", "style": {}}], metadata={}
        )
        fake["crosscodex_ingestion.html_backend"].html_backend_for.return_value = MagicMock(
            applied=None, ambiguous=False
        )
        for name, module in fake.items():
            monkeypatch.setitem(sys.modules, name, module)
        argv = ["worker.py", str(tmp_path / "in.pdf"), str(tmp_path / "out.json"), "pdf", "0", "0", "0", "m", "10"]
        monkeypatch.setattr(sys, "argv", argv)
        monkeypatch.setattr(sys, "stdin", io.StringIO("[]"))
        script = tmp_path / "worker.py"
        script.write_text(converter._WORKER_SCRIPT)

        with pytest.raises(LayoutError):
            runpy.run_path(str(script), run_name="__main__")

        assert not (tmp_path / ERROR_FILE_NAME).exists()


class TestRealWorkerStructure:
    """The real worker structures plain text, which needs no Docling models."""

    @staticmethod
    async def _convert_text(tmp_path: Path, text: str) -> dict:
        source = tmp_path / "input.txt"
        source.write_text(text)
        return await convert_document(str(source), "txt", IngestionSettings(), PROFILES, MAPPINGS)

    async def test_numbered_lines_become_controls_labelled_pattern(self, tmp_path):
        text = (Path(__file__).parent.parent / "fixtures" / "documents" / "sample.txt").read_text()

        result = await self._convert_text(tmp_path, text)

        metadata = result["metadata"]
        assert metadata["structure_method"] == "pattern"
        assert metadata["element_count"] == len(result["elements"]) > 0
        assert {e["role"] for e in result["elements"]} == {"control"}
        assert all(isinstance(e["id"], str) for e in result["elements"])
        # Measured on the layout, which has paragraphs only.
        assert (metadata["structure"]["heading_count"], metadata["structure"]["table_count"]) == (0, 0)

    async def test_prose_fails_with_no_structure_and_no_document_text(self, tmp_path):
        with pytest.raises(NoDocumentStructureError) as excinfo:
            await self._convert_text(tmp_path, f"Just prose about {CANARY}.\n\nMore prose.\n")

        assert str(excinfo.value) == NO_STRUCTURE_PDF.replace("pdf", "txt")
        assert CANARY not in str(excinfo.value)

    async def test_repeated_pattern_ids_fail_naming_the_method(self, tmp_path):
        with pytest.raises(RepeatedControlIdsError) as excinfo:
            await self._convert_text(tmp_path, f"1.1 {CANARY} first\n\n1.2 second\n\n3. third\n")

        assert str(excinfo.value) == str(RepeatedControlIdsError("txt", "pattern", []))
        assert CANARY not in str(excinfo.value)
