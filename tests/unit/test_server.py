"""Unit tests for server startup checks and shutdown."""

import json
import logging
import os
import signal
import socket
import subprocess
import sys
import time

import grpc
import pytest

from crosscodex_ingestion.config import IngestionSettings
from crosscodex_ingestion.server import _GRPC_REQUEST_OVERHEAD, _build_sampler, create_grpc_server, serve


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


@pytest.fixture
def startable_env(monkeypatch, tmp_path):
    """Environment that passes the model check, so a startup refusal comes from what a test unsets."""
    models = tmp_path / "models"
    models.mkdir()
    monkeypatch.setenv("DOCLING_ARTIFACTS_PATH", str(models))
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "data"))
    monkeypatch.setenv("GRPC_PORT", "0")
    monkeypatch.setenv("METRICS_PORT", str(_free_port()))


@pytest.mark.asyncio
async def test_serve_refuses_to_start_with_attestation_enabled_and_no_keys(monkeypatch, startable_env):
    monkeypatch.setenv("GRPC_TLS_MODE", "off")
    monkeypatch.delenv("ATTESTATION_ENABLED", raising=False)

    with pytest.raises(SystemExit, match="ATTESTATION_PRIVATE_KEY_PATH"):
        await serve()


@pytest.mark.asyncio
async def test_serve_refuses_to_start_with_a_malformed_mapping(monkeypatch, startable_env, tmp_path):
    """Fail closed: a mapping that does not load would otherwise fail every request that names it."""
    mappings = tmp_path / "mappings"
    mappings.mkdir()
    (mappings / "broken-1.0-json.yaml").write_text("mapping_version: 1\nid: [unclosed\n")
    monkeypatch.setenv("MAPPINGS_PATH", str(mappings))
    monkeypatch.setenv("GRPC_TLS_MODE", "off")
    monkeypatch.setenv("ATTESTATION_ENABLED", "false")

    with pytest.raises(
        SystemExit, match=r"Configuration error: Mapping .*broken-1\.0-json\.yaml: invalid YAML at line"
    ):
        await serve()


@pytest.mark.asyncio
async def test_serve_refuses_to_start_when_the_mappings_path_is_missing(monkeypatch, startable_env, tmp_path):
    monkeypatch.setenv("MAPPINGS_PATH", str(tmp_path / "absent"))

    with pytest.raises(SystemExit, match="MAPPINGS_PATH"):
        await serve()


@pytest.mark.asyncio
async def test_serve_refuses_to_start_when_the_html_profiles_path_is_not_a_directory(
    monkeypatch, startable_env, tmp_path
):
    not_a_directory = tmp_path / "profiles.yaml"
    not_a_directory.write_text("not a directory")
    monkeypatch.setenv("HTML_PROFILES_PATH", str(not_a_directory))
    monkeypatch.setenv("GRPC_TLS_MODE", "off")
    monkeypatch.setenv("ATTESTATION_ENABLED", "false")

    with pytest.raises(SystemExit, match=r"Configuration error: .*HTML_PROFILES_PATH"):
        await serve()


@pytest.mark.asyncio
async def test_serve_refuses_to_start_with_mutual_tls_and_no_certs(monkeypatch, startable_env):
    monkeypatch.setenv("ATTESTATION_ENABLED", "false")
    monkeypatch.delenv("GRPC_TLS_MODE", raising=False)

    with pytest.raises(SystemExit, match="GRPC_TLS_CERT"):
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
        "GRPC_TLS_MODE": "off",
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


@pytest.mark.asyncio
async def test_serve_refuses_to_start_with_a_nats_cert_and_no_key(monkeypatch, startable_env, pki_dir):
    """A half-configured client certificate used to fall back to plaintext without a word."""
    monkeypatch.setenv("ATTESTATION_ENABLED", "false")
    monkeypatch.setenv("GRPC_TLS_MODE", "off")
    monkeypatch.setenv("NATS_URL", "nats://127.0.0.1:4222")
    monkeypatch.setenv("NATS_TLS_CERT", str(pki_dir / "client.pem"))

    with pytest.raises(SystemExit, match=r"Configuration error: .*NATS_TLS_KEY"):
        await serve()


@pytest.mark.asyncio
async def test_serve_refuses_distributed_mode_without_nats_url(monkeypatch, startable_env):
    monkeypatch.setenv("ATTESTATION_ENABLED", "false")
    monkeypatch.setenv("GRPC_TLS_MODE", "off")
    monkeypatch.setenv("EXECUTION_MODE", "distributed")
    monkeypatch.delenv("NATS_URL", raising=False)

    with pytest.raises(SystemExit, match=r"Configuration error: .*NATS_URL"):
        await serve()


def test_server_serves_while_nats_does_not_answer(tmp_path):
    """NATS is optional at startup: the port opens at once and the connect retries in the background.

    The listener accepts TCP but never sends the NATS INFO line, so every connect attempt hangs
    until its timeout, as with a firewalled or wedged broker.
    """
    models = tmp_path / "models"
    models.mkdir()
    with socket.socket() as silent_nats:
        silent_nats.bind(("127.0.0.1", 0))
        silent_nats.listen(16)
        nats_port = silent_nats.getsockname()[1]
        env = {
            **os.environ,
            "STORAGE_PATH": str(tmp_path / "data"),
            "ATTESTATION_ENABLED": "false",
            "GRPC_TLS_MODE": "off",
            "GRPC_PORT": str(_free_port()),
            "METRICS_PORT": str(_free_port()),
            "DOCLING_ARTIFACTS_PATH": str(models),
            "NATS_URL": f"nats://127.0.0.1:{nats_port}",
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
                assert time.monotonic() < deadline, "server did not start while NATS hangs"
            else:
                raise AssertionError("server exited before listening")
            proc.send_signal(signal.SIGTERM)
            assert proc.wait(timeout=15) == 0
            rest = proc.stdout.read()
            assert "Server shutdown complete" in rest
            assert "Traceback" not in rest
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.mark.asyncio
async def test_server_refuses_a_port_another_server_holds():
    """gRPC sets SO_REUSEPORT by default, so a second instance on the same port started
    without error and the kernel split connections between the two."""
    settings = IngestionSettings(grpc_tls_mode="off")
    first, port = create_grpc_server(settings, "[::]:0")
    await first.start()
    try:
        with pytest.raises(RuntimeError, match="Failed to bind"):
            create_grpc_server(settings, f"[::]:{port}")
    finally:
        await first.stop(None)


@pytest.mark.asyncio
async def test_receive_limit_is_max_document_size_plus_request_overhead():
    """Messages up to max_document_size + overhead reach the handler, so the service can give its
    actionable size error; beyond that the transport rejects them before any handler runs."""
    settings = IngestionSettings(grpc_tls_mode="off", max_document_size=1000)
    limit = settings.max_document_size + _GRPC_REQUEST_OVERHEAD
    server, port = create_grpc_server(settings, "127.0.0.1:0")
    handler = grpc.unary_unary_rpc_method_handler(lambda request, _context: str(len(request)).encode())
    server.add_generic_rpc_handlers((grpc.method_handlers_generic_handler("test.Echo", {"Size": handler}),))
    await server.start()
    try:
        async with grpc.aio.insecure_channel(f"127.0.0.1:{port}") as channel:
            size = channel.unary_unary("/test.Echo/Size")
            for length in (settings.max_document_size + 1, limit):
                assert await size(b"x" * length) == str(length).encode()
            with pytest.raises(grpc.aio.AioRpcError) as exc:
                await size(b"x" * (limit + 1))
            assert exc.value.code() == grpc.StatusCode.RESOURCE_EXHAUSTED
    finally:
        await server.stop(None)


@pytest.fixture
def restore_logging():
    """serve() replaces the root logger's handlers; put them back so other tests keep pytest's capture."""
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    yield
    root.handlers[:] = handlers
    root.setLevel(level)


@pytest.mark.parametrize(
    ("name", "arg", "description"),
    [
        ("always_on", "", "AlwaysOnSampler"),
        ("always_off", "", "AlwaysOffSampler"),
        ("traceidratio", "", "TraceIdRatioBased{1.0}"),
        ("traceidratio", "0.25", "TraceIdRatioBased{0.25}"),
        ("parentbased_always_on", "", "ParentBased{root:AlwaysOnSampler,"),
        ("parentbased_always_off", "", "ParentBased{root:AlwaysOffSampler,"),
        ("parentbased_traceidratio", "0.5", "ParentBased{root:TraceIdRatioBased{0.5},"),
        ("PARENTBASED_ALWAYS_ON", "", "ParentBased{root:AlwaysOnSampler,"),
        ("parentbased_traceidratio", "", "ParentBased{root:TraceIdRatioBased{1.0},"),
    ],
)
def test_sampler_name_maps_to_the_standard_sampler(name, arg, description):
    assert _build_sampler(name, arg).get_description().startswith(description)


@pytest.mark.parametrize(
    ("name", "arg", "message"),
    [
        ("bogus", "", "OTEL_TRACES_SAMPLER 'bogus'"),
        ("traceidratio", "lots", "OTEL_TRACES_SAMPLER_ARG 'lots'"),
        ("traceidratio", "1.5", "OTEL_TRACES_SAMPLER_ARG '1.5'"),
        ("parentbased_traceidratio", "-0.1", "OTEL_TRACES_SAMPLER_ARG '-0.1'"),
        ("traceidratio", "nan", "OTEL_TRACES_SAMPLER_ARG 'nan'"),
    ],
)
def test_bad_sampler_settings_are_refused(name, arg, message):
    with pytest.raises(ValueError, match=message):
        _build_sampler(name, arg)


@pytest.mark.asyncio
async def test_serve_refuses_an_unknown_sampler(monkeypatch, startable_env, restore_logging):
    monkeypatch.setenv("GRPC_TLS_MODE", "off")
    monkeypatch.setenv("ATTESTATION_ENABLED", "false")
    monkeypatch.setenv("OTEL_TRACES_SAMPLER", "bogus")

    with pytest.raises(SystemExit, match=r"^Configuration error: OTEL_TRACES_SAMPLER 'bogus'"):
        await serve()


@pytest.mark.asyncio
async def test_serve_reports_every_configuration_error_at_once(monkeypatch, tmp_path, restore_logging):
    monkeypatch.delenv("DOCLING_ARTIFACTS_PATH", raising=False)
    monkeypatch.delenv("ATTESTATION_ENABLED", raising=False)
    monkeypatch.delenv("GRPC_TLS_MODE", raising=False)
    monkeypatch.setenv("MAPPINGS_PATH", str(tmp_path / "absent"))
    monkeypatch.setenv("OTEL_TRACES_SAMPLER", "bogus")

    with pytest.raises(SystemExit) as exit_info:
        await serve()

    lines = str(exit_info.value).splitlines()
    assert lines[0] == "Configuration errors:"
    problems = lines[1:]
    assert all(line.startswith("  - ") for line in problems)
    for expected in ("DOCLING_ARTIFACTS_PATH", "MAPPINGS_PATH", "ATTESTATION_PRIVATE_KEY_PATH", "GRPC_TLS", "bogus"):
        assert sum(expected in line for line in problems) == 1, expected
    assert len(problems) == 5


@pytest.mark.asyncio
async def test_serve_reports_distributed_mode_without_nats_alongside_other_errors(
    monkeypatch, startable_env, restore_logging
):
    monkeypatch.setenv("GRPC_TLS_MODE", "off")
    monkeypatch.setenv("ATTESTATION_ENABLED", "false")
    monkeypatch.setenv("EXECUTION_MODE", "distributed")
    monkeypatch.setenv("OTEL_TRACES_SAMPLER", "bogus")

    with pytest.raises(SystemExit) as exit_info:
        await serve()

    message = str(exit_info.value)
    assert message.startswith("Configuration errors:\n  - ")
    assert "NATS_URL" in message
    assert "bogus" in message


@pytest.mark.asyncio
async def test_serve_refuses_an_occupied_metrics_port(monkeypatch, startable_env, restore_logging):
    monkeypatch.setenv("GRPC_TLS_MODE", "off")
    monkeypatch.setenv("ATTESTATION_ENABLED", "false")
    with socket.socket() as holder:
        holder.bind(("0.0.0.0", 0))  # noqa: S104 - start_http_server listens on every interface
        holder.listen()
        monkeypatch.setenv("METRICS_PORT", str(holder.getsockname()[1]))

        with pytest.raises(SystemExit, match=r"^Configuration error: .*metrics.*"):
            await serve()


@pytest.mark.asyncio
async def test_every_log_line_from_a_refused_start_is_json(monkeypatch, restore_logging, capsys):
    """The TLS-off warning used to print as plain text because JSON logging was set up after it."""
    monkeypatch.delenv("DOCLING_ARTIFACTS_PATH", raising=False)
    monkeypatch.setenv("GRPC_TLS_MODE", "off")
    monkeypatch.setenv("GRPC_PORT", "0")

    with pytest.raises(SystemExit):
        await serve()

    lines = [line for line in capsys.readouterr().err.splitlines() if line.strip()]
    records = [json.loads(line) for line in lines]
    assert any(r["levelname"] == "WARNING" and "plaintext" in r["message"] for r in records)
