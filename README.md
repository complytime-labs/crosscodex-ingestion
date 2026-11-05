# CrossCodex Ingestion Service

----

> 🤖 LLM WARNING 🤖
>
> This project was written with LLM (AI) assistance.
>
> 🤖 LLM WARNING 🤖

----

Python gRPC service for secure multi-format document ingestion and conversion using Docling.

## What It Does

The Ingestion service receives documents via gRPC, validates them for security, converts them to structured JSON using Docling, stores the results, and optionally generates cryptographic attestations. It supports both embedded (same-process) and distributed (NATS JetStream) execution modes.

**Key Features:**
- Multi-format document support (PDF, DOCX, HTML, TXT)
- Security hardening (SSRF prevention, format validation, subprocess isolation, resource limits)
- Structured element extraction (headings, paragraphs, tables, lists)
- State management (memory, Redis)
- Storage backends (filesystem, S3)
- In-toto cryptographic attestations
- NATS-based audit event publishing
- Prometheus metrics and OpenTelemetry tracing

## Quick Start

### Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) (Python package manager)
- podman or docker (for containerized deployment)

### Local Development

```bash
# Create .venv and install all dependencies
task setup

# Generate protobuf stubs
task proto

# Download the Docling models once (see "Docling models" below)
uv run docling-tools models download -o ~/.cache/crosscodex/docling-models

# Run the server (embedded mode, filesystem storage, memory state)
DOCLING_ARTIFACTS_PATH=~/.cache/crosscodex/docling-models STORAGE_PATH=/tmp/crosscodex-data task run

# Run tests
task test

# Run integration tests (requires docling and the downloaded models)
DOCLING_ARTIFACTS_PATH=~/.cache/crosscodex/docling-models task test:integration
```

## Docling models

The conversion worker runs in a sandboxed subprocess with no network access to model hubs
(`HF_HUB_OFFLINE=1`), so Docling's layout, table, and OCR models must already be on disk.

```bash
docling-tools models download -o <dir>
export DOCLING_ARTIFACTS_PATH=<dir>
```

The server **refuses to start** if `DOCLING_ARTIFACTS_PATH` is unset or not a directory. The container
images download the models at build time (`/opt/docling-models` in the standard image,
`/opt/app-root/docling-models` in the FIPS image) and set `DOCLING_ARTIFACTS_PATH` for you.

## Environment Variables

| Variable                                                                  | Default             | Description                                                                                                                                                                                                                      |
|---------------------------------------------------------------------------|---------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `DOCLING_ARTIFACTS_PATH`                                                  | (required)          | Directory with pre-downloaded Docling models; server refuses to start without it                                                                                                                                                 |
| `GRPC_PORT`                                                               | `8080`              | gRPC server port                                                                                                                                                                                                                 |
| `METRICS_PORT`                                                            | `9090`              | Prometheus metrics port                                                                                                                                                                                                          |
| `EXECUTION_MODE`                                                          | `embedded`          | `embedded` or `distributed` (NATS)                                                                                                                                                                                               |
| `MAX_CONCURRENT_CONVERSIONS`                                              | `4`                 | Conversions running at once (each is a separate subprocess)                                                                                                                                                                      |
| `SHUTDOWN_GRACE_SECONDS`                                                  | `25`                | Seconds SIGTERM gives in-flight conversions to finish (`>= 0`); see "Graceful shutdown"                                                                                                                                          |
| `STATE_BACKEND`                                                           | `memory`            | `memory` or `redis`                                                                                                                                                                                                              |
| `STORAGE_BACKEND`                                                         | `filesystem`        | `filesystem` or `s3`                                                                                                                                                                                                             |
| `STORAGE_PATH`                                                            | `/data/documents`   | Filesystem storage root                                                                                                                                                                                                          |
| `ALLOWED_FORMATS`                                                         | `pdf,docx,html,txt` | Comma-separated list of allowed formats                                                                                                                                                                                          |
| `MAX_DOCUMENT_SIZE`                                                       | `52428800`          | Maximum uploaded document size in bytes (50 MB)                                                                                                                                                                                  |
| `MAX_URL_BYTES`                                                           | `104857600`         | Maximum bytes to fetch from URLs (100 MB)                                                                                                                                                                                        |
| `URL_TIMEOUT`                                                             | `30`                | URL fetch timeout in seconds                                                                                                                                                                                                     |
| `MAX_REDIRECTS`                                                           | `5`                 | Maximum redirects followed when fetching a URL                                                                                                                                                                                   |
| `MAX_PAGES`                                                               | `1000`              | Documents with more pages fail (never silently truncated)                                                                                                                                                                        |
| `MAX_EXTRACTION_LENGTH`                                                   | `10485760`          | Maximum serialized output size in bytes (10 MB); larger output fails                                                                                                                                                             |
| `MAX_PROCESSING_TIME`                                                     | `3600`              | Wall-clock limit per conversion in seconds                                                                                                                                                                                       |
| `RLIMIT_CPU`                                                              | `21600`             | CPU-seconds limit for the conversion subprocess                                                                                                                                                                                  |
| `RLIMIT_AS_MB`                                                            | `65536`             | Virtual address space limit for the conversion subprocess (MB)                                                                                                                                                                   |
| `RLIMIT_FSIZE_MB`                                                         | `100`               | Maximum size of any file the conversion subprocess writes (MB)                                                                                                                                                                   |
| `ATTESTATION_ENABLED`                                                     | `true`              | Enable in-toto attestation generation. When `true`, both key paths below are required or the service refuses to start. `false` is for development only (conversions are unattested; a warning is logged)                         |
| `ATTESTATION_PRIVATE_KEY_PATH`                                            | (empty)             | PEM private key for attestation signing (ECDSA P-256)                                                                                                                                                                            |
| `ATTESTATION_PUBLIC_KEY_PATH`                                             | (empty)             | PEM public key matching the private key; startup fails if they are not a pair                                                                                                                                                    |
| `FIPS_MODE`                                                               | `false`             | Require the attestation key to be ECDSA on an approved curve                                                                                                                                                                     |
| `GRPC_TLS_MODE`                                                           | `mutual`            | `mutual` (require a client certificate) or `off` (plaintext, unauthenticated; development and tests only, logs a warning)                                                                                                        |
| `GRPC_TLS_CERT`                                                           | (empty)             | Server certificate (PEM); required for `mutual`                                                                                                                                                                                  |
| `GRPC_TLS_KEY`                                                            | (empty)             | Server private key (PEM); required for `mutual`                                                                                                                                                                                  |
| `GRPC_TLS_CA`                                                             | (empty)             | CA (PEM) that signs client certificates; required for `mutual`                                                                                                                                                                   |
| `GRPC_ALLOWED_CLIENTS`                                                    | (empty)             | Comma-separated client certificate identities (subject CN or DNS SAN) allowed to call the service; empty accepts any certificate signed by `GRPC_TLS_CA`. Rejected with `PERMISSION_DENIED`; not usable with `GRPC_TLS_MODE=off` |
| `NATS_URL`                                                                | (empty)             | NATS server URL (e.g., `nats://localhost:4222`)                                                                                                                                                                                  |
| `REDIS_URL`                                                               | (empty)             | Redis URL for state backend                                                                                                                                                                                                      |
| `S3_ENDPOINT`, `S3_BUCKET`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_REGION` | (see `config.py`)   | S3 storage backend settings                                                                                                                                                                                                      |

See `src/crosscodex_ingestion/config.py` for the full list.

### Limits and resource sizing

The defaults are sized from measurements of real documents (Docling offline, models pre-downloaded):

| Document         | Pages | Wall time | CPU time | VmPeak  | RSS peak |
|------------------|-------|-----------|----------|---------|----------|
| NIST CSF 2.0     | 32    | 44 s      | 186 s    | ~40 GB  | 2.7 GB   |
| NIST SP 800-53r5 | 492   | 807 s     | 3221 s   | 43.2 GB | 4.3 GB   |

- **Time**: Docling uses several threads, and `RLIMIT_CPU` counts CPU time across all threads (CPU is
  roughly 4-6x wall time). `MAX_PROCESSING_TIME=3600` and `RLIMIT_CPU=21600` leave headroom for documents
  several times larger than SP 800-53r5.
- **Memory**: `RLIMIT_AS` bounds *virtual address space only*. Importing torch alone needs more than 4 GB of
  address space and conversions peak at ~40 GB `VmPeak` while using only a few GB resident, so the default is
  64 GB. It is not a memory cap. **The real memory bound is the container memory limit**: allow at least
  6 GB per concurrent conversion (`MAX_CONCURRENT_CONVERSIONS`).
- **Pages and output size**: `MAX_PAGES` and `MAX_EXTRACTION_LENGTH` fail the conversion with a clear
  message instead of truncating.

### Graceful shutdown

On SIGTERM or SIGINT the service stops accepting RPCs and then drains conversions within a single budget of
`SHUTDOWN_GRACE_SECONDS` (default 25):

1. Documents still waiting for a conversion slot are marked `FAILED` immediately.
2. Running conversions may finish within the grace period and complete normally.
3. Conversions still running when it expires are cancelled: the Docling subprocess (and anything it spawned) is
   SIGKILLed, and the document ends `FAILED` with error code `UNAVAILABLE` ("The service shut down during
   conversion; resubmit the document."). An audit event is published for each. Resubmitting is safe: conversion
   uses local open-weight models, so a retry costs only CPU time.

Shutdown takes at most `SHUTDOWN_GRACE_SECONDS` plus about 2 s of cleanup. Keep the container runtime's stop timeout
above that, or the runtime SIGKILLs the process mid-cleanup and documents stay `RUNNING`: Kubernetes defaults to 30 s
(`terminationGracePeriodSeconds`), while `podman stop` and `docker stop` default to 10 s. Under podman/docker either
raise the timeout (`--stop-timeout 30`, or `stop_grace_period: 30s` in compose) or lower `SHUTDOWN_GRACE_SECONDS` to
about 7. A 492-page PDF takes about 13.5 minutes, so documents that large are cancelled by any realistic grace
period and must be resubmitted after a restart.

In `distributed` mode this service only publishes jobs to NATS JetStream; the consumer is not part of this
repository, so redelivery of in-flight messages on shutdown (negative acknowledgement) is up to that worker.

### gRPC authentication

The gRPC port uses mutual TLS by default and the service refuses to start without `GRPC_TLS_CERT`,
`GRPC_TLS_KEY` and `GRPC_TLS_CA` (or an explicit `GRPC_TLS_MODE=off`). A client certificate identifies the
calling *service* (for example the CrossCodex gateway), not a tenant: the service still trusts `tenant_id` in
the request, so only issue client certificates to services that enforce tenant isolation themselves, and list
them in `GRPC_ALLOWED_CLIENTS`. TLS settings use the gRPC/OpenSSL defaults; no cryptographic algorithms are
chosen by this service, so the FIPS image inherits whatever its OpenSSL FIPS provider permits.

The container health check only opens a TCP connection to the gRPC port, because a gRPC channel check would
need a client certificate.

### Dev and test certificates

`task certs` runs `scripts/devpki.py`, which writes a throwaway PKI to `.test-output/certs` (override with
`CERTS_DIR=...`): `ca.pem`/`ca.key`, `server.pem`/`server.key` (SANs `localhost`, `127.0.0.1`, `ingestion`,
`crosscodex-ingestion`), `client.pem`/`client.key` (CN `crosscodex-gateway`) and an ECDSA P-256 attestation pair
(`attestation.key`, `attestation.pub`). Key files are mode 0600. Never use this CA outside development.

- **Stale** means any of: a file missing or unparseable; a leftover temp directory from an interrupted run;
  a key file readable by group/other; `ca.pem` not a CA; server/client not issued by the CA or missing the
  right extended key usage; a private key not matching its certificate; a required server SAN missing;
  the attestation keys not a pair; or less than 24 hours of validity left (`--min-valid-hours`).
  `task certs` skips work when the set is fresh and `python scripts/devpki.py --verify` reports why it is not.
- **Regeneration replaces the whole set with a new CA**, never individual files, so every previously issued
  client certificate and any verifier holding the old `attestation.pub` must be re-issued or updated.
- Concurrent runs are safe: generation holds a file lock next to the output directory, re-checks inside it, and
  swaps the finished set in with a rename. pytest generates its own PKI in a temporary directory per run.
- `task compose:up` generates the certs and starts `docker-compose.yml` with mutual TLS and attestation on.
  A one-shot `certs-init` container copies the server certificate, server key and attestation keys into the
  `ingestion-certs` volume, owned by the service user (keys 0400), on every start, so it works for any host
  uid under rootless podman or docker and never serves a stale set. The CA key and client key are not copied.
  `compose:up` passes `--force-recreate`: podman-compose otherwise keeps containers whose compose config is
  unchanged, so after regenerating the PKI the service would keep trusting the old CA. Verifiers use
  `.test-output/certs/attestation.pub`; callers use `client.pem`, `client.key` and `ca.pem`.

### Attestation keys

Attestations are signed with the key at `ATTESTATION_PRIVATE_KEY_PATH` and are verifiable with the key at
`ATTESTATION_PUBLIC_KEY_PATH`. There is no ephemeral-key fallback. Generate a pair with, for example:

```bash
openssl ecparam -name prime256v1 -genkey -noout | openssl pkcs8 -topk8 -nocrypt -out attestation.key
openssl pkey -in attestation.key -pubout -out attestation.pub
```

The signer (securesystemslib) supports ECDSA P-256 only for ECDSA keys, so P-384 and P-521 keys are
rejected at startup even though FIPS mode would allow them.

### Identifiers

`tenant_id` must match `^[a-z][a-z0-9-]{1,50}[a-z0-9]$` (3-52 characters: lowercase letters, digits, hyphens;
starts with a letter, does not end with a hyphen). It is used in storage paths and NATS subjects, so other
values are rejected with `INVALID_ARGUMENT`. `GetDocument` requires the `document_id` returned by
`ConvertDocument` (a UUID).

## Container Build

```bash
# Standard build (downloads torch and the Docling models; slow and large)
podman build -t crosscodex-ingestion:latest .

# FIPS build (UBI9)
podman build -t crosscodex-ingestion:fips -f Dockerfile.fips .

# Run the container (see "Limits and resource sizing" for the memory limit)
podman run --memory 8g -p 8080:8080 \
  crosscodex-ingestion:latest
```

Without a volume, converted output is stored inside the container (`/data/documents`) and lost when it is
removed. Mount a volume at `/data/documents` to keep it. The container needs the TLS and attestation key
files mounted (see "gRPC authentication" and "Attestation keys"); for a throwaway local run add
`-e GRPC_TLS_MODE=off -e ATTESTATION_ENABLED=false`.

## Architecture

The service has three primary components:

1. **gRPC Service** (`service.py`, `server.py`): Handles `ConvertDocument`, `GetDocument`, and `ListDocuments` RPCs.
2. **Dispatch Layer** (`dispatch.py`): Routes jobs to either embedded workers or NATS JetStream.
3. **Worker** (`worker.py`): Fetches, validates, converts, extracts, stores, and attests documents.

```
gRPC Request → Dispatch → Worker → [Converter → Extractor] → Storage → State → Response
```

**Security Modules:**
- `security.py`: SSRF prevention, URL validation, content type detection, resource limit enforcement. URL fetches connect directly and ignore `HTTP_PROXY`/`HTTPS_PROXY`/`ALL_PROXY`: the address actually connected to is checked against the blocked ranges, and through a proxy that check would only see the proxy.
- `converter.py`: Async subprocess-based Docling execution with rlimits and timeout; the subprocess's process group is killed on timeout or cancellation
- `extractor.py`: Structured element extraction from Docling output

**Supporting Modules:**
- `state.py`: Document record state management (async interface; memory, or Redis via `redis.asyncio`)
- `storage.py`: Blob storage (filesystem or S3)
- `attestation.py`: In-toto link metadata generation
- `audit.py`: NATS JetStream audit event publisher
- `config.py`: Pydantic settings with environment variable overrides

## Testing

```bash
# Unit tests (fast, no external dependencies)
task test

# Integration tests (requires docling and DOCLING_ARTIFACTS_PATH, may be slow)
task test:integration

# Lint (ruff + ruff format --check + mypy)
task lint
```

## gRPC API

See `proto/crosscodex/v1/ingestion.proto` for the full API definition.

**Key RPCs:**
- `ConvertDocument`: Submit a document for asynchronous conversion.
- `GetDocument`: Retrieve conversion status and results.
- `ListDocuments`: Paginate through documents for a tenant.

## Development Workflow

1. Make changes to `src/crosscodex_ingestion/`
2. Add tests to `tests/unit/` or `tests/integration/`
3. Run `task lint` and `task test`
4. Commit changes (see commit message style in git log)
5. Build container with `podman build -t crosscodex-ingestion:latest .`

## Support

- Issues: [GitHub Issues](https://github.com/complytime-labs/crosscodex-ingestion/issues)
- Docling: [Document extraction library](https://github.com/DS4SD/docling)
