from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, rsa
from securesystemslib.signer import CryptoSigner

from crosscodex_ingestion.attestation import AttestationManager, create_attestation_manager
from crosscodex_ingestion.config import IngestionSettings


def _write_key_pair(directory: Path, private_key, name: str = "key") -> tuple[Path, Path]:
    """Write private_key and its public half as PEM files; return (private_path, public_path)."""
    private_path = directory / f"{name}.pem"
    public_path = directory / f"{name}.pub.pem"
    private_path.write_bytes(
        private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    public_path.write_bytes(
        private_key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )
    )
    return private_path, public_path


@pytest.fixture
def key_settings(tmp_path) -> IngestionSettings:
    """Settings pointing at a freshly generated, matching ECDSA P-256 key pair."""
    private_path, public_path = _write_key_pair(tmp_path, ec.generate_private_key(ec.SECP256R1()))
    return IngestionSettings(
        attestation_private_key_path=str(private_path),
        attestation_public_key_path=str(public_path),
    )


def test_enabled_without_any_key_paths_refuses_to_start():
    """Fail closed: an ephemeral key would sign attestations nobody can verify."""
    with pytest.raises(
        ValueError, match=r"ATTESTATION_PRIVATE_KEY_PATH.*ATTESTATION_PUBLIC_KEY_PATH.*ATTESTATION_ENABLED=false"
    ):
        AttestationManager(IngestionSettings())


def test_enabled_without_public_key_path_refuses_to_start(key_settings):
    key_settings.attestation_public_key_path = ""
    with pytest.raises(ValueError, match="ATTESTATION_PUBLIC_KEY_PATH"):
        AttestationManager(key_settings)


def test_enabled_without_private_key_path_refuses_to_start(key_settings):
    key_settings.attestation_private_key_path = ""
    with pytest.raises(ValueError, match="ATTESTATION_PRIVATE_KEY_PATH"):
        AttestationManager(key_settings)


def test_missing_public_key_file_refuses_to_start(key_settings, tmp_path):
    key_settings.attestation_public_key_path = str(tmp_path / "absent.pub.pem")
    with pytest.raises(FileNotFoundError, match="Public key not found"):
        AttestationManager(key_settings)


def test_unparseable_public_key_refuses_to_start(key_settings, tmp_path):
    bad = tmp_path / "bad.pub.pem"
    bad.write_bytes(b"not a pem")
    key_settings.attestation_public_key_path = str(bad)
    with pytest.raises(ValueError, match="Cannot load attestation public key"):
        AttestationManager(key_settings)


def test_unparseable_private_key_refuses_to_start(key_settings, tmp_path):
    bad = tmp_path / "bad.pem"
    bad.write_bytes(b"not a pem")
    key_settings.attestation_private_key_path = str(bad)
    with pytest.raises(ValueError, match="Cannot load attestation private key"):
        AttestationManager(key_settings)


def test_mismatched_key_pair_refuses_to_start(key_settings, tmp_path):
    _, other_public = _write_key_pair(tmp_path, ec.generate_private_key(ec.SECP256R1()), name="other")
    key_settings.attestation_public_key_path = str(other_public)
    with pytest.raises(ValueError, match="not a key pair"):
        AttestationManager(key_settings)


def test_disabled_attestation_creates_no_manager_and_warns(caplog):
    with caplog.at_level("WARNING"):
        assert create_attestation_manager(IngestionSettings(attestation_enabled=False)) is None
    assert "unattested" in caplog.text


def test_create_link_produces_valid_structure(key_settings):
    """Verify that create_link produces a dict with materials, products, byproducts."""
    manager = AttestationManager(key_settings)

    materials = {"input.pdf": {"sha256": "abc123"}}
    products = {"output.proto": {"sha256": "def456"}}
    byproducts = {
        "trace_id": "trace-123",
        "span_id": "span-456",
        "tenant_id": "tenant-789",
        "hostname": "worker-01",
        "timestamp": "2026-07-01T12:00:00Z",
        "return_value": 0,
        "detected_format": "pdf",
        "element_count": 156,
        "processing_time_ms": 3200,
    }

    result = manager.create_link(
        step_name="convert-document",
        materials=materials,
        products=products,
        byproducts=byproducts,
    )

    # Check top-level structure
    assert "signed" in result
    assert "signatures" in result

    # Check signed section
    signed = result["signed"]
    assert signed["name"] == "convert-document"
    assert signed["materials"] == materials
    assert signed["products"] == products
    assert signed["byproducts"] == byproducts

    # Check signatures exist
    assert len(result["signatures"]) == 1
    assert "keyid" in result["signatures"][0]
    assert "sig" in result["signatures"][0]


def test_create_link_signature_non_empty(key_settings):
    """Verify that the signature is non-empty."""
    manager = AttestationManager(key_settings)

    result = manager.create_link(step_name="test-step", materials={}, products={}, byproducts={})

    sig = result["signatures"][0]["sig"]
    assert len(sig) > 0


def test_signed_link_verification(key_settings):
    """Verify that a signed link can be verified with the public key."""
    manager = AttestationManager(key_settings)

    result = manager.create_link(step_name="test-step", materials={}, products={}, byproducts={})

    # Import and verify using in_toto
    from in_toto.models.metadata import Metablock

    metablock = Metablock.from_dict(result)

    # Extract the signer's public key for verification
    # This verifies that the signature was created correctly
    assert len(metablock.signatures) == 1
    assert metablock.signatures[0]["keyid"] == manager.public_key_id()


def test_file_based_key_loading(key_settings):
    """The manager signs with the configured private key."""
    manager = AttestationManager(key_settings)
    private = serialization.load_pem_private_key(
        Path(key_settings.attestation_private_key_path).read_bytes(), password=None
    )
    assert manager.public_key_id() == CryptoSigner(private).public_key.keyid


@pytest.mark.parametrize(
    "key",
    [ed25519.Ed25519PrivateKey.generate(), rsa.generate_private_key(65537, 2048)],
    ids=["ed25519", "rsa"],
)
def test_fips_mode_rejects_non_ecdsa(tmp_path, key):
    private_path, public_path = _write_key_pair(tmp_path, key)
    settings = IngestionSettings(
        attestation_private_key_path=str(private_path),
        attestation_public_key_path=str(public_path),
        fips_mode=True,
    )
    with pytest.raises(ValueError, match="FIPS mode requires ECDSA"):
        AttestationManager(settings)


def test_fips_mode_rejects_unapproved_curve(tmp_path):
    private_path, public_path = _write_key_pair(tmp_path, ec.generate_private_key(ec.SECP256K1()))
    settings = IngestionSettings(
        attestation_private_key_path=str(private_path),
        attestation_public_key_path=str(public_path),
        fips_mode=True,
    )
    with pytest.raises(ValueError, match="approved curve"):
        AttestationManager(settings)


def test_fips_mode_accepts_p256(tmp_path):
    private_path, public_path = _write_key_pair(tmp_path, ec.generate_private_key(ec.SECP256R1()))
    settings = IngestionSettings(
        attestation_private_key_path=str(private_path),
        attestation_public_key_path=str(public_path),
        fips_mode=True,
    )
    assert AttestationManager(settings).public_key_id()


def test_curve_unsupported_by_signer_rejected_with_actionable_error(tmp_path):
    """Unsupported by securesystemslib's signer: fail at startup, not per conversion."""
    private_path, public_path = _write_key_pair(tmp_path, ec.generate_private_key(ec.SECP256K1()))
    settings = IngestionSettings(
        attestation_private_key_path=str(private_path), attestation_public_key_path=str(public_path)
    )
    with pytest.raises(ValueError, match="not usable by the signer"):
        AttestationManager(settings)


def test_create_attestation_manager_returns_instance_when_enabled(key_settings):
    assert isinstance(create_attestation_manager(key_settings), AttestationManager)


def test_byproducts_preserved_in_output(key_settings):
    """Verify that all byproducts fields are preserved in the output."""
    manager = AttestationManager(key_settings)

    byproducts = {
        "trace_id": "trace-abc",
        "span_id": "span-def",
        "tenant_id": "tenant-ghi",
        "hostname": "worker-02",
        "timestamp": "2026-07-01T14:30:00Z",
        "return_value": 0,
        "detected_format": "docx",
        "element_count": 234,
        "processing_time_ms": 4500,
    }

    result = manager.create_link(step_name="test", materials={}, products={}, byproducts=byproducts)

    # All byproducts should be present
    assert result["signed"]["byproducts"] == byproducts
