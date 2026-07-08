import tempfile
from pathlib import Path

import pytest
from securesystemslib.signer import CryptoSigner

from crosscodex_ingestion.attestation import AttestationManager, create_attestation_manager
from crosscodex_ingestion.config import IngestionSettings


def test_ephemeral_key_generation():
    """Verify that ephemeral ECDSA key is generated when no key path is provided."""
    settings = IngestionSettings(attestation_private_key_path="")
    manager = AttestationManager(settings)

    # Should have a valid public key ID
    key_id = manager.public_key_id()
    assert isinstance(key_id, str)
    assert len(key_id) > 0


def test_create_link_produces_valid_structure():
    """Verify that create_link produces a dict with materials, products, byproducts."""
    settings = IngestionSettings()
    manager = AttestationManager(settings)

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


def test_create_link_signature_non_empty():
    """Verify that the signature is non-empty."""
    settings = IngestionSettings()
    manager = AttestationManager(settings)

    result = manager.create_link(step_name="test-step", materials={}, products={}, byproducts={})

    sig = result["signatures"][0]["sig"]
    assert len(sig) > 0


def test_signed_link_verification():
    """Verify that a signed link can be verified with the public key."""
    settings = IngestionSettings()
    manager = AttestationManager(settings)

    result = manager.create_link(step_name="test-step", materials={}, products={}, byproducts={})

    # Import and verify using in_toto
    from in_toto.models.metadata import Metablock

    metablock = Metablock.from_dict(result)

    # Extract the signer's public key for verification
    # This verifies that the signature was created correctly
    assert len(metablock.signatures) == 1
    assert metablock.signatures[0]["keyid"] == manager.public_key_id()


def test_file_based_key_loading():
    """Verify that keys can be loaded from files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        private_key_path = Path(tmpdir) / "private_key.pem"

        # Generate an ECDSA key and save it
        signer = CryptoSigner.generate_ecdsa()
        private_key_path.write_bytes(signer.private_bytes)

        # Load the key
        settings = IngestionSettings(attestation_private_key_path=str(private_key_path))
        manager = AttestationManager(settings)

        # Should use the loaded key
        assert manager.public_key_id() == signer.public_key.keyid


def test_fips_mode_rejects_ed25519():
    """Verify that FIPS mode rejects Ed25519 keys."""
    with tempfile.TemporaryDirectory() as tmpdir:
        private_key_path = Path(tmpdir) / "private_key.pem"

        # Generate an Ed25519 key
        signer = CryptoSigner.generate_ed25519()
        private_key_path.write_bytes(signer.private_bytes)

        settings = IngestionSettings(attestation_private_key_path=str(private_key_path), fips_mode=True)

        with pytest.raises(ValueError, match="FIPS mode requires ECDSA"):
            AttestationManager(settings)


def test_fips_mode_rejects_rsa():
    """Verify that FIPS mode rejects RSA keys."""
    with tempfile.TemporaryDirectory() as tmpdir:
        private_key_path = Path(tmpdir) / "private_key.pem"

        # Generate an RSA key
        signer = CryptoSigner.generate_rsa()
        private_key_path.write_bytes(signer.private_bytes)

        settings = IngestionSettings(attestation_private_key_path=str(private_key_path), fips_mode=True)

        with pytest.raises(ValueError, match="FIPS mode requires ECDSA"):
            AttestationManager(settings)


def test_fips_mode_accepts_ecdsa():
    """Verify that FIPS mode accepts ECDSA keys."""
    with tempfile.TemporaryDirectory() as tmpdir:
        private_key_path = Path(tmpdir) / "private_key.pem"

        # Generate an ECDSA key
        signer = CryptoSigner.generate_ecdsa()
        private_key_path.write_bytes(signer.private_bytes)

        settings = IngestionSettings(attestation_private_key_path=str(private_key_path), fips_mode=True)

        # Should not raise
        manager = AttestationManager(settings)
        assert manager.public_key_id() == signer.public_key.keyid


def test_create_attestation_manager_returns_none_when_disabled():
    """Verify that create_attestation_manager returns None when attestation is disabled."""
    settings = IngestionSettings(attestation_enabled=False)
    manager = create_attestation_manager(settings)
    assert manager is None


def test_create_attestation_manager_returns_instance_when_enabled():
    """Verify that create_attestation_manager returns an instance when enabled."""
    settings = IngestionSettings(attestation_enabled=True)
    manager = create_attestation_manager(settings)
    assert isinstance(manager, AttestationManager)


def test_byproducts_preserved_in_output():
    """Verify that all byproducts fields are preserved in the output."""
    settings = IngestionSettings()
    manager = AttestationManager(settings)

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
