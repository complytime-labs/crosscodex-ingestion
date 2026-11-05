"""Dev/test PKI generator: idempotence, race safety and stale-set repair."""

import datetime
import subprocess
import sys
from pathlib import Path

import devpki
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

SCRIPT = Path(devpki.__file__)


def _run(out: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(SCRIPT), "--out", str(out), *args], capture_output=True, text=True)


@pytest.fixture
def out(tmp_path) -> Path:
    return tmp_path / "certs"


def test_ensure_generates_a_verified_set(out):
    assert devpki.ensure(out) is True
    assert devpki.verify(out) == []
    assert (out / "server.key").stat().st_mode & 0o777 == 0o600
    assert (out / "attestation.key").stat().st_mode & 0o777 == 0o600


def test_valid_set_is_left_untouched(out):
    devpki.ensure(out)
    before = {p.name: p.read_bytes() for p in out.iterdir()}
    assert devpki.ensure(out) is False
    assert {p.name: p.read_bytes() for p in out.iterdir()} == before


def test_concurrent_processes_yield_one_consistent_set(out):
    procs = [
        subprocess.Popen(
            [sys.executable, str(SCRIPT), "--out", str(out)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        for _ in range(8)
    ]
    results = [(p.wait(timeout=60), p.stdout.read()) for p in procs]  # type: ignore[union-attr]
    assert all(code == 0 for code, _ in results), results
    assert sum("generated" in stdout for _, stdout in results) == 1
    assert devpki.verify(out) == []
    assert sorted(p.name for p in out.parent.iterdir()) == ["certs", "certs.lock"]


def test_verify_cli_exit_codes(out):
    assert _run(out, "--verify").returncode != 0  # absent
    assert _run(out).returncode == 0
    assert _run(out, "--verify").returncode == 0


def _ca_bytes(out: Path) -> bytes:
    return (out / "ca.pem").read_bytes()


def _assert_repaired(out: Path, expected_problem: str) -> None:
    assert any(expected_problem in p for p in devpki.verify(out)), devpki.verify(out)
    old_ca = _ca_bytes(out) if (out / "ca.pem").exists() else b""
    assert devpki.ensure(out) is True
    assert devpki.verify(out) == []
    assert _ca_bytes(out) != old_ca  # whole set regenerated, never patched


def test_key_mismatch_is_repaired(out):
    devpki.ensure(out)
    other = ec.generate_private_key(ec.SECP256R1())
    (out / "server.key").write_bytes(
        other.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    )
    (out / "server.key").chmod(0o600)
    _assert_repaired(out, "server.key does not match server.pem")


def test_attestation_pair_mismatch_is_repaired(out):
    devpki.ensure(out)
    other = ec.generate_private_key(ec.SECP256R1())
    (out / "attestation.pub").write_bytes(
        other.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    )
    _assert_repaired(out, "attestation")


def test_missing_san_is_repaired(out):
    devpki.ensure(out)
    ca = x509.load_pem_x509_certificate((out / "ca.pem").read_bytes())
    ca_key = serialization.load_pem_private_key((out / "ca.key").read_bytes(), password=None)
    server = x509.load_pem_x509_certificate((out / "server.pem").read_bytes())
    now = datetime.datetime.now(datetime.UTC)
    builder = (
        x509.CertificateBuilder()
        .subject_name(server.subject)
        .issuer_name(ca.subject)
        .public_key(server.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=30))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
        .add_extension(x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
    )
    (out / "server.pem").write_bytes(builder.sign(ca_key, hashes.SHA256()).public_bytes(serialization.Encoding.PEM))  # type: ignore[arg-type]
    _assert_repaired(out, "SAN")


def test_near_expiry_is_repaired(out):
    devpki.ensure(out, validity=datetime.timedelta(hours=2))
    _assert_repaired(out, "expires")


def test_wrong_ca_is_repaired(out):
    devpki.ensure(out)
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "impostor")])
    now = datetime.datetime.now(datetime.UTC)
    fake = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(1)
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=30))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    (out / "ca.pem").write_bytes(fake.public_bytes(serialization.Encoding.PEM))
    _assert_repaired(out, "server.pem")


def test_leftover_temp_dir_is_cleaned(out):
    devpki.ensure(out)
    leftover = out.parent / ".certs.tmp-interrupted"
    leftover.mkdir()
    (leftover / "ca.key").write_text("partial")
    assert any("leftover" in p for p in devpki.verify(out))
    devpki.ensure(out)
    assert not leftover.exists()
    assert devpki.verify(out) == []


def test_half_written_set_is_repaired(out):
    devpki.ensure(out)
    (out / "client.pem").unlink()
    (out / "attestation.key").write_bytes(b"")
    _assert_repaired(out, "client.pem")


def test_set_missing_entirely_is_generated(out):
    assert any("missing" in p for p in devpki.verify(out))
    assert devpki.ensure(out) is True


def test_loose_key_permissions_are_repaired(out):
    devpki.ensure(out)
    (out / "ca.key").chmod(0o644)
    _assert_repaired(out, "permissions")
