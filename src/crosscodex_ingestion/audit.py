"""
audit.py — audit event publisher: core NATS publish, or structured logging when NATS_URL is unset.

Publishes audit events to NATS subject: tenant.<tenant_id>.audit.ingestion
Headers: X-Trace-Id, X-Span-Id, X-Tenant-Id, X-Timestamp, X-Content-SHA256

This is a core NATS publish with no JetStream acknowledgement: an event is kept only if the
operator defines a JetStream stream on tenant.*.audit.ingestion. When NATS_URL is set but NATS is
not connected, publish raises AuditUnavailableError rather than dropping the event.
"""

import json
import logging
from datetime import UTC, datetime
from hashlib import sha256

from crosscodex_ingestion.config import IngestionSettings
from crosscodex_ingestion.nats_connection import NatsConnection, NatsUnavailableError

logger = logging.getLogger(__name__)


class AuditUnavailableError(NatsUnavailableError):
    """NATS is configured but not connected, so the audit event cannot be recorded."""


class AuditPublisher:
    """
    Publishes audit events over the shared NATS connection, or logs them when NATS_URL is unset.
    """

    def __init__(self, settings: IngestionSettings, connection: NatsConnection | None):
        """
        Initialize the audit publisher.

        Args:
            settings: Configuration settings.
            connection: The shared NATS connection, or None when NATS_URL is unset (log fallback).
        """
        self.settings = settings
        self._connection = connection
        if connection is None:
            logger.info("NATS URL not configured, using logging fallback for audit events")

    async def publish(
        self,
        tenant_id: str,
        event: dict,
        trace_id: str = "",
        span_id: str = "",
    ) -> None:
        """
        Publish an audit event to NATS, or log it when NATS_URL is unset.

        Args:
            tenant_id: Tenant identifier.
            event: Event dictionary to publish.
            trace_id: Optional trace ID for distributed tracing.
            span_id: Optional span ID for distributed tracing.

        Raises:
            AuditUnavailableError: If NATS is configured but not connected.
        """
        # Serialize event to JSON bytes
        payload = json.dumps(event, separators=(",", ":")).encode("utf-8")

        # Compute SHA-256 of payload
        content_hash = sha256(payload).hexdigest()

        # Generate timestamp
        timestamp = datetime.now(UTC).isoformat()

        if self._connection is not None:
            client = self._connection.client
            if client is None or not self._connection.available:
                raise AuditUnavailableError(
                    f"NATS at {self._connection.url} is unavailable; "
                    f"audit event for tenant {tenant_id} was not recorded"
                )
            subject = f"tenant.{tenant_id}.audit.ingestion"
            headers = {
                "X-Trace-Id": trace_id,
                "X-Span-Id": span_id,
                "X-Tenant-Id": tenant_id,
                "X-Timestamp": timestamp,
                "X-Content-SHA256": content_hash,
            }

            await client.publish(subject, payload, headers=headers)
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
