"""Tests for scripts/verify_attestation.py, run as a subprocess against links signed by AttestationManager."""

import hashlib
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from crosscodex_ingestion.attestation import AttestationManager
from crosscodex_ingestion.config import IngestionSettings

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "verify_attestation.py"
INPUT = b"%PDF-1.4 original input"
OUTPUT = b'{"elements":[]}'


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_key_pair(directory: Path, name: str) -> tuple[Path, Path]:
    key = ec.generate_private_key(ec.SECP256R1())
    private = directory / f"{name}.key"
    public = directory / f"{name}.pub"
    private.write_bytes(
        key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    )
    public.write_bytes(
        key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    )
    return private, public


@dataclass(frozen=True)
class Signed:
    directory: Path
    link: Path
    public_key: Path
    other_public_key: Path
    input_file: Path
    output_file: Path


@pytest.fixture
def signed(tmp_path: Path) -> Signed:
    private, public = _write_key_pair(tmp_path, "signer")
    _, other_public = _write_key_pair(tmp_path, "other")
    manager = AttestationManager(
        IngestionSettings(
            attestation_enabled=True,
            attestation_private_key_path=str(private),
            attestation_public_key_path=str(public),
        )
    )
    link = manager.create_link(
        step_name="convert-document",
        materials={"input": {"sha256": _sha256(INPUT)}},
        products={"structured_json": {"sha256": _sha256(OUTPUT)}},
        byproducts={"document_id": "d1", "created_by": "gateway"},
    )
    link_path = tmp_path / "attestation.link"
    link_path.write_text(json.dumps(link))
    (tmp_path / "input.pdf").write_bytes(INPUT)
    (tmp_path / "structured.json").write_bytes(OUTPUT)
    return Signed(tmp_path, link_path, public, other_public, tmp_path / "input.pdf", tmp_path / "structured.json")


def _run(*args: Path | str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-I", str(SCRIPT), *map(str, args)], capture_output=True, text=True, timeout=60, check=False
    )


def test_valid_link_exits_zero_and_reports_key_id(signed: Signed):
    result = _run(signed.link, signed.public_key)

    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("signature: OK (keyid ")


def test_matching_material_and_product_are_reported_ok(signed: Signed):
    result = _run(signed.link, signed.public_key, "--material", signed.input_file, "--product", signed.output_file)

    assert result.returncode == 0, result.stderr
    assert "material: OK" in result.stdout
    assert "product: OK" in result.stdout


def test_tampered_byproduct_fails_with_bad_signature(signed: Signed):
    link = json.loads(signed.link.read_text())
    link["signed"]["byproducts"]["created_by"] = "someone-else"
    signed.link.write_text(json.dumps(link))

    result = _run(signed.link, signed.public_key)

    assert result.returncode == 1
    assert "signature: BAD" in result.stderr
    assert "modified" in result.stderr


def test_wrong_key_fails_naming_the_key_mismatch(signed: Signed):
    result = _run(signed.link, signed.other_public_key)

    assert result.returncode == 1
    assert "signature: WRONG KEY" in result.stderr


def test_mismatched_product_fails_after_reporting_signature_ok(signed: Signed):
    signed.output_file.write_bytes(b'{"elements":["forged"]}')

    result = _run(signed.link, signed.public_key, "--product", signed.output_file)

    assert result.returncode == 1
    assert "signature: OK" in result.stdout
    assert "product: MISMATCH" in result.stdout


def test_mismatched_material_fails(signed: Signed):
    signed.input_file.write_bytes(b"different input")

    result = _run(signed.link, signed.public_key, "--material", signed.input_file)

    assert result.returncode == 1
    assert "material: MISMATCH" in result.stdout


def test_failures_have_distinct_messages(signed: Signed):
    tampered = json.loads(signed.link.read_text())
    tampered["signed"]["byproducts"]["created_by"] = "x"
    tampered_path = signed.directory / "tampered.link"
    tampered_path.write_text(json.dumps(tampered))
    signed.output_file.write_bytes(b"changed")

    messages = {
        _run(tampered_path, signed.public_key).stderr,
        _run(signed.link, signed.other_public_key).stderr,
        _run(signed.link, signed.public_key, "--product", signed.output_file).stdout,
    }

    assert len(messages) == 3


def test_multiple_products_match_by_basename(signed: Signed):
    private, public = _write_key_pair(signed.directory, "multi")
    manager = AttestationManager(
        IngestionSettings(
            attestation_enabled=True,
            attestation_private_key_path=str(private),
            attestation_public_key_path=str(public),
        )
    )
    link = manager.create_link(
        step_name="convert-document",
        materials={"input": {"sha256": _sha256(INPUT)}},
        products={"structured.json": {"sha256": _sha256(OUTPUT)}, "other.json": {"sha256": _sha256(b"x")}},
        byproducts={},
    )
    link_path = signed.directory / "multi.link"
    link_path.write_text(json.dumps(link))

    ok = _run(link_path, public, "--product", signed.output_file)
    no_entry = _run(link_path, public, "--product", signed.input_file)

    assert ok.returncode == 0, ok.stderr
    assert no_entry.returncode == 1
    assert "no product named" in no_entry.stdout + no_entry.stderr


def test_unreadable_link_exits_one(signed: Signed):
    result = _run(signed.directory / "missing.link", signed.public_key)

    assert result.returncode == 1
    assert "missing.link" in result.stderr


def test_malformed_link_exits_one(signed: Signed):
    signed.link.write_text("not json")

    result = _run(signed.link, signed.public_key)

    assert result.returncode == 1
    assert "not a valid in-toto link" in result.stderr


def test_unreadable_public_key_exits_one(signed: Signed):
    result = _run(signed.link, signed.directory / "missing.pub")

    assert result.returncode == 1
    assert "missing.pub" in result.stderr


def test_link_without_a_product_digest_fails_with_a_clear_message(signed: Signed):
    private, public = _write_key_pair(signed.directory, "nodigest")
    manager = AttestationManager(
        IngestionSettings(
            attestation_enabled=True,
            attestation_private_key_path=str(private),
            attestation_public_key_path=str(public),
        )
    )
    link = manager.create_link("convert-document", {}, {"structured_json": {}}, {})
    link_path = signed.directory / "nodigest.link"
    link_path.write_text(json.dumps(link))

    result = _run(link_path, public, "--product", signed.output_file)

    assert result.returncode == 1
    assert "records no sha256" in result.stderr
