"""Attestation module for cryptographic signing of conversion operations.

This module provides in-toto link attestation for document conversion steps,
ensuring cryptographic verifiability of materials, products, and metadata.
"""

from pathlib import Path
from typing import Any

from in_toto.models.link import Link
from in_toto.models.metadata import Metablock
from securesystemslib.signer import CryptoSigner

from crosscodex_ingestion.config import IngestionSettings


class AttestationManager:
    """Manages cryptographic attestation for conversion operations.

    The AttestationManager creates and signs in-toto links that capture
    the materials (inputs), products (outputs), and byproducts (metadata)
    of each conversion step.

    In FIPS mode, only ECDSA P-256/P-384/P-521 keys are permitted.
    """

    def __init__(self, settings: IngestionSettings):
        """Initialize the attestation manager.

        Args:
            settings: Ingestion configuration settings.

        Raises:
            ValueError: If FIPS mode is enabled and a non-ECDSA key is loaded.
            FileNotFoundError: If the private key path is specified but does not exist.
        """
        self._settings = settings
        self._signer = self._initialize_signer()

    def _initialize_signer(self) -> CryptoSigner:
        """Initialize the cryptographic signer.

        Returns:
            A CryptoSigner instance with either an ephemeral or file-based key.

        Raises:
            ValueError: If FIPS mode is enabled and the key is not ECDSA.
            FileNotFoundError: If the private key path does not exist.
        """
        if self._settings.attestation_private_key_path:
            # Load key from file
            private_key_path = Path(self._settings.attestation_private_key_path)
            if not private_key_path.exists():
                raise FileNotFoundError(f"Private key not found: {private_key_path}")

            # Load the private key bytes
            private_key_bytes = private_key_path.read_bytes()

            # Deserialize the key using CryptoSigner
            from cryptography.hazmat.backends import default_backend
            from cryptography.hazmat.primitives import serialization

            private_key = serialization.load_pem_private_key(
                private_key_bytes, password=None, backend=default_backend()
            )

            # Create a signer from the loaded key
            signer = CryptoSigner(private_key)

            # FIPS mode validation
            if self._settings.fips_mode:
                if not signer.public_key.scheme.startswith("ecdsa-"):
                    raise ValueError(f"FIPS mode requires ECDSA keys, got {signer.public_key.scheme}")

            return signer
        else:
            # Generate ephemeral ECDSA key
            return CryptoSigner.generate_ecdsa()

    def create_link(
        self,
        step_name: str,
        materials: dict[str, dict],
        products: dict[str, dict],
        byproducts: dict,
    ) -> dict:
        """Create and sign an in-toto link for a conversion step.

        Args:
            step_name: Name of the conversion step.
            materials: Input artifacts (e.g., {"input.pdf": {"sha256": "..."}}).
            products: Output artifacts (e.g., {"output.proto": {"sha256": "..."}}).
            byproducts: Metadata about the conversion (trace_id, span_id, etc.).

        Returns:
            A serialized signed link as a dictionary with 'signed' and 'signatures' keys.
        """
        # Create the link object
        link = Link(
            name=step_name,
            materials=materials,
            products=products,
            command=[],  # No command execution in this context
            byproducts=byproducts,
        )

        # Wrap in a Metablock for signing
        metablock = Metablock(signed=link)

        # Sign the link
        metablock.create_signature(self._signer)

        # Return as dictionary
        result: dict[Any, Any] = metablock.to_dict()
        return result

    def public_key_id(self) -> str:
        """Return the public key ID of the signer.

        Returns:
            The key ID as a hex string.
        """
        return self._signer.public_key.keyid


def create_attestation_manager(
    settings: IngestionSettings,
) -> AttestationManager | None:
    """Create an attestation manager if attestation is enabled.

    Args:
        settings: Ingestion configuration settings.

    Returns:
        An AttestationManager instance if attestation_enabled is True, None otherwise.
    """
    if not settings.attestation_enabled:
        return None
    return AttestationManager(settings)
