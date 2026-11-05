import tempfile
from collections.abc import AsyncGenerator
from pathlib import Path

import devpki
import grpc
import pytest

from crosscodex_ingestion.attestation import create_attestation_manager
from crosscodex_ingestion.audit import AuditPublisher
from crosscodex_ingestion.config import IngestionSettings
from crosscodex_ingestion.dispatch import create_dispatch
from crosscodex_ingestion.proto.crosscodex.v1 import ingestion_pb2_grpc
from crosscodex_ingestion.server import create_grpc_server
from crosscodex_ingestion.service import IngestionServiceServicer
from crosscodex_ingestion.state import create_state_backend
from crosscodex_ingestion.storage import create_storage

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "documents"


@pytest.fixture
def sample_html() -> bytes:
    """Load sample HTML fixture."""
    return (FIXTURES_DIR / "sample.html").read_bytes()


@pytest.fixture
def sample_txt() -> bytes:
    """Load sample TXT fixture."""
    return (FIXTURES_DIR / "sample.txt").read_bytes()


@pytest.fixture
def sample_pdf() -> bytes:
    """Load sample PDF fixture."""
    return (FIXTURES_DIR / "sample.pdf").read_bytes()


@pytest.fixture
def sample_docx() -> bytes:
    """Load sample DOCX fixture."""
    return (FIXTURES_DIR / "sample.docx").read_bytes()


@pytest.fixture(scope="session")
def pki_dir(tmp_path_factory) -> Path:
    """Fake CA, server/client certs and attestation keys, private to this pytest run (see scripts/devpki.py)."""
    out = tmp_path_factory.mktemp("pki") / "certs"
    devpki.ensure(out)
    return out


@pytest.fixture
async def running_grpc_server(pki_dir: Path) -> AsyncGenerator[tuple[grpc.aio.Server, int], None]:
    """Start the real server (mutual TLS, attestation on, allowlist set) with filesystem backends.

    Built through create_grpc_server, so tests exercise the production credential and
    interceptor wiring. Uses the memory state backend, a temp storage directory and no audit
    publishing (noop).

    Yields:
        Tuple of (server, bound port).
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        settings = IngestionSettings(
            execution_mode="embedded",
            state_backend="memory",
            storage_backend="filesystem",
            storage_path=tmpdir,
            attestation_enabled=True,
            attestation_private_key_path=str(pki_dir / "attestation.key"),
            attestation_public_key_path=str(pki_dir / "attestation.pub"),
            max_concurrent_conversions=2,
            grpc_tls_mode="mutual",
            grpc_tls_cert=str(pki_dir / "server.pem"),
            grpc_tls_key=str(pki_dir / "server.key"),
            grpc_tls_ca=str(pki_dir / "ca.pem"),
            grpc_allowed_clients=devpki.CLIENT_NAME,
            rlimit_as_mb=0,  # Unlimited for integration tests
            rlimit_cpu=0,
            rlimit_fsize_mb=0,
        )

        state_backend = create_state_backend(settings)
        storage_backend = create_storage(settings)
        attestation_manager = create_attestation_manager(settings)

        audit_publisher = AuditPublisher(settings)
        await audit_publisher.connect()

        dispatch_backend = create_dispatch(
            settings=settings,
            state=state_backend,
            storage=storage_backend,
            attestation=attestation_manager,
            audit=audit_publisher,
        )

        servicer = IngestionServiceServicer(
            settings=settings,
            state_backend=state_backend,
            dispatch_backend=dispatch_backend,
        )

        server, port = create_grpc_server(settings, "[::]:0")  # port 0: ephemeral
        ingestion_pb2_grpc.add_IngestionServiceServicer_to_server(servicer, server)
        await server.start()

        try:
            yield server, port
        finally:
            await dispatch_backend.shutdown(grace_seconds=5)
            await audit_publisher.close()
            await server.stop(grace=1.0)


@pytest.fixture
async def grpc_server_and_channel(
    running_grpc_server: tuple[grpc.aio.Server, int], pki_dir: Path
) -> AsyncGenerator[tuple[grpc.aio.Server, grpc.aio.Channel], None]:
    """Running server plus a channel authenticated with the allowlisted client certificate."""
    server, port = running_grpc_server
    credentials = grpc.ssl_channel_credentials(
        root_certificates=(pki_dir / "ca.pem").read_bytes(),
        private_key=(pki_dir / "client.key").read_bytes(),
        certificate_chain=(pki_dir / "client.pem").read_bytes(),
    )
    channel = grpc.aio.secure_channel(f"localhost:{port}", credentials)
    try:
        yield server, channel
    finally:
        await channel.close()
