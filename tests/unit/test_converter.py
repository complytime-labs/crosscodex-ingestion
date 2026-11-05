"""Tests for converter module — subprocess isolation."""

import asyncio
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest

from crosscodex_ingestion.config import IngestionSettings
from crosscodex_ingestion.converter import (
    PAGE_LIMIT_EXIT_CODE,
    ConversionError,
    ConversionTimeout,
    DocumentLimitError,
    convert_document,
    validate_artifacts_path,
)
from tests.unit.process_helpers import is_running, wait_for_pids


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
        result = await convert_document("/path/to/doc.pdf", "pdf", settings)

        # Assert
        assert "elements" in result
        assert "metadata" in result
        assert len(result["elements"]) == 1
        assert result["elements"][0]["element_id"] == "e-001"
        assert result["metadata"]["element_count"] == 1

    @patch("crosscodex_ingestion.converter.asyncio.create_subprocess_exec", new_callable=AsyncMock)
    @patch("crosscodex_ingestion.converter.shutil.copy2")
    async def test_subprocess_crash_raises_conversion_error(self, mock_copy2, mock_subprocess_run):
        """Subprocess crash (non-zero exit) raises ConversionError."""
        # Arrange
        mock_subprocess_run.return_value = _proc(returncode=137, stderr="Memory limit exceeded")

        settings = IngestionSettings()

        # Act & Assert
        with pytest.raises(ConversionError) as exc_info:
            await convert_document("/path/to/doc.pdf", "pdf", settings)

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
            await convert_document("/path/to/doc.pdf", "pdf", settings)

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
                await convert_document("/path/to/doc.pdf", "pdf", settings)

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
        await convert_document("/path/to/doc.pdf", "pdf", settings)

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
        await convert_document("/path/to/doc.pdf", "pdf", settings)

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
        await convert_document("/path/to/doc.pdf", "pdf", settings)

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

        await convert_document("/path/to/doc.pdf", "pdf", settings)

        assert "/opt/docling-models" in mock_subprocess_run.call_args[0]

    @patch("crosscodex_ingestion.converter.asyncio.create_subprocess_exec", new_callable=AsyncMock)
    @patch("crosscodex_ingestion.converter.shutil.copy2")
    async def test_worker_env_forces_hf_offline_and_leaks_nothing_else(
        self, mock_copy2, mock_subprocess_run, monkeypatch
    ):
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "should-not-leak")
        _capture_cmd_and_env(mock_subprocess_run)

        await convert_document("/path/to/doc.pdf", "pdf", IngestionSettings(docling_artifacts_path="/m"))

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

        await convert_document("/path/to/doc.pdf", "pdf", IngestionSettings(docling_artifacts_path="/m"))

        assert mock_subprocess_run.call_args.kwargs["env"]["PYTHONPATH"] == "/opt/app-root/lib/python3.12/site-packages"

    @patch("crosscodex_ingestion.converter.asyncio.create_subprocess_exec", new_callable=AsyncMock)
    @patch("crosscodex_ingestion.converter.shutil.copy2")
    async def test_worker_env_has_no_pythonpath_when_parent_has_none(
        self, mock_copy2, mock_subprocess_run, monkeypatch
    ):
        monkeypatch.delenv("PYTHONPATH", raising=False)
        _capture_cmd_and_env(mock_subprocess_run)

        await convert_document("/path/to/doc.pdf", "pdf", IngestionSettings(docling_artifacts_path="/m"))

        assert "PYTHONPATH" not in mock_subprocess_run.call_args.kwargs["env"]


class TestValidateArtifactsPath:
    def test_unset_path_is_refused_with_download_instructions(self):
        with pytest.raises(ValueError, match=r"DOCLING_ARTIFACTS_PATH.*docling-tools models download -o"):
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

        await convert_document("/path/to/doc.pdf", "pdf", IngestionSettings(max_pages=42))

        assert mock_subprocess_run.call_args[0][-1] == "42"

    @patch("crosscodex_ingestion.converter.asyncio.create_subprocess_exec", new_callable=AsyncMock)
    @patch("crosscodex_ingestion.converter.shutil.copy2")
    async def test_page_limit_exit_code_raises_limit_error_without_leaking_stderr(
        self, mock_copy2, mock_subprocess_run
    ):
        mock_subprocess_run.return_value = _proc(returncode=PAGE_LIMIT_EXIT_CODE, stderr="Traceback /srv/secret/path")

        with pytest.raises(DocumentLimitError) as exc_info:
            await convert_document("/path/to/doc.pdf", "pdf", IngestionSettings(max_pages=10))

        assert "10 pages" in str(exc_info.value)
        assert "/srv/secret" not in str(exc_info.value)
        assert isinstance(exc_info.value, ValueError)

    @patch("crosscodex_ingestion.converter.asyncio.create_subprocess_exec", new_callable=AsyncMock)
    @patch("crosscodex_ingestion.converter.shutil.copy2")
    async def test_other_nonzero_exit_is_not_a_limit_error(self, mock_copy2, mock_subprocess_run):
        mock_subprocess_run.return_value = _proc(returncode=1, stderr="boom")

        with pytest.raises(ConversionError):
            await convert_document("/path/to/doc.pdf", "pdf", IngestionSettings())

    @patch("crosscodex_ingestion.converter.asyncio.create_subprocess_exec", new_callable=AsyncMock)
    @patch("crosscodex_ingestion.converter.shutil.copy2")
    async def test_output_over_max_extraction_length_is_rejected(self, mock_copy2, mock_subprocess_run):
        payload = json.dumps({"elements": [], "metadata": {}}, separators=(",", ":"))

        def write_output(*cmd, **kwargs):
            Path(cmd[3]).write_text(payload)
            return _proc(returncode=0)

        mock_subprocess_run.side_effect = write_output

        with pytest.raises(DocumentLimitError, match="exceeds the maximum of"):
            await convert_document("/path/to/doc.pdf", "pdf", IngestionSettings(max_extraction_length=len(payload) - 1))

    @patch("crosscodex_ingestion.converter.asyncio.create_subprocess_exec", new_callable=AsyncMock)
    @patch("crosscodex_ingestion.converter.shutil.copy2")
    async def test_output_exactly_at_max_extraction_length_is_accepted(self, mock_copy2, mock_subprocess_run):
        payload = json.dumps({"elements": [], "metadata": {}}, separators=(",", ":"))

        def write_output(*cmd, **kwargs):
            Path(cmd[3]).write_text(payload)
            return _proc(returncode=0)

        mock_subprocess_run.side_effect = write_output

        result = await convert_document(
            "/path/to/doc.pdf", "pdf", IngestionSettings(max_extraction_length=len(payload))
        )

        assert result == {"elements": [], "metadata": {}}


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
            task = asyncio.create_task(convert_document(str(input_path), "pdf", IngestionSettings()))
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
            await convert_document(str(input_path), "pdf", IngestionSettings(max_processing_time=1))

        assert not any(is_running(p) for p in await wait_for_pids(pidfile))
