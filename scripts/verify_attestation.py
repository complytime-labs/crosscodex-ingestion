#!/usr/bin/env python3
"""Verify a conversion attestation link written by the ingestion service.

Usage:
    uv run python scripts/verify_attestation.py LINK PUBLIC_KEY [--material FILE] [--product FILE]

LINK is the object GetDocument.attestation_uri points to (download it first). PUBLIC_KEY is the PEM
file the service publishes as ATTESTATION_PUBLIC_KEY_PATH. --material is the original input document;
--product is the structured.json the service produced. Each file's sha256 is compared with the digest
the signed link records.

Exit status is 0 only when the signature and every requested hash check pass; any failure exits 1
with a message naming the cause (WRONG KEY, BAD signature, MISMATCH, ...).
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from in_toto.exceptions import SignatureVerificationError
from in_toto.models.metadata import Metablock
from securesystemslib.exceptions import FormatError
from securesystemslib.signer import SSlibKey


class VerificationFailure(Exception):
    """A check failed; the message is the line to show the operator."""


def _read(path: Path, what: str) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise VerificationFailure(f"{what}: cannot read {path}: {exc.strerror or exc}") from exc


def _load_link(path: Path) -> Metablock:
    try:
        return Metablock.from_dict(json.loads(_read(path, "link")))
    except (ValueError, FormatError, AttributeError, TypeError) as exc:
        raise VerificationFailure(f"link: {path} is not a valid in-toto link: {exc}") from exc


def _load_public_key(path: Path) -> SSlibKey:
    try:
        return SSlibKey.from_crypto(serialization.load_pem_public_key(_read(path, "public key")))
    except (ValueError, TypeError) as exc:
        raise VerificationFailure(f"public key: {path} is not a usable PEM public key: {exc}") from exc


def _check_signature(link: Metablock, key: SSlibKey) -> str:
    signers = [signature["keyid"] for signature in link.signatures]
    if key.keyid not in signers:
        raise VerificationFailure(
            f"signature: WRONG KEY (link is signed by keyid {', '.join(signers) or 'nobody'}, "
            f"the given public key is keyid {key.keyid})"
        )
    try:
        link.verify_signature({"keyid": key.keyid, **key.to_dict()})
    except SignatureVerificationError as exc:
        raise VerificationFailure(
            f"signature: BAD (the link was modified after signing, or its signature is invalid): {exc}"
        ) from exc
    return key.keyid


def _recorded_digest(artifacts: dict[str, dict], file: Path, kind: str) -> str:
    """The sha256 the link records for FILE: the only entry, else the one named like the file."""
    if len(artifacts) == 1:
        name = next(iter(artifacts))
    elif file.name in artifacts:
        name = file.name
    else:
        raise VerificationFailure(
            f"{kind}: no {kind} named {file.name!r} in the link (it records: {', '.join(sorted(artifacts)) or 'none'})"
        )
    digest = artifacts[name].get("sha256")
    if not digest:
        raise VerificationFailure(f"{kind}: the link records no sha256 for {name!r}")
    return str(digest)


def _check_hash(artifacts: dict[str, dict], file: Path, kind: str) -> tuple[str, bool]:
    """Compare FILE's sha256 with the link; returns the report line and whether it matched."""
    recorded = _recorded_digest(artifacts, file, kind)
    actual = hashlib.sha256(_read(file, kind)).hexdigest()
    if actual == recorded:
        return f"{kind}: OK (sha256 {actual})", True
    return f"{kind}: MISMATCH ({file} is sha256 {actual}, the link records {recorded})", False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify a conversion attestation link.")
    parser.add_argument("link", type=Path, help="attestation link file (GetDocument.attestation_uri)")
    parser.add_argument("public_key", type=Path, help="PEM public key the service signs with")
    parser.add_argument("--material", type=Path, help="original input document to check against the link")
    parser.add_argument("--product", type=Path, help="structured.json to check against the link")
    args = parser.parse_args(argv)

    ok = True
    try:
        link = _load_link(args.link)
        keyid = _check_signature(link, _load_public_key(args.public_key))
        print(f"signature: OK (keyid {keyid})")
        for kind, file, artifacts in (
            ("material", args.material, link.signed.materials),
            ("product", args.product, link.signed.products),
        ):
            if file is not None:
                line, matched = _check_hash(artifacts, file, kind)
                print(line)
                ok = ok and matched
    except VerificationFailure as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
