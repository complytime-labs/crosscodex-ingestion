"""Unit tests for server startup checks and shutdown."""

import os
import signal
import socket
import subprocess
import sys
import time

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


@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGINT])
def test_server_shuts_down_gracefully_on_signal(tmp_path, sig):
    """Container runtimes stop services with SIGTERM; it must end the process, not wait for SIGKILL."""
    models = tmp_path / "models"
    models.mkdir()
    env = {
        **os.environ,
        "STORAGE_PATH": str(tmp_path / "data"),
        "ATTESTATION_ENABLED": "false",
        "GRPC_PORT": str(_free_port()),
        "METRICS_PORT": str(_free_port()),
        "DOCLING_ARTIFACTS_PATH": str(models),
    }
    proc = subprocess.Popen(
        [sys.executable, "-m", "crosscodex_ingestion"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        deadline = time.monotonic() + 30
        assert proc.stdout is not None
        for line in proc.stdout:
            if "gRPC server listening" in line:
                break
            assert time.monotonic() < deadline, "server did not start"
        proc.send_signal(sig)
        assert proc.wait(timeout=15) == 0
        assert "Server shutdown complete" in proc.stdout.read()
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])
