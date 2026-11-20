"""Unit tests for the command-line entry point."""

import importlib.metadata
from unittest.mock import AsyncMock, patch

import pytest

from crosscodex_ingestion import __version__
from crosscodex_ingestion.__main__ import main


def test_version_is_the_installed_distribution_version():
    assert __version__ == importlib.metadata.version("crosscodex-ingestion")


@pytest.mark.parametrize("flag", ["--help", "--version"])
def test_info_flags_exit_zero_without_starting_the_server(flag, capsys):
    with patch("crosscodex_ingestion.__main__.serve", new=AsyncMock()) as serve, pytest.raises(SystemExit) as exit_info:
        main([flag])

    assert exit_info.value.code == 0
    serve.assert_not_called()
    assert capsys.readouterr().out.strip()


def test_version_output_names_the_service_and_version(capsys):
    with pytest.raises(SystemExit):
        main(["--version"])

    assert capsys.readouterr().out.strip() == f"crosscodex-ingestion {__version__}"


def test_help_points_at_the_environment_configuration(capsys):
    with pytest.raises(SystemExit):
        main(["--help"])

    assert "environment" in capsys.readouterr().out.lower()


def test_no_arguments_starts_the_server():
    with patch("crosscodex_ingestion.__main__.serve", new=AsyncMock()) as serve:
        main([])

    serve.assert_awaited_once_with()


def test_an_unknown_flag_is_refused_without_starting_the_server(capsys):
    with patch("crosscodex_ingestion.__main__.serve", new=AsyncMock()) as serve, pytest.raises(SystemExit) as exit_info:
        main(["--port", "1"])

    assert exit_info.value.code == 2
    serve.assert_not_called()
    assert "--port" in capsys.readouterr().err
