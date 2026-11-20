"""Unit tests for the shared NATS connection: TLS context rules and the backoff connect loop."""

import asyncio
import logging
import ssl
from pathlib import Path

import pytest

from crosscodex_ingestion import nats_connection
from crosscodex_ingestion.config import IngestionSettings
from crosscodex_ingestion.nats_connection import NatsConnection, build_nats_tls_context

URL = "nats://nats.invalid:4222"
# The sleeps fixture patches asyncio.sleep; waiting on the loop must not show up as a backoff delay.
_real_sleep = asyncio.sleep


class FakeClient:
    """Stands in for nats.aio.client.Client: connection state plus the options nats.connect stored."""

    def __init__(self) -> None:
        self.is_connected = True
        self.is_closed = False
        self.options = {"max_reconnect_attempts": 1}
        self.drained = False
        self.closed = False

    async def drain(self) -> None:
        self.drained = True
        self.is_connected = False
        self.is_closed = True

    async def close(self) -> None:
        self.closed = True
        self.is_connected = False
        self.is_closed = True


@pytest.fixture
def load_cert_chain_calls(monkeypatch) -> list[dict]:
    """Record SSLContext.load_cert_chain calls (the context exposes no getter for its client cert)."""
    calls: list[dict] = []
    real = ssl.SSLContext.load_cert_chain

    def spy(self, *args, **kwargs):
        calls.append(kwargs)
        return real(self, *args, **kwargs)

    monkeypatch.setattr(ssl.SSLContext, "load_cert_chain", spy)
    return calls


class TestBuildNatsTlsContext:
    def test_no_tls_vars_gives_no_context(self):
        assert build_nats_tls_context(IngestionSettings(nats_url=URL)) is None

    def test_ca_only_gives_server_auth_tls_without_a_client_cert(self, pki_dir: Path, load_cert_chain_calls):
        context = build_nats_tls_context(IngestionSettings(nats_url=URL, nats_tls_ca=str(pki_dir / "ca.pem")))

        assert context is not None
        assert context.verify_mode == ssl.CERT_REQUIRED
        assert context.check_hostname
        assert len(context.get_ca_certs()) == 1
        assert load_cert_chain_calls == []

    def test_cert_key_and_ca_give_mutual_tls(self, pki_dir: Path, load_cert_chain_calls):
        context = build_nats_tls_context(
            IngestionSettings(
                nats_url=URL,
                nats_tls_cert=str(pki_dir / "client.pem"),
                nats_tls_key=str(pki_dir / "client.key"),
                nats_tls_ca=str(pki_dir / "ca.pem"),
            )
        )

        assert context is not None
        assert context.verify_mode == ssl.CERT_REQUIRED
        assert len(context.get_ca_certs()) == 1
        assert load_cert_chain_calls == [
            {"certfile": str(pki_dir / "client.pem"), "keyfile": str(pki_dir / "client.key")}
        ]

    def test_cert_and_key_without_ca_give_mutual_tls_against_system_roots(self, pki_dir: Path, load_cert_chain_calls):
        context = build_nats_tls_context(
            IngestionSettings(
                nats_url=URL,
                nats_tls_cert=str(pki_dir / "client.pem"),
                nats_tls_key=str(pki_dir / "client.key"),
            )
        )

        assert context is not None
        assert context.verify_mode == ssl.CERT_REQUIRED
        assert len(load_cert_chain_calls) == 1

    def test_cert_without_key_is_rejected(self, pki_dir: Path):
        settings = IngestionSettings(nats_url=URL, nats_tls_cert=str(pki_dir / "client.pem"))

        with pytest.raises(ValueError, match="NATS_TLS_KEY"):
            build_nats_tls_context(settings)

    def test_key_without_cert_is_rejected(self, pki_dir: Path):
        settings = IngestionSettings(
            nats_url=URL, nats_tls_key=str(pki_dir / "client.key"), nats_tls_ca=str(pki_dir / "ca.pem")
        )

        with pytest.raises(ValueError, match="NATS_TLS_CERT"):
            build_nats_tls_context(settings)


@pytest.fixture
def sleeps(monkeypatch) -> list[float]:
    """Record the backoff delays instead of sleeping them."""
    delays: list[float] = []

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)
        await _real_sleep(0)

    monkeypatch.setattr(nats_connection.asyncio, "sleep", fake_sleep)
    return delays


async def _until(predicate) -> None:
    for _ in range(1000):
        if predicate():
            return
        await _real_sleep(0)
    raise AssertionError("condition not reached")


class TestNatsConnection:
    async def test_backs_off_then_connects_and_logs_without_tracebacks(self, monkeypatch, sleeps, caplog):
        client = FakeClient()
        attempts: list[dict] = []

        async def fake_connect(**kwargs):
            attempts.append(kwargs)
            if len(attempts) <= 2:
                raise OSError("connection refused")
            return client

        monkeypatch.setattr(nats_connection.nats, "connect", fake_connect)
        connection = NatsConnection(IngestionSettings(nats_url=URL))

        with caplog.at_level(logging.INFO, logger="crosscodex_ingestion.nats_connection"):
            await connection.start()
            await _until(lambda: connection.client is not None)

        assert connection.available
        assert sleeps == [1, 2]
        assert [r.exc_info for r in caplog.records] == [None] * len(caplog.records)
        assert [(r.levelno, r.getMessage()) for r in caplog.records] == [
            (logging.WARNING, f"NATS at {URL} unreachable (connection refused); retrying in 1s"),
            (logging.WARNING, f"NATS at {URL} unreachable (connection refused); retrying in 2s"),
            (logging.INFO, f"Connected to NATS at {URL}"),
        ]
        assert attempts[0]["servers"] == [URL]
        assert attempts[0]["connect_timeout"] == nats_connection.CONNECT_TIMEOUT_SECONDS
        # nats-py's own reconnect takes over once connected, with no attempt limit
        assert client.options["max_reconnect_attempts"] == -1
        await connection.close()

    async def test_backoff_doubles_up_to_sixty_seconds(self, monkeypatch, sleeps):
        attempts = 0

        async def fake_connect(**_kwargs):
            nonlocal attempts
            attempts += 1
            if attempts <= 8:
                raise OSError("connection refused")
            return FakeClient()

        monkeypatch.setattr(nats_connection.nats, "connect", fake_connect)
        connection = NatsConnection(IngestionSettings(nats_url=URL))

        await connection.start()
        await _until(lambda: connection.available)

        assert sleeps == [1, 2, 4, 8, 16, 32, 60, 60]
        await connection.close()

    async def test_reports_the_error_nats_passed_to_error_cb(self, monkeypatch, sleeps, caplog):
        """nats-py hands the real cause to error_cb and then raises a generic NoServersError."""
        from nats.errors import NoServersError

        calls = 0

        async def fake_connect(**kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                await kwargs["error_cb"](ConnectionRefusedError("[Errno 111] Connect call failed"))
                raise NoServersError
            return FakeClient()

        monkeypatch.setattr(nats_connection.nats, "connect", fake_connect)
        connection = NatsConnection(IngestionSettings(nats_url=URL))

        with caplog.at_level(logging.WARNING, logger="crosscodex_ingestion.nats_connection"):
            await connection.start()
            await _until(lambda: connection.available)

        assert [r.getMessage() for r in caplog.records] == [
            f"NATS at {URL} unreachable ([Errno 111] Connect call failed); retrying in 1s"
        ]
        await connection.close()

    async def test_a_timeout_is_named_rather_than_logged_as_an_empty_message(self, monkeypatch, sleeps, caplog):
        calls = 0

        async def fake_connect(**_kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise TimeoutError
            return FakeClient()

        monkeypatch.setattr(nats_connection.nats, "connect", fake_connect)
        connection = NatsConnection(IngestionSettings(nats_url=URL))

        with caplog.at_level(logging.WARNING, logger="crosscodex_ingestion.nats_connection"):
            await connection.start()
            await _until(lambda: connection.available)

        assert [r.getMessage() for r in caplog.records] == [f"NATS at {URL} unreachable (TimeoutError); retrying in 1s"]
        await connection.close()

    async def test_unavailable_before_the_first_connect(self, monkeypatch):
        never = asyncio.Event()

        async def hanging_connect(**_kwargs):
            await never.wait()

        monkeypatch.setattr(nats_connection.nats, "connect", hanging_connect)
        connection = NatsConnection(IngestionSettings(nats_url=URL))

        await connection.start()

        assert connection.client is None
        assert not connection.available
        await connection.close()

    async def test_unavailable_while_the_client_is_disconnected(self, monkeypatch):
        client = FakeClient()

        async def fake_connect(**_kwargs):
            return client

        monkeypatch.setattr(nats_connection.nats, "connect", fake_connect)
        connection = NatsConnection(IngestionSettings(nats_url=URL))
        await connection.start()
        await _until(lambda: connection.available)

        client.is_connected = False  # nats-py is reconnecting
        assert not connection.available

        client.is_connected = True  # reconnected
        assert connection.available
        await connection.close()

    async def test_close_cancels_a_pending_connect_loop(self, monkeypatch):
        never = asyncio.Event()
        started = asyncio.Event()

        async def hanging_connect(**_kwargs):
            started.set()
            await never.wait()

        monkeypatch.setattr(nats_connection.nats, "connect", hanging_connect)
        connection = NatsConnection(IngestionSettings(nats_url=URL))
        await connection.start()
        await started.wait()

        await connection.close()

        assert connection.client is None
        assert not connection.available

    async def test_close_drains_a_connected_client(self, monkeypatch):
        client = FakeClient()

        async def fake_connect(**_kwargs):
            return client

        monkeypatch.setattr(nats_connection.nats, "connect", fake_connect)
        connection = NatsConnection(IngestionSettings(nats_url=URL))
        await connection.start()
        await _until(lambda: connection.available)

        await connection.close()

        assert client.drained
        assert not connection.available

    async def test_close_closes_a_reconnecting_client(self, monkeypatch):
        """drain() raises while nats-py is reconnecting, so a disconnected client is closed instead."""
        client = FakeClient()

        async def fake_connect(**_kwargs):
            return client

        monkeypatch.setattr(nats_connection.nats, "connect", fake_connect)
        connection = NatsConnection(IngestionSettings(nats_url=URL))
        await connection.start()
        await _until(lambda: connection.available)
        client.is_connected = False

        await connection.close()

        assert client.closed
        assert not client.drained

    def test_url_in_messages_omits_credentials(self):
        connection = NatsConnection(IngestionSettings(nats_url="nats://audit:s3cret@nats.internal:4222"))

        assert connection.url == "nats://nats.internal:4222"

    async def test_connects_with_the_configured_url_including_credentials(self, monkeypatch):
        attempts: list[dict] = []

        async def fake_connect(**kwargs):
            attempts.append(kwargs)
            return FakeClient()

        monkeypatch.setattr(nats_connection.nats, "connect", fake_connect)
        connection = NatsConnection(IngestionSettings(nats_url="nats://audit:s3cret@nats.internal:4222"))
        await connection.start()
        await _until(lambda: connection.available)

        assert attempts[0]["servers"] == ["nats://audit:s3cret@nats.internal:4222"]
        await connection.close()
