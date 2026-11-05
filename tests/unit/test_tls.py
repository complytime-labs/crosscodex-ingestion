"""Mutual TLS on the gRPC port: credentials, client authentication and the client allowlist."""

import datetime as dt
from dataclasses import dataclass
from pathlib import Path

import grpc
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from crosscodex_ingestion.config import IngestionSettings
from crosscodex_ingestion.proto.crosscodex.v1 import ingestion_pb2, ingestion_pb2_grpc
from crosscodex_ingestion.server import create_grpc_server
from crosscodex_ingestion.tls import build_server_credentials


@dataclass(frozen=True)
class Pem:
    cert: bytes
    key: bytes


def _issue(
    name: str,
    issuer: tuple[x509.Name, ec.EllipticCurvePrivateKey] | None,
    *,
    ca: bool = False,
    san: str | None = None,
    eku: ExtendedKeyUsageOID | None = None,
) -> tuple[Pem, x509.Name, ec.EllipticCurvePrivateKey]:
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    issuer_name, issuer_key = issuer or (subject, key)
    now = dt.datetime.now(dt.UTC)
    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer_name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(hours=1))
        .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
    )
    if san:
        builder = builder.add_extension(x509.SubjectAlternativeName([x509.DNSName(san)]), critical=False)
    if eku:
        builder = builder.add_extension(x509.ExtendedKeyUsage([eku]), critical=False)
    cert = builder.sign(issuer_key, hashes.SHA256())
    pem = Pem(
        cert.public_bytes(serialization.Encoding.PEM),
        key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()),
    )
    return pem, subject, key


@dataclass(frozen=True)
class Pki:
    directory: Path
    ca: Pem
    server: Pem
    client: Pem  # CN=gateway, SAN=gateway.internal, signed by ca
    other_client: Pem  # CN=intruder, signed by ca
    rogue_client: Pem  # CN=gateway, signed by an unrelated CA
    server_paths: dict[str, str]


@pytest.fixture(scope="module")
def pki(tmp_path_factory) -> Pki:
    """Throwaway CA, server cert (SAN localhost) and client certs; nothing here is a real credential."""
    directory = tmp_path_factory.mktemp("pki")
    ca, ca_name, ca_key = _issue("test-ca", None, ca=True)
    server, _, _ = _issue("localhost", (ca_name, ca_key), san="localhost", eku=ExtendedKeyUsageOID.SERVER_AUTH)
    client, _, _ = _issue("gateway", (ca_name, ca_key), san="gateway.internal", eku=ExtendedKeyUsageOID.CLIENT_AUTH)
    other, _, _ = _issue("intruder", (ca_name, ca_key), eku=ExtendedKeyUsageOID.CLIENT_AUTH)
    _, rogue_ca_name, rogue_ca_key = _issue("rogue-ca", None, ca=True)
    rogue, _, _ = _issue("gateway", (rogue_ca_name, rogue_ca_key), eku=ExtendedKeyUsageOID.CLIENT_AUTH)
    (directory / "ca.pem").write_bytes(ca.cert)
    (directory / "server.pem").write_bytes(server.cert)
    (directory / "server.key").write_bytes(server.key)
    server_paths = {
        "grpc_tls_cert": str(directory / "server.pem"),
        "grpc_tls_key": str(directory / "server.key"),
        "grpc_tls_ca": str(directory / "ca.pem"),
    }
    return Pki(directory, ca, server, client, other, rogue, server_paths)


class _Servicer(ingestion_pb2_grpc.IngestionServiceServicer):
    async def GetDocument(self, request, context):
        return ingestion_pb2.GetDocumentResponse()


async def _serve(settings: IngestionSettings):
    server, port = create_grpc_server(settings, "127.0.0.1:0")
    ingestion_pb2_grpc.add_IngestionServiceServicer_to_server(_Servicer(), server)
    await server.start()
    return server, port


async def _call(port: int, pki: Pki, client: Pem | None, ca: bytes | None = None) -> None:
    credentials = grpc.ssl_channel_credentials(
        root_certificates=ca or pki.ca.cert,
        private_key=client.key if client else None,
        certificate_chain=client.cert if client else None,
    )
    async with grpc.aio.secure_channel(f"localhost:{port}", credentials) as channel:
        await ingestion_pb2_grpc.IngestionServiceStub(channel).GetDocument(
            ingestion_pb2.GetDocumentRequest(), timeout=5
        )


@pytest.fixture
async def mutual_server(pki):
    """Server with mutual TLS and no allowlist; yields its port."""
    server, port = await _serve(IngestionSettings(grpc_tls_mode="mutual", **pki.server_paths))
    yield port
    await server.stop(grace=0)


async def test_mutual_accepts_client_signed_by_ca(mutual_server, pki):
    await _call(mutual_server, pki, pki.client)


async def test_mutual_rejects_missing_client_certificate(mutual_server, pki):
    with pytest.raises(grpc.aio.AioRpcError) as err:
        await _call(mutual_server, pki, None)
    assert err.value.code() == grpc.StatusCode.UNAVAILABLE


async def test_mutual_rejects_client_certificate_from_another_ca(mutual_server, pki):
    with pytest.raises(grpc.aio.AioRpcError) as err:
        await _call(mutual_server, pki, pki.rogue_client)
    assert err.value.code() == grpc.StatusCode.UNAVAILABLE


async def test_plaintext_client_cannot_reach_mutual_server(mutual_server):
    async with grpc.aio.insecure_channel(f"localhost:{mutual_server}") as channel:
        with pytest.raises(grpc.aio.AioRpcError) as err:
            await ingestion_pb2_grpc.IngestionServiceStub(channel).GetDocument(
                ingestion_pb2.GetDocumentRequest(), timeout=5
            )
    assert err.value.code() == grpc.StatusCode.UNAVAILABLE


@pytest.mark.parametrize("allowed", ["gateway", "gateway.internal", "other, gateway.internal "])
async def test_allowlist_accepts_listed_cn_or_dns_san(pki, allowed):
    settings = IngestionSettings(grpc_tls_mode="mutual", grpc_allowed_clients=allowed, **pki.server_paths)
    server, port = await _serve(settings)
    try:
        await _call(port, pki, pki.client)
    finally:
        await server.stop(grace=0)


async def test_allowlist_rejects_unlisted_client_without_detail(pki):
    settings = IngestionSettings(grpc_tls_mode="mutual", grpc_allowed_clients="gateway", **pki.server_paths)
    server, port = await _serve(settings)
    try:
        with pytest.raises(grpc.aio.AioRpcError) as err:
            await _call(port, pki, pki.other_client)
    finally:
        await server.stop(grace=0)
    assert err.value.code() == grpc.StatusCode.PERMISSION_DENIED
    assert err.value.details() == "permission denied"
    assert "intruder" not in (err.value.debug_error_string() or "")


async def test_allowlist_does_not_admit_partial_identity_matches(pki):
    settings = IngestionSettings(grpc_tls_mode="mutual", grpc_allowed_clients="gate", **pki.server_paths)
    server, port = await _serve(settings)
    try:
        with pytest.raises(grpc.aio.AioRpcError) as err:
            await _call(port, pki, pki.client)
    finally:
        await server.stop(grace=0)
    assert err.value.code() == grpc.StatusCode.PERMISSION_DENIED


async def test_tls_off_serves_plaintext(caplog):
    with caplog.at_level("WARNING"):
        server, port = await _serve(IngestionSettings(grpc_tls_mode="off"))
    try:
        async with grpc.aio.insecure_channel(f"localhost:{port}") as channel:
            await ingestion_pb2_grpc.IngestionServiceStub(channel).GetDocument(
                ingestion_pb2.GetDocumentRequest(), timeout=5
            )
    finally:
        await server.stop(grace=0)
    assert "GRPC_TLS_MODE=off" in caplog.text


def test_default_mode_is_mutual():
    assert IngestionSettings().grpc_tls_mode == "mutual"


@pytest.mark.parametrize("missing", ["grpc_tls_cert", "grpc_tls_key", "grpc_tls_ca"])
def test_mutual_without_a_file_setting_refuses_to_start(pki, missing):
    paths = {**pki.server_paths, missing: ""}
    with pytest.raises(ValueError, match=missing.upper()):
        build_server_credentials(IngestionSettings(grpc_tls_mode="mutual", **paths))


def test_mutual_with_unreadable_file_refuses_to_start(pki):
    paths = {**pki.server_paths, "grpc_tls_ca": str(pki.directory / "absent.pem")}
    with pytest.raises(ValueError, match="GRPC_TLS_CA"):
        build_server_credentials(IngestionSettings(grpc_tls_mode="mutual", **paths))


def test_allowlist_with_tls_off_refuses_to_start():
    with pytest.raises(ValueError, match="GRPC_ALLOWED_CLIENTS"):
        build_server_credentials(IngestionSettings(grpc_tls_mode="off", grpc_allowed_clients="gateway"))
