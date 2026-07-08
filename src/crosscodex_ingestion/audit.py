"""
audit.py — NATS JetStream audit event publisher with logging fallback.

Publishes audit events to NATS subject: tenant.<tenant_id>.audit.ingestion
Headers: X-Trace-Id, X-Span-Id, X-Tenant-Id, X-Timestamp, X-Content-SHA256

When nats_url is empty, falls back to structured logging instead.
"""

import json
import logging
import ssl
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any

import nats
from nats.aio.client import Client as NATS

from crosscodex_ingestion.config import IngestionSettings

logger = logging.getLogger(__name__)


class AuditPublisher:
    """
    Publishes audit events to NATS JetStream or logs them when NATS is unavailable.
    """

    def __init__(self, settings: IngestionSettings):
        """
        Initialize the audit publisher.

        Args:
            settings: Configuration settings containing NATS connection details.
        """
        self.settings = settings
        self._nc: NATS | None = None
        self._use_nats = bool(settings.nats_url)

    async def connect(self) -> None:
        """
        Connect to NATS server. No-op if using log fallback.
        """
        if not self._use_nats:
            logger.info("NATS URL not configured, using logging fallback for audit events")
            return

        # Build TLS context if certificates are provided
        tls_context: ssl.SSLContext | None = None
        if self.settings.nats_tls_cert and self.settings.nats_tls_key and self.settings.nats_tls_ca:
            tls_context = ssl.create_default_context(
                purpose=ssl.Purpose.SERVER_AUTH,
                cafile=self.settings.nats_tls_ca,
            )
            tls_context.load_cert_chain(
                certfile=self.settings.nats_tls_cert,
                keyfile=self.settings.nats_tls_key,
            )

        # Connect to NATS
        connect_kwargs: dict[str, Any] = {"servers": self.settings.nats_url}
        if tls_context:
            connect_kwargs["tls"] = tls_context

        self._nc = await nats.connect(**connect_kwargs)
        logger.info("Connected to NATS at %s", self.settings.nats_url)

    async def publish(
        self,
        tenant_id: str,
        event: dict,
        trace_id: str = "",
        span_id: str = "",
    ) -> None:
        """
        Publish an audit event to NATS or log it.

        Args:
            tenant_id: Tenant identifier.
            event: Event dictionary to publish.
            trace_id: Optional trace ID for distributed tracing.
            span_id: Optional span ID for distributed tracing.
        """
        # Serialize event to JSON bytes
        payload = json.dumps(event, separators=(",", ":")).encode("utf-8")

        # Compute SHA-256 of payload
        content_hash = sha256(payload).hexdigest()

        # Generate timestamp
        timestamp = datetime.now(UTC).isoformat()

        if self._use_nats and self._nc:
            # Publish to NATS
            subject = f"tenant.{tenant_id}.audit.ingestion"
            headers = {
                "X-Trace-Id": trace_id,
                "X-Span-Id": span_id,
                "X-Tenant-Id": tenant_id,
                "X-Timestamp": timestamp,
                "X-Content-SHA256": content_hash,
            }

            await self._nc.publish(subject, payload, headers=headers)
        else:
            # Log fallback
            logger.info(
                "Audit event for tenant %s: %s (trace=%s, span=%s, hash=%s)",
                tenant_id,
                event,
                trace_id,
                span_id,
                content_hash,
            )

    async def close(self) -> None:
        """
        Close the NATS connection gracefully. No-op for log fallback.
        """
        if self._nc:
            await self._nc.close()
            logger.info("NATS connection closed")
