"""
nats_connection.py — the one NATS connection shared by audit publishing and distributed dispatch.

The service serves while NATS is down. start() returns at once; a background loop connects with
exponential backoff, and after the first connect nats-py reconnects on its own without a limit.
`available` tells callers whether a publish can go out now.
"""

import asyncio
import logging
import ssl
from typing import TYPE_CHECKING
from urllib.parse import urlsplit, urlunsplit

import nats

from crosscodex_ingestion.config import IngestionSettings

if TYPE_CHECKING:
    from nats.aio.client import Client

logger = logging.getLogger(__name__)

CONNECT_TIMEOUT_SECONDS = 5
INITIAL_BACKOFF_SECONDS = 1
MAX_BACKOFF_SECONDS = 60


# Sent to clients while NATS is down. CWE-209: the broker's address goes to the log only.
NATS_UNAVAILABLE_MESSAGE = "NATS is unavailable; audit events cannot be recorded. Retry later."


class NatsUnavailableError(RuntimeError):
    """NATS is configured but not connected. The message names the URL, so log it; do not send it to clients."""


def build_nats_tls_context(settings: IngestionSettings) -> ssl.SSLContext | None:
    """Build the TLS context for NATS from NATS_TLS_CERT, NATS_TLS_KEY and NATS_TLS_CA.

    None of them set: no TLS context. CA only: TLS that verifies the server against that CA.
    Cert and key (with or without CA): mutual TLS; without a CA the server is verified against the
    system trust store.

    Raises:
        ValueError: If only one of NATS_TLS_CERT and NATS_TLS_KEY is set.
        OSError: If a file cannot be read or does not hold a valid certificate or key.
    """
    cert, key, ca = settings.nats_tls_cert, settings.nats_tls_key, settings.nats_tls_ca
    if not (cert or key or ca):
        return None
    if bool(cert) != bool(key):
        missing, present = ("NATS_TLS_KEY", "NATS_TLS_CERT") if cert else ("NATS_TLS_CERT", "NATS_TLS_KEY")
        raise ValueError(f"{present} is set but {missing} is not; NATS mutual TLS needs both")
    context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH, cafile=ca or None)
    if cert:
        context.load_cert_chain(certfile=cert, keyfile=key)
    return context


class NatsConnection:
    """A NATS client that connects in the background and reports whether it is connected."""

    def __init__(self, settings: IngestionSettings):
        """Validate the TLS settings; nothing connects until start().

        Raises:
            ValueError, OSError: From build_nats_tls_context.
        """
        self._servers = [settings.nats_url]
        self._tls = build_nats_tls_context(settings)
        # For log and error messages, which must not carry credentials embedded in NATS_URL.
        parts = urlsplit(settings.nats_url)
        self.url = urlunsplit(parts._replace(netloc=parts.netloc.rpartition("@")[2]))
        self.client: Client | None = None
        self._connect_task: asyncio.Task | None = None
        # Cause of the current failed attempt: nats-py reports it to error_cb, then raises NoServersError.
        self._attempt_error: Exception | None = None

    @property
    def available(self) -> bool:
        """True only while the client is connected (not before the first connect, nor while reconnecting)."""
        return self.client is not None and self.client.is_connected

    async def start(self) -> None:
        """Start connecting in the background and return at once."""
        self._connect_task = asyncio.create_task(self._connect_with_backoff())

    async def _connect_with_backoff(self) -> None:
        delay = INITIAL_BACKOFF_SECONDS
        while True:
            self._attempt_error = None
            try:
                client = await nats.connect(
                    servers=self._servers,
                    tls=self._tls,
                    connect_timeout=CONNECT_TIMEOUT_SECONDS,
                    # nats-py retries the first connect itself until a server's attempts exceed this
                    # (0 or less would retry forever), so 1 gives two tries; this loop does the backoff.
                    max_reconnect_attempts=1,
                    error_cb=self._on_error,
                    disconnected_cb=self._on_disconnected,
                    reconnected_cb=self._on_reconnected,
                )
            except Exception as exc:  # any failure to connect is retried; the cause is logged
                cause = self._attempt_error or exc
                logger.warning(
                    "NATS at %s unreachable (%s); retrying in %ds",
                    self.url,
                    str(cause) or type(cause).__name__,
                    delay,
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2, MAX_BACKOFF_SECONDS)
                continue
            # nats-py reads this option on every reconnect: from now on it never gives up.
            client.options["max_reconnect_attempts"] = -1
            self.client = client
            logger.info("Connected to NATS at %s", self.url)
            return

    async def _on_error(self, exc: Exception) -> None:
        """Replace nats-py's default callback, which logs every failed attempt at ERROR with a traceback."""
        if self.client is None:
            self._attempt_error = exc
            return
        logger.warning("NATS at %s: %s", self.url, str(exc) or type(exc).__name__)

    async def _on_disconnected(self) -> None:
        if self.client is not None and self.client.is_closed:
            return  # our own close() at shutdown
        logger.warning(
            "Disconnected from NATS at %s; ConvertDocument returns UNAVAILABLE until it reconnects", self.url
        )

    async def _on_reconnected(self) -> None:
        logger.info("Reconnected to NATS at %s", self.url)

    async def close(self) -> None:
        """Stop connecting and close the client, flushing pending publishes when connected."""
        if self._connect_task is not None:
            self._connect_task.cancel()
            await asyncio.wait([self._connect_task])
        if self.client is None or self.client.is_closed:
            return
        if self.client.is_connected:
            await self.client.drain()
        else:
            # drain() raises while nats-py is reconnecting
            await self.client.close()
