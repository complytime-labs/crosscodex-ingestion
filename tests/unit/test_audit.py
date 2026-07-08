"""
test_audit.py — Tests for audit event publishing to NATS JetStream with logging fallback.
"""

import json
from hashlib import sha256
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from crosscodex_ingestion.audit import AuditPublisher
from crosscodex_ingestion.config import IngestionSettings


@pytest.mark.asyncio
async def test_nats_publish_sends_correct_subject_and_payload():
    """Test that publish sends to correct NATS subject with correct payload."""
    settings = IngestionSettings(nats_url="nats://localhost:4222")

    with patch("nats.connect", new_callable=AsyncMock) as mock_connect:
        mock_nc = AsyncMock()
        mock_connect.return_value = mock_nc

        publisher = AuditPublisher(settings)
        await publisher.connect()

        event = {"action": "document_ingested", "doc_id": "12345"}
        await publisher.publish(
            tenant_id="tenant-a",
            event=event,
            trace_id="trace-123",
            span_id="span-456",
        )

        # Verify publish was called
        assert mock_nc.publish.call_count == 1
        call_args = mock_nc.publish.call_args

        # Check subject
        assert call_args[0][0] == "tenant.tenant-a.audit.ingestion"

        # Check payload is JSON-encoded event
        payload = call_args[0][1]
        assert json.loads(payload) == event


@pytest.mark.asyncio
async def test_nats_publish_includes_all_required_headers():
    """Test that all 5 required headers are present."""
    settings = IngestionSettings(nats_url="nats://localhost:4222")

    with patch("nats.connect", new_callable=AsyncMock) as mock_connect:
        mock_nc = AsyncMock()
        mock_connect.return_value = mock_nc

        publisher = AuditPublisher(settings)
        await publisher.connect()

        event = {"action": "test"}
        await publisher.publish(
            tenant_id="tenant-b",
            event=event,
            trace_id="trace-abc",
            span_id="span-def",
        )

        # Extract headers from publish call
        headers = mock_nc.publish.call_args[1]["headers"]

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

    with patch("nats.connect", new_callable=AsyncMock) as mock_connect:
        mock_nc = AsyncMock()
        mock_connect.return_value = mock_nc

        publisher = AuditPublisher(settings)
        await publisher.connect()

        event = {"action": "audit_test", "value": 42}
        await publisher.publish(tenant_id="tenant-c", event=event)

        # Get payload and headers
        payload = mock_nc.publish.call_args[0][1]
        headers = mock_nc.publish.call_args[1]["headers"]

        # Compute expected SHA-256
        expected_sha256 = sha256(payload).hexdigest()

        assert headers["X-Content-SHA256"] == expected_sha256


@pytest.mark.asyncio
async def test_log_fallback_when_no_nats_url(caplog):
    """Test that when nats_url is empty, publish logs the event instead."""
    settings = IngestionSettings(nats_url="")

    publisher = AuditPublisher(settings)
    await publisher.connect()

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


@pytest.mark.asyncio
async def test_close_cleans_up_nats_connection():
    """Test that close() properly closes the NATS connection."""
    settings = IngestionSettings(nats_url="nats://localhost:4222")

    with patch("nats.connect", new_callable=AsyncMock) as mock_connect:
        mock_nc = AsyncMock()
        mock_connect.return_value = mock_nc

        publisher = AuditPublisher(settings)
        await publisher.connect()
        await publisher.close()

        # Verify close was called
        mock_nc.close.assert_called_once()


@pytest.mark.asyncio
async def test_tls_connection_when_certs_provided():
    """Test that TLS SSLContext is created when cert/key/ca are provided."""
    settings = IngestionSettings(
        nats_url="nats://localhost:4222",
        nats_tls_cert="/path/to/cert.pem",
        nats_tls_key="/path/to/key.pem",
        nats_tls_ca="/path/to/ca.pem",
    )

    with patch("nats.connect", new_callable=AsyncMock) as mock_connect:
        with patch("ssl.create_default_context") as mock_ssl_context:
            mock_ctx = MagicMock()
            mock_ssl_context.return_value = mock_ctx
            mock_nc = AsyncMock()
            mock_connect.return_value = mock_nc

            publisher = AuditPublisher(settings)
            await publisher.connect()

            # Verify SSL context was created
            mock_ssl_context.assert_called_once()

            # Verify connect was called with tls parameter
            mock_connect.assert_called_once()
            call_kwargs = mock_connect.call_args[1]
            assert "tls" in call_kwargs
            assert call_kwargs["tls"] == mock_ctx


@pytest.mark.asyncio
async def test_close_is_noop_for_log_fallback():
    """Test that close() is a no-op when using log fallback."""
    settings = IngestionSettings(nats_url="")

    publisher = AuditPublisher(settings)
    await publisher.connect()

    # Should not raise
    await publisher.close()
