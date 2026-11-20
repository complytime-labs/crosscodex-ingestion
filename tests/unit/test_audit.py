"""
test_audit.py — Tests for audit event publishing over core NATS, with a logging fallback when NATS_URL is unset.
"""

import json
from hashlib import sha256
from unittest.mock import AsyncMock, Mock

import pytest

from crosscodex_ingestion.audit import AuditPublisher, AuditUnavailableError
from crosscodex_ingestion.config import IngestionSettings
from crosscodex_ingestion.nats_connection import NatsConnection, NatsUnavailableError


def _connection(available: bool = True) -> Mock:
    connection = Mock(spec=NatsConnection)
    connection.client = AsyncMock()
    connection.available = available
    connection.url = "nats://localhost:4222"
    return connection


@pytest.mark.asyncio
async def test_nats_publish_sends_correct_subject_and_payload():
    """Test that publish sends to correct NATS subject with correct payload."""
    settings = IngestionSettings(nats_url="nats://localhost:4222")
    connection = _connection()
    publisher = AuditPublisher(settings, connection)

    event = {"action": "document_ingested", "doc_id": "12345"}
    await publisher.publish(
        tenant_id="tenant-a",
        event=event,
        trace_id="trace-123",
        span_id="span-456",
    )

    # Verify publish was called
    assert connection.client.publish.call_count == 1
    call_args = connection.client.publish.call_args

    # Check subject
    assert call_args[0][0] == "tenant.tenant-a.audit.ingestion"

    # Check payload is JSON-encoded event
    payload = call_args[0][1]
    assert json.loads(payload) == event


@pytest.mark.asyncio
async def test_nats_publish_includes_all_required_headers():
    """Test that all 5 required headers are present."""
    settings = IngestionSettings(nats_url="nats://localhost:4222")
    connection = _connection()
    publisher = AuditPublisher(settings, connection)

    event = {"action": "test"}
    await publisher.publish(
        tenant_id="tenant-b",
        event=event,
        trace_id="trace-abc",
        span_id="span-def",
    )

    # Extract headers from publish call
    headers = connection.client.publish.call_args[1]["headers"]

    # Verify all required headers
    assert "X-Trace-Id" in headers
    assert "X-Span-Id" in headers
    assert "X-Tenant-Id" in headers
    assert "X-Timestamp" in headers
    assert "X-Content-SHA256" in headers

    assert headers["X-Trace-Id"] == "trace-abc"
    assert headers["X-Span-Id"] == "span-def"
    assert headers["X-Tenant-Id"] == "tenant-b"


@pytest.mark.asyncio
async def test_nats_publish_content_sha256_is_correct():
    """Test that X-Content-SHA256 header contains correct SHA-256 of payload."""
    settings = IngestionSettings(nats_url="nats://localhost:4222")
    connection = _connection()
    publisher = AuditPublisher(settings, connection)

    event = {"action": "audit_test", "value": 42}
    await publisher.publish(tenant_id="tenant-c", event=event)

    # Get payload and headers
    payload = connection.client.publish.call_args[0][1]
    headers = connection.client.publish.call_args[1]["headers"]

    # Compute expected SHA-256
    expected_sha256 = sha256(payload).hexdigest()

    assert headers["X-Content-SHA256"] == expected_sha256


@pytest.mark.asyncio
async def test_publish_while_nats_is_unavailable_raises_instead_of_dropping(caplog):
    """A configured but disconnected NATS must not lose the event silently or log it as if delivered."""
    settings = IngestionSettings(nats_url="nats://localhost:4222")
    connection = _connection(available=False)
    publisher = AuditPublisher(settings, connection)

    with pytest.raises(AuditUnavailableError, match=r"NATS at nats://localhost:4222 is unavailable"):
        await publisher.publish(tenant_id="tenant-d", event={"action": "lost"})

    connection.client.publish.assert_not_called()
    assert not [r for r in caplog.records if "lost" in r.getMessage()]


def test_audit_unavailable_is_a_nats_unavailable_error():
    """Callers that handle any NATS outage (the servicer) catch both with one except clause."""
    assert issubclass(AuditUnavailableError, NatsUnavailableError)
    assert issubclass(AuditUnavailableError, RuntimeError)


@pytest.mark.asyncio
async def test_publish_before_the_first_connect_raises():
    settings = IngestionSettings(nats_url="nats://localhost:4222")
    connection = _connection(available=False)
    connection.client = None
    publisher = AuditPublisher(settings, connection)

    with pytest.raises(AuditUnavailableError):
        await publisher.publish(tenant_id="tenant-e", event={"action": "early"})


@pytest.mark.asyncio
async def test_log_fallback_when_no_nats_url(caplog):
    """Test that when nats_url is empty, publish logs the event instead."""
    settings = IngestionSettings(nats_url="")

    publisher = AuditPublisher(settings, None)

    event = {"action": "document_processed", "size": 1024}

    with caplog.at_level("INFO"):
        await publisher.publish(
            tenant_id="tenant-log",
            event=event,
            trace_id="trace-log",
            span_id="span-log",
        )

    # Verify event was logged
    assert len(caplog.records) > 0

    # Find the audit log record
    audit_records = [r for r in caplog.records if "audit" in r.message.lower()]
    assert len(audit_records) > 0

    # Verify tenant_id and event appear in the log
    log_text = " ".join([r.message for r in caplog.records])
    assert "tenant-log" in log_text
    assert "document_processed" in log_text
