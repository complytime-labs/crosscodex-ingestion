"""Unit tests for dispatch module."""

import asyncio
import base64
import json
from unittest.mock import AsyncMock, Mock, patch

import pytest

from crosscodex_ingestion.attestation import AttestationManager
from crosscodex_ingestion.audit import AuditPublisher
from crosscodex_ingestion.config import IngestionSettings
from crosscodex_ingestion.dispatch import (
    EmbeddedDispatch,
    NatsDispatch,
    create_dispatch,
)
from crosscodex_ingestion.state import StateBackend
from crosscodex_ingestion.storage import StorageBackend


@pytest.fixture
def mock_settings():
    """Create mock IngestionSettings for testing."""
    settings = Mock(spec=IngestionSettings)
    settings.max_concurrent_conversions = 2
    settings.execution_mode = "embedded"
    settings.nats_url = "nats://localhost:4222"
    settings.nats_tls_cert = ""
    settings.nats_tls_key = ""
    settings.nats_tls_ca = ""
    return settings


@pytest.fixture
def mock_state():
    """Create mock StateBackend for testing."""
    return Mock(spec=StateBackend)


@pytest.fixture
def mock_storage():
    """Create mock StorageBackend for testing."""
    return Mock(spec=StorageBackend)


@pytest.fixture
def mock_attestation():
    """Create mock AttestationManager for testing."""
    return Mock(spec=AttestationManager)


@pytest.fixture
def mock_audit():
    """Create mock AuditPublisher for testing."""
    return Mock(spec=AuditPublisher)


class TestEmbeddedDispatch:
    """Test the embedded asyncio dispatch backend."""

    @pytest.mark.asyncio
    async def test_dispatch_creates_task(self, mock_settings, mock_state, mock_storage, mock_attestation, mock_audit):
        """Test that dispatch creates an asyncio task."""
        dispatcher = EmbeddedDispatch(
            settings=mock_settings,
            state=mock_state,
            storage=mock_storage,
            attestation=mock_attestation,
            audit=mock_audit,
        )

        with patch("crosscodex_ingestion.dispatch.asyncio.create_task") as mock_create_task:
            mock_task = Mock()
            mock_create_task.return_value = mock_task

            await dispatcher.dispatch(
                document_id="doc-123",
                tenant_id="tenant-1",
                content=b"test content",
                source_uri=None,
                metadata={"foo": "bar"},
            )

            # Verify task was created
            mock_create_task.assert_called_once()
            # Verify task is tracked
            assert mock_task in dispatcher._tasks

    @pytest.mark.asyncio
    async def test_dispatch_calls_run_conversion(
        self, mock_settings, mock_state, mock_storage, mock_attestation, mock_audit
    ):
        """Test that dispatched task calls run_conversion with correct arguments."""
        dispatcher = EmbeddedDispatch(
            settings=mock_settings,
            state=mock_state,
            storage=mock_storage,
            attestation=mock_attestation,
            audit=mock_audit,
        )

        with patch("crosscodex_ingestion.dispatch.run_conversion", new_callable=AsyncMock) as mock_run:
            await dispatcher.dispatch(
                document_id="doc-123",
                tenant_id="tenant-1",
                content=b"test content",
                source_uri="https://example.com/doc.pdf",
                metadata={"mime_type": "application/pdf"},
            )

            # Give the task time to start
            await asyncio.sleep(0.01)

            # Verify run_conversion was called
            mock_run.assert_called_once_with(
                document_id="doc-123",
                tenant_id="tenant-1",
                content=b"test content",
                source_uri="https://example.com/doc.pdf",
                metadata={"mime_type": "application/pdf"},
                settings=mock_settings,
                state=mock_state,
                storage=mock_storage,
                attestation=mock_attestation,
                audit=mock_audit,
            )

    @pytest.mark.asyncio
    async def test_semaphore_limits_concurrency(
        self, mock_settings, mock_state, mock_storage, mock_attestation, mock_audit
    ):
        """Test that semaphore limits concurrent conversions."""
        # Set max concurrent to 2
        mock_settings.max_concurrent_conversions = 2

        dispatcher = EmbeddedDispatch(
            settings=mock_settings,
            state=mock_state,
            storage=mock_storage,
            attestation=mock_attestation,
            audit=mock_audit,
        )

        # Create a flag to track how many tasks are running concurrently
        running_count = 0
        max_concurrent = 0

        async def mock_conversion(*args, **kwargs):
            nonlocal running_count, max_concurrent
            running_count += 1
            max_concurrent = max(max_concurrent, running_count)
            # Simulate work
            await asyncio.sleep(0.05)
            running_count -= 1

        with patch("crosscodex_ingestion.dispatch.run_conversion", side_effect=mock_conversion):
            # Dispatch 5 tasks
            for i in range(5):
                await dispatcher.dispatch(
                    document_id=f"doc-{i}",
                    tenant_id="tenant-1",
                    content=b"test",
                    source_uri=None,
                    metadata={},
                )

            # Wait for all tasks to complete
            await dispatcher.shutdown()

        # Verify that max concurrent was limited to 2
        assert max_concurrent == 2

    @pytest.mark.asyncio
    async def test_shutdown_waits_for_tasks(
        self, mock_settings, mock_state, mock_storage, mock_attestation, mock_audit
    ):
        """Test that shutdown waits for all in-flight tasks."""
        dispatcher = EmbeddedDispatch(
            settings=mock_settings,
            state=mock_state,
            storage=mock_storage,
            attestation=mock_attestation,
            audit=mock_audit,
        )

        task_started = False
        task_completed = False

        async def mock_conversion(*args, **kwargs):
            nonlocal task_started, task_completed
            task_started = True
            await asyncio.sleep(0.05)
            task_completed = True

        with patch("crosscodex_ingestion.dispatch.run_conversion", side_effect=mock_conversion):
            # Dispatch a task
            await dispatcher.dispatch(
                document_id="doc-123",
                tenant_id="tenant-1",
                content=b"test",
                source_uri=None,
                metadata={},
            )

            # Verify task started
            await asyncio.sleep(0.01)
            assert task_started

            # Shutdown should wait for task completion
            await dispatcher.shutdown()
            assert task_completed

    @pytest.mark.asyncio
    async def test_task_cleanup_on_completion(
        self, mock_settings, mock_state, mock_storage, mock_attestation, mock_audit
    ):
        """Test that completed tasks are removed from tracking."""
        dispatcher = EmbeddedDispatch(
            settings=mock_settings,
            state=mock_state,
            storage=mock_storage,
            attestation=mock_attestation,
            audit=mock_audit,
        )

        with patch("crosscodex_ingestion.dispatch.run_conversion", new_callable=AsyncMock):
            await dispatcher.dispatch(
                document_id="doc-123",
                tenant_id="tenant-1",
                content=b"test",
                source_uri=None,
                metadata={},
            )

            # Wait for task to complete
            await asyncio.sleep(0.01)

            # Tasks should be empty after completion
            assert len(dispatcher._tasks) == 0


class TestNatsDispatch:
    """Test the NATS JetStream dispatch backend."""

    @pytest.mark.asyncio
    async def test_dispatch_publishes_to_nats(self, mock_settings):
        """Test that dispatch publishes serialized job to NATS."""
        mock_nc = AsyncMock()
        mock_js = AsyncMock()
        # jetstream() is synchronous, not async
        mock_nc.jetstream = Mock(return_value=mock_js)

        with patch("crosscodex_ingestion.dispatch.nats.connect", return_value=mock_nc):
            dispatcher = NatsDispatch(settings=mock_settings)
            await dispatcher._connect()

            # Dispatch a job
            await dispatcher.dispatch(
                document_id="doc-123",
                tenant_id="tenant-1",
                content=b"test content",
                source_uri="https://example.com/doc.pdf",
                metadata={"mime_type": "application/pdf"},
            )

            # Verify publish was called
            mock_js.publish.assert_called_once()

            # Verify subject and payload (using keyword arguments)
            call_kwargs = mock_js.publish.call_args.kwargs
            assert call_kwargs["subject"] == "ingestion.jobs"

            # Verify payload structure
            payload = json.loads(call_kwargs["payload"])
            assert payload["document_id"] == "doc-123"
            assert payload["tenant_id"] == "tenant-1"
            assert payload["content"] == base64.b64encode(b"test content").decode("utf-8")
            assert payload["source_uri"] == "https://example.com/doc.pdf"
            assert payload["metadata"] == {"mime_type": "application/pdf"}

    @pytest.mark.asyncio
    async def test_dispatch_with_null_content(self, mock_settings):
        """Test that dispatch handles None content correctly."""
        mock_nc = AsyncMock()
        mock_js = AsyncMock()
        # jetstream() is synchronous, not async
        mock_nc.jetstream = Mock(return_value=mock_js)

        with patch("crosscodex_ingestion.dispatch.nats.connect", return_value=mock_nc):
            dispatcher = NatsDispatch(settings=mock_settings)
            await dispatcher._connect()

            # Dispatch with None content
            await dispatcher.dispatch(
                document_id="doc-123",
                tenant_id="tenant-1",
                content=None,
                source_uri="https://example.com/doc.pdf",
                metadata={},
            )

            # Verify payload
            call_args = mock_js.publish.call_args
            payload = json.loads(call_args[1]["payload"])
            assert payload["content"] is None

    @pytest.mark.asyncio
    async def test_shutdown_closes_nats(self, mock_settings):
        """Test that shutdown closes the NATS connection."""
        mock_nc = AsyncMock()
        mock_js = AsyncMock()
        # jetstream() is synchronous, not async
        mock_nc.jetstream = Mock(return_value=mock_js)

        with patch("crosscodex_ingestion.dispatch.nats.connect", return_value=mock_nc):
            dispatcher = NatsDispatch(settings=mock_settings)
            await dispatcher._connect()

            # Shutdown
            await dispatcher.shutdown()

            # Verify drain and close were called
            mock_nc.drain.assert_called_once()
            mock_nc.close.assert_called_once()


class TestCreateDispatch:
    """Test the dispatch factory function."""

    def test_create_embedded_dispatch(self, mock_state, mock_storage, mock_attestation, mock_audit):
        """Test that create_dispatch returns EmbeddedDispatch for embedded mode."""
        settings = Mock(spec=IngestionSettings)
        settings.execution_mode = "embedded"
        settings.max_concurrent_conversions = 4

        dispatcher = create_dispatch(
            settings=settings,
            state=mock_state,
            storage=mock_storage,
            attestation=mock_attestation,
            audit=mock_audit,
        )

        assert isinstance(dispatcher, EmbeddedDispatch)

    def test_create_nats_dispatch(self, mock_state, mock_storage, mock_attestation, mock_audit):
        """Test that create_dispatch returns NatsDispatch for distributed mode."""
        settings = Mock(spec=IngestionSettings)
        settings.execution_mode = "distributed"
        settings.nats_url = "nats://localhost:4222"

        dispatcher = create_dispatch(
            settings=settings,
            state=mock_state,
            storage=mock_storage,
            attestation=mock_attestation,
            audit=mock_audit,
        )

        assert isinstance(dispatcher, NatsDispatch)

    def test_create_dispatch_invalid_mode(self, mock_state, mock_storage, mock_attestation, mock_audit):
        """Test that create_dispatch raises error for invalid execution mode."""
        settings = Mock(spec=IngestionSettings)
        settings.execution_mode = "invalid"

        with pytest.raises(ValueError, match="Unknown execution_mode"):
            create_dispatch(
                settings=settings,
                state=mock_state,
                storage=mock_storage,
                attestation=mock_attestation,
                audit=mock_audit,
            )
