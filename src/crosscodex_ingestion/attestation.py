"""Attestation module for cryptographic signing of conversion operations.

This module provides in-toto link attestation for document conversion steps,
ensuring cryptographic verifiability of materials, products, and metadata.
"""

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from in_toto.models.link import Link
from in_toto.models.metadata import Metablock
from securesystemslib.signer import CryptoSigner

from crosscodex_ingestion.config import IngestionSettings

logger = logging.getLogger(__name__)

# NIST-approved ECDSA curves permitted in FIPS mode.
_FIPS_CURVES = frozenset({"secp256r1", "secp384r1", "secp521r1"})


def _load_key(path: Path, kind: str, loader: Callable[..., Any], **kwargs: Any) -> Any:
    """Read a PEM key file and parse it, with errors that name the file and setting."""
    if not path.exists():
        raise FileNotFoundError(f"{kind.capitalize()} key not found: {path}")
    try:
        return loader(path.read_bytes(), **kwargs)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"Cannot load attestation {kind} key {path}: {exc}") from exc


def _public_pem(public_key: Any) -> bytes:
    return bytes(public_key.public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))


class AttestationManager:
    """Manages cryptographic attestation for conversion operations.

    The AttestationManager creates and signs in-toto links that capture
    the materials (inputs), products (outputs), and byproducts (metadata)
    of each conversion step.

    In FIPS mode, only ECDSA P-256/P-384/P-521 keys are permitted. (securesystemslib currently
    signs ECDSA with P-256 only, so P-384/P-521 keys are rejected at load time.)
    """

    def __init__(self, settings: IngestionSettings):
        """Initialize the attestation manager.

        Args:
            settings: Ingestion configuration settings.

        Raises:
            ValueError: If either key path is unset, a key cannot be parsed, the keys are not
                one pair, or FIPS mode is enabled and the key is not ECDSA on an approved curve.
            FileNotFoundError: If a key file does not exist.
        """
        self._settings = settings
        self._signer = self._initialize_signer()

    def _initialize_signer(self) -> CryptoSigner:
        """Load and validate the configured key pair.

        Attestations are only useful if a verifier can check them, so both halves of the
        key pair are required (there is no ephemeral-key fallback).

        Returns:
            A CryptoSigner backed by the configured private key.

        Raises:
            ValueError: See __init__.
            FileNotFoundError: See __init__.
        """
        private_path = self._settings.attestation_private_key_path
        public_path = self._settings.attestation_public_key_path
        if not private_path or not public_path:
            raise ValueError(
                "ATTESTATION_ENABLED=true requires both ATTESTATION_PRIVATE_KEY_PATH and "
                "ATTESTATION_PUBLIC_KEY_PATH: attestations signed without a published public key "
                "cannot be verified. Provision an ECDSA key pair and set both paths, or set "
                "ATTESTATION_ENABLED=false (development only; conversions will be unattested)."
            )

        private_key = _load_key(Path(private_path), "private", serialization.load_pem_private_key, password=None)
        public_key = _load_key(Path(public_path), "public", serialization.load_pem_public_key)

        if _public_pem(private_key.public_key()) != _public_pem(public_key):
            raise ValueError(
                f"ATTESTATION_PRIVATE_KEY_PATH ({private_path}) and ATTESTATION_PUBLIC_KEY_PATH "
                f"({public_path}) are not a key pair"
            )

        if self._settings.fips_mode:
            if not isinstance(private_key, ec.EllipticCurvePrivateKey):
                raise ValueError(f"FIPS mode requires ECDSA keys, got {type(private_key).__name__}")
            if private_key.curve.name not in _FIPS_CURVES:
                raise ValueError(
                    f"FIPS mode requires ECDSA on an approved curve ({', '.join(sorted(_FIPS_CURVES))}), "
                    f"got {private_key.curve.name}"
                )

        try:
            return CryptoSigner(private_key)
        except ValueError as exc:
            # securesystemslib signs ECDSA with P-256 only; surface that instead of its terse message.
            raise ValueError(f"Attestation key {private_path} is not usable by the signer: {exc}") from exc

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

    Raises:
        ValueError, FileNotFoundError: If enabled and the key pair is missing or invalid.
    """
    if not settings.attestation_enabled:
        logger.warning(
            "ATTESTATION_ENABLED=false: conversions are unattested (development only; "
            "provision ATTESTATION_PRIVATE_KEY_PATH and ATTESTATION_PUBLIC_KEY_PATH for production)"
        )
        return None
    return AttestationManager(settings)
