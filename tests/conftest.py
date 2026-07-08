import os
import tempfile
from collections.abc import AsyncGenerator

import grpc
import pytest
from crosscodex_ingestion.proto.crosscodex.v1 import ingestion_pb2_grpc

from crosscodex_ingestion.attestation import create_attestation_manager
from crosscodex_ingestion.audit import AuditPublisher
from crosscodex_ingestion.config import IngestionSettings
from crosscodex_ingestion.dispatch import create_dispatch
from crosscodex_ingestion.service import IngestionServiceServicer
from crosscodex_ingestion.state import create_state_backend
from crosscodex_ingestion.storage import create_storage

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures", "documents")


@pytest.fixture
def sample_html() -> bytes:
    """Load sample HTML fixture."""
    with open(os.path.join(FIXTURES_DIR, "sample.html"), "rb") as f:
        return f.read()


@pytest.fixture
def sample_txt() -> bytes:
    """Load sample TXT fixture."""
    with open(os.path.join(FIXTURES_DIR, "sample.txt"), "rb") as f:
        return f.read()


@pytest.fixture
def sample_pdf() -> bytes:
    """Load sample PDF fixture."""
    with open(os.path.join(FIXTURES_DIR, "sample.pdf"), "rb") as f:
        return f.read()


@pytest.fixture
def sample_docx() -> bytes:
    """Load sample DOCX fixture."""
    with open(os.path.join(FIXTURES_DIR, "sample.docx"), "rb") as f:
        return f.read()


@pytest.fixture
async def grpc_server_and_channel() -> AsyncGenerator[tuple[grpc.aio.Server, grpc.aio.Channel], None]:
    """Start an in-process gRPC server with filesystem backends and return server + channel.

    This fixture creates a real gRPC server in embedded mode with:
    - Memory state backend
    - Filesystem storage backend (in a temp directory)
    - No attestation
    - No audit publishing (noop)

    Yields:
        Tuple of (server, channel) for integration tests.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create settings for embedded mode with filesystem storage
        settings = IngestionSettings(
            execution_mode="embedded",
            state_backend="memory",
            storage_backend="filesystem",
            storage_path=tmpdir,
            attestation_enabled=False,
            max_concurrent_conversions=2,
            grpc_port=0,  # Will bind to ephemeral port
            rlimit_as_mb=0,  # Unlimited for integration tests
            rlimit_cpu=0,
            rlimit_fsize_mb=0,
        )

        # Create backends
        state_backend = create_state_backend(settings)
        storage_backend = create_storage(settings)
        attestation_manager = create_attestation_manager(settings)

        # Create audit publisher (noop when disabled)
        audit_publisher = AuditPublisher(settings)
        await audit_publisher.connect()

        # Create dispatch backend
        dispatch_backend = create_dispatch(
            settings=settings,
            state=state_backend,
            storage=storage_backend,
            attestation=attestation_manager,
            audit=audit_publisher,
        )

        # Create servicer
        servicer = IngestionServiceServicer(
            settings=settings,
            state_backend=state_backend,
            dispatch_backend=dispatch_backend,
        )

        # Create gRPC server
        server = grpc.aio.server()
        ingestion_pb2_grpc.add_IngestionServiceServicer_to_server(servicer, server)

        # Bind to ephemeral port
        port = server.add_insecure_port("[::]:0")
        await server.start()

        # Create client channel
        channel = grpc.aio.insecure_channel(f"localhost:{port}")

        try:
            yield server, channel
        finally:
            # Cleanup
            await channel.close()
            await dispatch_backend.shutdown()
            await audit_publisher.close()
            await server.stop(grace=1.0)
