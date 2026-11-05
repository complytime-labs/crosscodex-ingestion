"""Integration tests: the real server construction path with mutual TLS and attestation enabled."""

import datetime
import subprocess
import sys
from pathlib import Path

import devpki
import grpc
import pytest

from crosscodex_ingestion.proto.crosscodex.v1 import common_pb2, ingestion_pb2, ingestion_pb2_grpc

pytestmark = pytest.mark.integration

_LIST = ingestion_pb2.ListDocumentsRequest(tenant_context=common_pb2.TenantContext(tenant_id="mtls-check"))


async def _list_documents(port: int, pki_dir: Path, *, ca: str = "ca.pem", client: str | None = "client") -> None:
    credentials = grpc.ssl_channel_credentials(
        root_certificates=(pki_dir / ca).read_bytes(),
        private_key=(pki_dir / f"{client}.key").read_bytes() if client else None,
        certificate_chain=(pki_dir / f"{client}.pem").read_bytes() if client else None,
    )
    async with grpc.aio.secure_channel(f"localhost:{port}", credentials) as channel:
        await ingestion_pb2_grpc.IngestionServiceStub(channel).ListDocuments(_LIST, timeout=10)


async def test_allowlisted_client_is_accepted(running_grpc_server, pki_dir):
    _, port = running_grpc_server
    await _list_documents(port, pki_dir)


async def test_client_without_certificate_is_rejected(running_grpc_server, pki_dir):
    _, port = running_grpc_server
    with pytest.raises(grpc.aio.AioRpcError) as err:
        await _list_documents(port, pki_dir, client=None)
    assert err.value.code() == grpc.StatusCode.UNAVAILABLE


async def test_client_from_another_ca_is_rejected(running_grpc_server, pki_dir, tmp_path):
    """A second, independent generation is a different CA: its client cert must not be trusted."""
    other = tmp_path / "other-certs"
    devpki.ensure(other)
    _, port = running_grpc_server
    credentials = grpc.ssl_channel_credentials(
        root_certificates=(pki_dir / "ca.pem").read_bytes(),
        private_key=(other / "client.key").read_bytes(),
        certificate_chain=(other / "client.pem").read_bytes(),
    )
    async with grpc.aio.secure_channel(f"localhost:{port}", credentials) as channel:
        with pytest.raises(grpc.aio.AioRpcError) as err:
            await ingestion_pb2_grpc.IngestionServiceStub(channel).ListDocuments(_LIST, timeout=10)
    assert err.value.code() == grpc.StatusCode.UNAVAILABLE


async def test_server_not_signed_by_trusted_ca_is_refused_by_client(running_grpc_server, pki_dir, tmp_path):
    other = tmp_path / "other-certs"
    devpki.ensure(other)
    _, port = running_grpc_server
    with pytest.raises(grpc.aio.AioRpcError) as err:
        await _list_documents(port, other, client="client")  # trusts the other CA for the server
    assert err.value.code() == grpc.StatusCode.UNAVAILABLE


async def test_plaintext_client_is_rejected(running_grpc_server):
    _, port = running_grpc_server
    async with grpc.aio.insecure_channel(f"localhost:{port}") as channel:
        with pytest.raises(grpc.aio.AioRpcError) as err:
            await ingestion_pb2_grpc.IngestionServiceStub(channel).ListDocuments(_LIST, timeout=10)
    assert err.value.code() == grpc.StatusCode.UNAVAILABLE


def test_regeneration_after_expiry_mints_a_new_ca(tmp_path):
    """Stale CAs across runs: an expiring set is replaced wholesale, so the old CA stops being trusted."""
    out = tmp_path / "certs"
    devpki.ensure(out, validity=datetime.timedelta(hours=1))
    old_ca = (out / "ca.pem").read_bytes()
    result = subprocess.run([sys.executable, devpki.__file__, "--out", str(out)], capture_output=True, text=True)
    assert result.returncode == 0 and "generated" in result.stdout
    assert (out / "ca.pem").read_bytes() != old_ca
    assert devpki.verify(out) == []
