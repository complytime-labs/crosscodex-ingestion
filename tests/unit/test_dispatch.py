"""Unit tests for dispatch module."""

import asyncio
import base64
import gc
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
from crosscodex_ingestion.html_profile import load_html_profiles
from crosscodex_ingestion.mapping import MappingRegistry
from crosscodex_ingestion.nats_connection import NatsConnection, NatsUnavailableError
from crosscodex_ingestion.state import StateBackend
from crosscodex_ingestion.storage import StorageBackend

REGISTRY = MappingRegistry({})
PROFILES = load_html_profiles("")


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
            mappings=REGISTRY,
            html_profiles=PROFILES,
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
            mappings=REGISTRY,
            html_profiles=PROFILES,
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
                mappings=REGISTRY,
                html_profiles=PROFILES,
            )

    @pytest.mark.asyncio
    async def test_a_failed_conversion_task_is_logged_not_left_unretrieved(
        self, mock_settings, mock_state, mock_storage, mock_attestation, mock_audit, caplog
    ):
        """An exception escaping run_conversion is retrieved and logged with the document ID."""
        dispatcher = EmbeddedDispatch(
            settings=mock_settings,
            state=mock_state,
            storage=mock_storage,
            attestation=mock_attestation,
            audit=mock_audit,
            mappings=REGISTRY,
            html_profiles=PROFILES,
        )
        loop = asyncio.get_running_loop()
        unretrieved: list[dict] = []
        loop.set_exception_handler(lambda _loop, ctx: unretrieved.append(ctx))

        with (
            patch("crosscodex_ingestion.dispatch.run_conversion", side_effect=RuntimeError("state store down")),
            caplog.at_level("ERROR"),
        ):
            await dispatcher.dispatch(
                document_id="doc-err", tenant_id="tenant-1", content=b"x", source_uri=None, metadata={}
            )
            (task,) = dispatcher._tasks
            await asyncio.wait([task])
            await asyncio.sleep(0)  # done callbacks run on the next loop turn

        errors = [r for r in caplog.records if "doc-err" in r.getMessage()]
        assert len(errors) == 1
        assert errors[0].levelname == "ERROR"
        assert "state store down" in str(errors[0].exc_info[1])
        assert dispatcher._tasks == set()
        del task
        gc.collect()
        assert unretrieved == []

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
            mappings=REGISTRY,
            html_profiles=PROFILES,
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
            await asyncio.gather(*dispatcher._tasks)

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
            mappings=REGISTRY,
            html_profiles=PROFILES,
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
            await dispatcher.shutdown(grace_seconds=5)
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
            mappings=REGISTRY,
            html_profiles=PROFILES,
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


def _nats_connection(available: bool = True) -> tuple[Mock, AsyncMock]:
    """A shared NatsConnection whose client hands out the returned JetStream context."""
    mock_js = AsyncMock()
    connection = Mock(spec=NatsConnection)
    connection.client = AsyncMock()
    # jetstream() is synchronous, not async
    connection.client.jetstream = Mock(return_value=mock_js)
    connection.available = available
    connection.url = "nats://localhost:4222"
    return connection, mock_js


class TestNatsDispatch:
    """Test the NATS JetStream dispatch backend."""

    @pytest.mark.asyncio
    async def test_dispatch_publishes_to_nats(self):
        """Test that dispatch publishes serialized job to NATS."""
        connection, mock_js = _nats_connection()
        dispatcher = NatsDispatch(connection)

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
    async def test_job_carries_the_mapping_id_and_hash(self):
        """The consuming worker re-checks the hash against its own mappings (worker.MappingSkewError)."""
        connection, mock_js = _nats_connection()
        metadata = {"mapping_id": "owasp-asvs-5.0.0-json", "mapping_sha256": "a" * 64}

        dispatcher = NatsDispatch(connection)
        await dispatcher.dispatch(
            document_id="doc-123", tenant_id="tenant-1", content=b"{}", source_uri=None, metadata=metadata
        )

        payload = json.loads(mock_js.publish.call_args.kwargs["payload"])
        assert payload["metadata"] == metadata

    @pytest.mark.asyncio
    async def test_dispatch_with_null_content(self):
        """Test that dispatch handles None content correctly."""
        connection, mock_js = _nats_connection()
        dispatcher = NatsDispatch(connection)

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
    async def test_dispatch_while_nats_is_unavailable_raises(self):
        """The servicer rejects submissions first; this covers NATS dropping between that check and here."""
        connection, mock_js = _nats_connection(available=False)
        dispatcher = NatsDispatch(connection)

        with pytest.raises(NatsUnavailableError, match=r"NATS at nats://localhost:4222 is unavailable.*doc-123"):
            await dispatcher.dispatch(
                document_id="doc-123", tenant_id="tenant-1", content=b"x", source_uri=None, metadata={}
            )

        mock_js.publish.assert_not_called()

    @pytest.mark.asyncio
    async def test_shutdown_leaves_the_shared_connection_open(self):
        """The connection also carries audit events; server.serve() closes it after the dispatch shuts down."""
        connection, _ = _nats_connection()
        dispatcher = NatsDispatch(connection)

        await dispatcher.shutdown(grace_seconds=5)

        connection.client.drain.assert_not_called()
        connection.client.close.assert_not_called()
        connection.close.assert_not_called()


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
            mappings=REGISTRY,
            html_profiles=PROFILES,
        )

        assert isinstance(dispatcher, EmbeddedDispatch)

    def test_create_nats_dispatch(self, mock_state, mock_storage, mock_attestation, mock_audit):
        """Test that create_dispatch returns NatsDispatch for distributed mode."""
        settings = Mock(spec=IngestionSettings)
        settings.execution_mode = "distributed"
        settings.nats_url = "nats://localhost:4222"
        connection, _ = _nats_connection()

        dispatcher = create_dispatch(
            settings=settings,
            state=mock_state,
            storage=mock_storage,
            attestation=mock_attestation,
            audit=mock_audit,
            mappings=REGISTRY,
            html_profiles=PROFILES,
            nats=connection,
        )

        assert isinstance(dispatcher, NatsDispatch)

    def test_distributed_mode_without_nats_is_rejected(self, mock_state, mock_storage, mock_attestation, mock_audit):
        """Distributed mode publishes every job to NATS, so it cannot run without NATS_URL."""
        settings = Mock(spec=IngestionSettings)
        settings.execution_mode = "distributed"

        with pytest.raises(ValueError, match="NATS_URL"):
            create_dispatch(
                settings=settings,
                state=mock_state,
                storage=mock_storage,
                attestation=mock_attestation,
                audit=mock_audit,
                mappings=REGISTRY,
                html_profiles=PROFILES,
                nats=None,
            )

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
                mappings=REGISTRY,
                html_profiles=PROFILES,
            )
