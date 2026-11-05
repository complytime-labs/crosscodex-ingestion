#!/usr/bin/env python3
"""Generate and verify the throwaway PKI used by tests and docker-compose.

Writes a CA, a server certificate, a client certificate and an ECDSA P-256 attestation key
pair. NOT for production: the CA key sits next to the leaf keys.

    devpki.py [--out DIR] [--min-valid-hours N]   ensure a valid set exists (generate if not)
    devpki.py --verify [--out DIR]                exit 1 and list problems if the set is stale

Concurrency: ensure() holds an exclusive flock on <out>.lock (a sibling of the output
directory, so swapping the directory never replaces the lock). Under the lock it re-verifies,
so N racing callers produce one set. A new set is built in a temp directory beside the output
directory and swapped in with rename(2). Any defect regenerates the whole set with a new CA;
pieces are never patched, so a set is always internally consistent.
"""

import argparse
import datetime as dt
import fcntl
import ipaddress
import os
import shutil
import sys
import tempfile
from pathlib import Path

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

DEFAULT_OUT = Path(".test-output/certs")
DEFAULT_MIN_VALID = dt.timedelta(hours=24)
DEFAULT_VALIDITY = dt.timedelta(days=90)

SERVER_DNS_NAMES = ("localhost", "ingestion", "crosscodex-ingestion")  # compose service and container names
SERVER_IPS = ("127.0.0.1",)
CLIENT_NAME = "crosscodex-gateway"

KEY_FILES = ("ca.key", "server.key", "client.key", "attestation.key")
ALL_FILES = ("ca.pem", "server.pem", "client.pem", "attestation.pub", *KEY_FILES)


def _siblings(out: Path) -> list[Path]:
    """Temp and aside directories left by this output dir's generations."""
    return sorted(p for p in out.parent.glob(f".{out.name}.*") if p.is_dir())


def _pem_key(key: ec.EllipticCurvePrivateKey) -> bytes:
    return key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )


def _pem_pub(key: ec.EllipticCurvePublicKey) -> bytes:
    return key.public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)


def _spki(public_key: object) -> bytes:
    if not isinstance(public_key, ec.EllipticCurvePublicKey):
        raise TypeError("not an ECDSA public key")
    return public_key.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)


def _issue(
    cn: str,
    key: ec.EllipticCurvePrivateKey,
    issuer: tuple[x509.Name, ec.EllipticCurvePrivateKey] | None,
    validity: dt.timedelta,
    *,
    ca: bool = False,
    eku: x509.ObjectIdentifier | None = None,
    sans: list[x509.GeneralName] | None = None,
) -> x509.Certificate:
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    issuer_name, issuer_key = issuer or (subject, key)
    now = dt.datetime.now(dt.UTC)
    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer_name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + validity)
        .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
    )
    if eku:
        builder = builder.add_extension(x509.ExtendedKeyUsage([eku]), critical=False)
    if sans:
        builder = builder.add_extension(x509.SubjectAlternativeName(sans), critical=False)
    return builder.sign(issuer_key, hashes.SHA256())


def _write(path: Path, data: bytes, mode: int) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _generate_into(directory: Path, validity: dt.timedelta) -> None:
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca = _issue("crosscodex-test-ca", ca_key, None, validity, ca=True)
    ca_issuer = (ca.subject, ca_key)

    server_key = ec.generate_private_key(ec.SECP256R1())
    server_sans: list[x509.GeneralName] = [x509.DNSName(n) for n in SERVER_DNS_NAMES]
    server_sans += [x509.IPAddress(ipaddress.ip_address(ip)) for ip in SERVER_IPS]
    server = _issue(
        "crosscodex-ingestion", server_key, ca_issuer, validity, eku=ExtendedKeyUsageOID.SERVER_AUTH, sans=server_sans
    )

    client_key = ec.generate_private_key(ec.SECP256R1())
    client = _issue(
        CLIENT_NAME,
        client_key,
        ca_issuer,
        validity,
        eku=ExtendedKeyUsageOID.CLIENT_AUTH,
        sans=[x509.DNSName(CLIENT_NAME)],
    )

    attestation_key = ec.generate_private_key(ec.SECP256R1())

    pem = serialization.Encoding.PEM
    for name, data, mode in (
        ("ca.pem", ca.public_bytes(pem), 0o644),
        ("ca.key", _pem_key(ca_key), 0o600),
        ("server.pem", server.public_bytes(pem), 0o644),
        ("server.key", _pem_key(server_key), 0o600),
        ("client.pem", client.public_bytes(pem), 0o644),
        ("client.key", _pem_key(client_key), 0o600),
        ("attestation.key", _pem_key(attestation_key), 0o600),
        ("attestation.pub", _pem_pub(attestation_key.public_key()), 0o644),
    ):
        _write(directory / name, data, mode)
    _fsync_dir(directory)


def _load_cert(path: Path) -> x509.Certificate:
    return x509.load_pem_x509_certificate(path.read_bytes())


def _load_key(path: Path) -> ec.EllipticCurvePrivateKey:
    key = serialization.load_pem_private_key(path.read_bytes(), password=None)
    if not isinstance(key, ec.EllipticCurvePrivateKey):
        raise TypeError("not an ECDSA key")
    if not isinstance(key.curve, ec.SECP256R1):
        raise TypeError("not an ECDSA P-256 key")
    return key


def verify(out: Path, min_valid: dt.timedelta = DEFAULT_MIN_VALID) -> list[str]:
    """Return a list of problems; empty means the set is complete, consistent and fresh."""
    problems = [f"leftover temp directory {p.name}" for p in _siblings(out)]
    if not out.is_dir():
        return [*problems, f"{out} is missing"]
    missing = [f for f in ALL_FILES if not (out / f).is_file()]
    if missing:
        return [*problems, *(f"{f} is missing" for f in missing)]

    problems += [
        f"{f} has permissions {(out / f).stat().st_mode & 0o777:o}, want 600"
        for f in KEY_FILES
        if (out / f).stat().st_mode & 0o077
    ]

    try:
        ca, server, client = (_load_cert(out / f) for f in ("ca.pem", "server.pem", "client.pem"))
        keys = {f: _load_key(out / f) for f in KEY_FILES}
        attestation_pub = serialization.load_pem_public_key((out / "attestation.pub").read_bytes())
    except (OSError, ValueError, TypeError) as exc:
        return [*problems, f"unreadable or unparseable file: {exc}"]

    now = dt.datetime.now(dt.UTC)
    for name, cert in (("ca.pem", ca), ("server.pem", server), ("client.pem", client)):
        if cert.not_valid_before_utc > now:
            problems.append(f"{name} is not yet valid")
        if cert.not_valid_after_utc - now < min_valid:
            problems.append(f"{name} expires {cert.not_valid_after_utc:%Y-%m-%d %H:%M}Z (< {min_valid} left)")

    try:
        if not ca.extensions.get_extension_for_class(x509.BasicConstraints).value.ca:
            problems.append("ca.pem is not a CA")
    except x509.ExtensionNotFound:
        problems.append("ca.pem is not a CA")

    for name, cert, key_name, eku in (
        ("server.pem", server, "server.key", ExtendedKeyUsageOID.SERVER_AUTH),
        ("client.pem", client, "client.key", ExtendedKeyUsageOID.CLIENT_AUTH),
    ):
        try:
            cert.verify_directly_issued_by(ca)
        except (InvalidSignature, ValueError, TypeError):
            problems.append(f"{name} is not issued by ca.pem")
        try:
            if eku not in cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value:
                problems.append(f"{name} lacks extended key usage {eku.dotted_string}")
        except x509.ExtensionNotFound:
            problems.append(f"{name} lacks extended key usage {eku.dotted_string}")
        if _spki(cert.public_key()) != _spki(keys[key_name].public_key()):
            problems.append(f"{key_name} does not match {name}")
    if _spki(ca.public_key()) != _spki(keys["ca.key"].public_key()):
        problems.append("ca.key does not match ca.pem")

    try:
        sans = server.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        dns, ips = set(sans.get_values_for_type(x509.DNSName)), set(sans.get_values_for_type(x509.IPAddress))
    except x509.ExtensionNotFound:
        dns, ips = set(), set()
    for name in SERVER_DNS_NAMES:
        if name not in dns:
            problems.append(f"server.pem lacks SAN DNS:{name}")
    for ip in SERVER_IPS:
        if ipaddress.ip_address(ip) not in ips:
            problems.append(f"server.pem lacks SAN IP:{ip}")
    if client.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value != CLIENT_NAME:
        problems.append(f"client.pem CN is not {CLIENT_NAME}")

    if _spki(attestation_pub) != _spki(keys["attestation.key"].public_key()):
        problems.append("attestation.key and attestation.pub are not a pair")
    return problems


def ensure(
    out: Path,
    min_valid: dt.timedelta = DEFAULT_MIN_VALID,
    validity: dt.timedelta = DEFAULT_VALIDITY,
) -> bool:
    """Make sure a valid set exists at out. Returns True if a new set was generated."""
    out = out.absolute()
    out.parent.mkdir(parents=True, exist_ok=True)
    with (out.parent / f"{out.name}.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        # Holding the lock: nobody is mid-generation, so any temp/aside directory is stale.
        for leftover in _siblings(out):
            shutil.rmtree(leftover)
        if not verify(out, min_valid):
            return False

        tmp = Path(tempfile.mkdtemp(prefix=f".{out.name}.tmp-", dir=out.parent))
        try:
            _generate_into(tmp, validity)
            tmp.chmod(0o755)
            aside = out.parent / f".{out.name}.old-{os.getpid()}"
            if out.exists():
                out.rename(aside)
            tmp.rename(out)
            _fsync_dir(out.parent)
            if aside.exists():
                shutil.rmtree(aside)
        finally:
            if tmp.exists():
                shutil.rmtree(tmp)
        return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help=f"output directory (default {DEFAULT_OUT})")
    parser.add_argument("--min-valid-hours", type=float, default=24, help="regenerate if less validity remains")
    parser.add_argument("--verify", action="store_true", help="only check; exit 1 if the set is stale")
    args = parser.parse_args()
    min_valid = dt.timedelta(hours=args.min_valid_hours)

    if args.verify:
        problems = verify(args.out.absolute(), min_valid)
        for problem in problems:
            print(f"stale: {problem}", file=sys.stderr)
        return 1 if problems else 0
    print(f"generated {args.out}" if ensure(args.out, min_valid) else f"valid {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
