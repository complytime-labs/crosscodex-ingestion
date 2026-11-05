"""Unit tests for server startup checks."""

import pytest

from crosscodex_ingestion.server import serve


@pytest.mark.asyncio
async def test_serve_refuses_to_start_without_docling_models(monkeypatch):
    """Fail closed: without a models directory every conversion would fail at request time."""
    monkeypatch.delenv("DOCLING_ARTIFACTS_PATH", raising=False)

    with pytest.raises(SystemExit, match="DOCLING_ARTIFACTS_PATH"):
        await serve()


@pytest.mark.asyncio
async def test_serve_refuses_to_start_when_models_path_is_not_a_directory(monkeypatch, tmp_path):
    monkeypatch.setenv("DOCLING_ARTIFACTS_PATH", str(tmp_path / "absent"))

    with pytest.raises(SystemExit, match="not a directory"):
        await serve()
