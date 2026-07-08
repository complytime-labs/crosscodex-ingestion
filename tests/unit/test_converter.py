"""Tests for converter module — subprocess isolation."""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from crosscodex_ingestion.config import IngestionSettings
from crosscodex_ingestion.converter import (
    ConversionError,
    ConversionTimeout,
    convert_document,
)


class TestConvertDocument:
    """Test the convert_document function."""

    @patch("crosscodex_ingestion.converter.subprocess.run")
    @patch("crosscodex_ingestion.converter.shutil.copy2")
    def test_successful_conversion_returns_dict_with_elements_and_metadata(self, mock_copy2, mock_subprocess_run):
        """Successful conversion returns dict with elements and metadata keys."""

        # Arrange
        # Mock the subprocess to write output file
        def write_output_file(cmd, *args, **kwargs):
            # Extract output path from command args
            # cmd format: [python, script_path, input_path, output_path, format_hint, rlimits...]
            output_path = Path(cmd[3])
            output_data = {
                "elements": [{"element_id": "e-001", "type": "heading", "content": "Test"}],
                "metadata": {"element_count": 1, "detected_format": "pdf", "processing_time_ms": 123.45},
            }
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, "w") as f:
                json.dump(output_data, f)
            return Mock(returncode=0)

        mock_subprocess_run.side_effect = write_output_file

        settings = IngestionSettings()

        # Act
        result = convert_document("/path/to/doc.pdf", "pdf", settings)

        # Assert
        assert "elements" in result
        assert "metadata" in result
        assert len(result["elements"]) == 1
        assert result["elements"][0]["element_id"] == "e-001"
        assert result["metadata"]["element_count"] == 1

    @patch("crosscodex_ingestion.converter.subprocess.run")
    @patch("crosscodex_ingestion.converter.shutil.copy2")
    def test_subprocess_timeout_raises_conversion_timeout(self, mock_copy2, mock_subprocess_run):
        """Subprocess timeout raises ConversionTimeout."""
        # Arrange
        mock_subprocess_run.side_effect = subprocess.TimeoutExpired(cmd=["python"], timeout=300)

        settings = IngestionSettings(max_processing_time=5)

        # Act & Assert
        with pytest.raises(ConversionTimeout) as exc_info:
            convert_document("/path/to/doc.pdf", "pdf", settings)

        assert "timed out after 5 seconds" in str(exc_info.value).lower()

    @patch("crosscodex_ingestion.converter.subprocess.run")
    @patch("crosscodex_ingestion.converter.shutil.copy2")
    def test_subprocess_crash_raises_conversion_error(self, mock_copy2, mock_subprocess_run):
        """Subprocess crash (non-zero exit) raises ConversionError."""
        # Arrange
        mock_subprocess_run.return_value = Mock(
            returncode=137,  # SIGKILL
            stdout="",
            stderr="Memory limit exceeded",
        )

        settings = IngestionSettings()

        # Act & Assert
        with pytest.raises(ConversionError) as exc_info:
            convert_document("/path/to/doc.pdf", "pdf", settings)

        assert "exit code 137" in str(exc_info.value).lower()

    @patch("crosscodex_ingestion.converter.subprocess.run")
    @patch("crosscodex_ingestion.converter.shutil.copy2")
    def test_temp_directory_cleaned_up_after_success(self, mock_copy2, mock_subprocess_run):
        """Temp directory is cleaned up after successful conversion."""
        # Arrange
        temp_dirs_created = []

        original_mkdtemp = tempfile.mkdtemp

        def track_mkdtemp(*args, **kwargs):
            temp_dir = original_mkdtemp(*args, **kwargs)
            temp_dirs_created.append(temp_dir)
            return temp_dir

        def write_output_file(cmd, *args, **kwargs):
            output_path = Path(cmd[3])
            output_data = {"elements": [], "metadata": {}}
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, "w") as f:
                json.dump(output_data, f)
            return Mock(returncode=0)

        mock_subprocess_run.side_effect = write_output_file

        with patch("crosscodex_ingestion.converter.tempfile.mkdtemp", side_effect=track_mkdtemp):
            settings = IngestionSettings()

            # Act
            convert_document("/path/to/doc.pdf", "pdf", settings)

        # Assert - temp directory should not exist
        assert len(temp_dirs_created) == 1
        assert not Path(temp_dirs_created[0]).exists()

    @patch("crosscodex_ingestion.converter.subprocess.run")
    @patch("crosscodex_ingestion.converter.shutil.copy2")
    def test_temp_directory_cleaned_up_after_failure(self, mock_copy2, mock_subprocess_run):
        """Temp directory is cleaned up after conversion failure."""
        # Arrange
        temp_dirs_created = []

        original_mkdtemp = tempfile.mkdtemp

        def track_mkdtemp(*args, **kwargs):
            temp_dir = original_mkdtemp(*args, **kwargs)
            temp_dirs_created.append(temp_dir)
            return temp_dir

        mock_subprocess_run.return_value = Mock(returncode=1, stdout="", stderr="Error")

        with patch("crosscodex_ingestion.converter.tempfile.mkdtemp", side_effect=track_mkdtemp):
            settings = IngestionSettings()

            # Act & Assert
            with pytest.raises(ConversionError):
                convert_document("/path/to/doc.pdf", "pdf", settings)

        # Assert - temp directory should not exist
        assert len(temp_dirs_created) == 1
        assert not Path(temp_dirs_created[0]).exists()

    @patch("crosscodex_ingestion.converter.subprocess.run")
    @patch("crosscodex_ingestion.converter.shutil.copy2")
    def test_rlimits_passed_to_subprocess_when_nonzero(self, mock_copy2, mock_subprocess_run):
        """Rlimits are passed to subprocess script when non-zero."""

        # Arrange
        def capture_subprocess_args(cmd, *args, **kwargs):
            output_path = Path(cmd[3])
            output_data = {"elements": [], "metadata": {}}
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, "w") as f:
                json.dump(output_data, f)
            return Mock(returncode=0)

        mock_subprocess_run.side_effect = capture_subprocess_args

        settings = IngestionSettings(rlimit_as_mb=2048, rlimit_cpu=120, rlimit_fsize_mb=50)

        # Act
        convert_document("/path/to/doc.pdf", "pdf", settings)

        # Assert
        mock_subprocess_run.assert_called_once()
        call_args = mock_subprocess_run.call_args

        # Verify the command includes rlimit values
        cmd = call_args[0][0]
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

    @patch("crosscodex_ingestion.converter.subprocess.run")
    @patch("crosscodex_ingestion.converter.shutil.copy2")
    def test_rlimits_set_to_zero_when_unlimited(self, mock_copy2, mock_subprocess_run):
        """Rlimits are passed as 0 when settings specify unlimited (0)."""

        # Arrange
        def capture_subprocess_args(cmd, *args, **kwargs):
            output_path = Path(cmd[3])
            output_data = {"elements": [], "metadata": {}}
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, "w") as f:
                json.dump(output_data, f)
            return Mock(returncode=0)

        mock_subprocess_run.side_effect = capture_subprocess_args

        settings = IngestionSettings(rlimit_as_mb=0, rlimit_cpu=0, rlimit_fsize_mb=0)

        # Act
        convert_document("/path/to/doc.pdf", "pdf", settings)

        # Assert
        mock_subprocess_run.assert_called_once()
        call_args = mock_subprocess_run.call_args
        cmd = call_args[0][0]

        # Should have three "0" values for the rlimits
        # The args are: script_path, input_path, output_path, format_hint, rlimit_as, rlimit_cpu, rlimit_fsize
        # Last three should be "0"
        assert cmd[-3:] == ["0", "0", "0"]

    @patch("crosscodex_ingestion.converter.subprocess.run")
    @patch("crosscodex_ingestion.converter.shutil.copy2")
    def test_minimal_environment_passed_to_child(self, mock_copy2, mock_subprocess_run):
        """Minimal environment is passed to child (no HOME, USER, etc.)."""

        # Arrange
        def capture_env(cmd, *args, **kwargs):
            output_path = Path(cmd[3])
            output_data = {"elements": [], "metadata": {}}
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, "w") as f:
                json.dump(output_data, f)
            return Mock(returncode=0)

        mock_subprocess_run.side_effect = capture_env
        settings = IngestionSettings()

        # Act
        convert_document("/path/to/doc.pdf", "pdf", settings)

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
                env={"PATH": os.environ.get("PATH", "")},
            )

            # Verify the output file was created
            assert result.returncode == 0
            assert output_file.exists()

            # Verify the content
            with open(output_file) as f:
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
