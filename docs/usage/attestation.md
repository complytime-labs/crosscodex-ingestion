# Verifying a conversion attestation

When `ATTESTATION_ENABLED=true`, each completed conversion produces a signed [in-toto](https://in-toto.io)
link. It binds the input document, the `structured.json` output and the caller that submitted the job. Use it
to prove that a `structured.json` came from this service, from a given input, and was not edited afterwards.

## Find the link

`GetDocument` returns the link's location in `Document.attestation_uri`:

- It is a storage URI: `s3://BUCKET/...` with S3 storage, or `file://...` with filesystem storage.
- It is empty while the job is still running, when the job failed, and when the job was converted with
  `ATTESTATION_ENABLED=false`. A document converted before this field existed also reads as empty, even if a
  link was stored for it.

Download the object to a local file, for example `aws s3 cp "$ATTESTATION_URI" attestation.link`.

## Verify it

You need the public key the service signs with (`ATTESTATION_PUBLIC_KEY_PATH`, see
[Attestation keys](configuration.md#attestation-keys)). From a checkout, run:

```bash
uv run python scripts/verify_attestation.py attestation.link attestation.pub \
  --material original.pdf --product structured.json
```

`--material` is the document you submitted and `--product` is the `structured.json` the service produced; both
are optional. The script prints one line per check:

```text
signature: OK (keyid cb210c0c...)
material: OK (sha256 9f86d0...)
product: OK (sha256 2c26b4...)
```

It exits 0 only when every check passes. Otherwise it exits 1 and says why:

| Message                    | Meaning                                                                          |
|----------------------------|----------------------------------------------------------------------------------|
| `signature: WRONG KEY`     | The link was signed by a different key than the one you gave                     |
| `signature: BAD`           | The link was modified after signing (its byproducts, hashes or name), or the signature is invalid |
| `material: MISMATCH`       | The file you gave is not the input the link records                              |
| `product: MISMATCH`        | The file you gave is not the output the link records                             |
| `link: ... not a valid in-toto link`, `public key: ...` | An input file is missing or malformed          |

A link that records several materials or products is matched to your file by its name; a file whose name the
link does not record fails with `no material named ...` or `no product named ...`. A link written by this
service records one of each, so any file name works.

The check proves the link is intact and matches your files. It does not prove the public key is the service's:
get that key from your operator over a channel you trust.

## What the link records

| Field                     | Value                                                                           |
|---------------------------|---------------------------------------------------------------------------------|
| step name                 | `convert-document`                                                              |
| material `input`          | sha256 of the submitted document                                                |
| product `structured_json` | sha256 of the stored `structured.json`                                          |
| byproduct `document_id`   | The document the link belongs to                                                |
| byproduct `tenant_id`     | The tenant named in the request                                                 |
| byproduct `format`        | The format the service detected                                                 |
| byproduct `created_by`    | The caller that submitted the job (see below)                                   |
| byproduct `mapping_id`, `mapping_sha256` | For JSON, CSV and XLSX only: the mapping used and its hash       |

## Who `created_by` names

`created_by` is the identity of the mTLS client certificate on the `ConvertDocument` call. The service
considers the certificate's common name first, then its subject alternative names in order. With
`GRPC_ALLOWED_CLIENTS` set, `created_by` is the first of these that is on the allowlist, which is the identity
that authorized the call (a certificate with CN `foo` and SAN `gateway`, allowlisting `gateway`, is recorded
as `gateway`). Without an allowlist it is the common name, or the first SAN when there is no common name. It
names the calling *service*, never a tenant or end user (see
[gRPC authentication](configuration.md#grpc-authentication)). With `GRPC_TLS_MODE=off` it is
`unauthenticated`. It appears in the link and in `Document.audit.created_by`. A job whose state record has no
`created_by` is attested as `unknown`.
