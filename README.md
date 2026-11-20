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
- Structured sources (JSON, CSV, XLSX) read through reviewed, declarative mappings (see [Structured sources](docs/usage/structured-sources.md))
- Security hardening (SSRF prevention, format validation, subprocess isolation, resource limits)
- Structured element extraction (headings, paragraphs, tables, lists)
- State management (memory, Redis)
- Storage backends (filesystem, S3)
- In-toto cryptographic attestations
- Publishing of audit events over NATS
- Prometheus metrics and OpenTelemetry tracing

## Quick Start

### Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) (Python package manager)
- [Task](https://taskfile.dev) (runs the commands below)
- [grpcurl](https://github.com/fullstorydev/grpcurl) (for the example requests)
- podman or docker (for containerized deployment)
- [Git LFS](https://git-lfs.com/) (for the integration tests: the real-publication PDF fixtures,
  `tests/fixtures/documents/nist-*.pdf`, are stored in LFS; run `git lfs pull` if you cloned without it)

### Run in a container

```bash
task compose:up
```

This generates a throwaway dev PKI in `.test-output/certs` (see [Dev and test certificates](docs/usage/configuration.md#dev-and-test-certificates)), builds the image
(slow the first time: it downloads torch and the Docling models), and starts the service on `localhost:8080`
with mutual TLS and attestation on, in the foreground. Stop it with Ctrl-C. Then
[convert a document](#convert-a-document).

### Run from source

To run the service from a checkout instead, follow [Run from source](docs/usage/getting-started.md).

### Convert a document

Any gRPC client works. These examples use [grpcurl](https://github.com/fullstorydev/grpcurl) with the dev client
certificate (CN `crosscodex-gateway`, the identity `GRPC_ALLOWED_CLIENTS` admits above and in compose). Run them
from the repository root, so the dev certificates and `proto/` resolve:

```bash
C=.test-output/certs
G="grpcurl -cacert $C/ca.pem -cert $C/client.pem -key $C/client.key -import-path proto -proto crosscodex/v1/ingestion.proto"

# Submit a document of your own (here policy.html; bytes fields are base64 in grpcurl's JSON); prints a documentId
$G -d "{\"tenant_context\": {\"tenant_id\": \"default\"},
        \"content\": \"$(base64 -w0 policy.html)\",
        \"metadata\": {\"mime_type\": \"text/html\"}}" \
  localhost:8080 crosscodex.v1.IngestionService/ConvertDocument

# Poll until conversionStatus is JOB_STATUS_COMPLETED (or JOB_STATUS_FAILED, with an error)
$G -d '{"tenant_context": {"tenant_id": "default"}, "document_id": "<documentId>"}' \
  localhost:8080 crosscodex.v1.IngestionService/GetDocument
```

Conversion is asynchronous. A completed document's `structuredJsonUri` (grpcurl prints proto field names such as
`structured_json_uri` in lowerCamelCase) points to the extracted elements under
`STORAGE_PATH` (in compose, inside the container's `/data/documents`). Use `"source_uri": "https://…"`
instead of `content` to have the service fetch a URL. `tenant_id` must be a valid identifier (see
[Identifiers](docs/usage/configuration.md#identifiers)). For JSON, CSV or XLSX, also set `"mapping"` (see
[Structured sources](docs/usage/structured-sources.md)).

## Where to go next

- **All usage docs:** [docs/usage/](docs/usage/README.md)
- **Run from source:** [docs/usage/getting-started.md](docs/usage/getting-started.md)
- **Output format and its guarantees:** [docs/usage/output-format.md](docs/usage/output-format.md)
- **Converting JSON, CSV and XLSX with mappings:** [docs/usage/structured-sources.md](docs/usage/structured-sources.md)
- **Verifying a conversion attestation:** [docs/usage/attestation.md](docs/usage/attestation.md)
- **Configuration, limits, authentication and keys:** [docs/usage/configuration.md](docs/usage/configuration.md)
- **How it works (architecture and design notes):** [docs/dev/](docs/dev/README.md)
- **Reporting a vulnerability:** [SECURITY.md](SECURITY.md)

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

The images fetch the models with `scripts/download_models.py`, not `docling-tools`: the CLI has no revision
option and Docling requests the moving `main` ref for most of its models, so pinning the docling package does
not fix their contents. The script pins each Hugging Face repository to a commit SHA (edit the table in the
script to bump one). RapidOCR checkpoints come from URLs fixed by the installed Docling and RapidOCR versions.
Both images install exactly the versions in `uv.lock`, hash-checked, so the models and Docling stay matched.

## Container Build

```bash
# Standard build (downloads torch and the Docling models; slow and large)
podman build -t crosscodex-ingestion:latest .

# FIPS build (UBI9)
podman build -t crosscodex-ingestion:fips -f Dockerfile.fips .

# Run the container (needs TLS and attestation keys, see below; see docs/usage/configuration.md for the memory limit)
podman run --memory 8g -p 8080:8080 \
  crosscodex-ingestion:latest
```

Without a volume, converted output is stored inside the container (`/data/documents`) and lost when it is
removed. Mount a volume at `/data/documents` to keep it. The container needs the TLS and attestation key
files mounted (see [gRPC authentication](docs/usage/configuration.md#grpc-authentication) and
[Attestation keys](docs/usage/configuration.md#attestation-keys)); for a throwaway local run add
`-e GRPC_TLS_MODE=off -e ATTESTATION_ENABLED=false`.

`task test:container` builds both images, runs each image's `HEALTHCHECK` against a service started with mutual
TLS and attestation, and converts `tests/fixtures/documents/sample.pdf` in a container started with
`--network=none`. It needs `podman`, is slow (it downloads torch and the models), and runs in CI on every PR.
If `podman build` fails with "mounting an overlay over build context directory" (podman nested inside another
container), point `TMPDIR` at a directory on a non-overlay filesystem.


## Testing

```bash
# Unit tests (fast, no external dependencies)
task test

# Integration tests (slow; downloads the pinned models on first run, see `task test:models`)
task test:integration

# Container tests (needs podman; builds both images, health check, offline conversion)
task test:container

# Lint (ruff + ruff format --check + mypy)
task lint
```

## gRPC API

See `proto/crosscodex/v1/ingestion.proto` for the full API definition.

**Key RPCs:**
- `ConvertDocument`: Submit a document for asynchronous conversion.
- `GetDocument`: Retrieve conversion status and results.
- `ListDocuments`: List a tenant's documents, with filters, sort and stable pagination ([details](docs/usage/listing.md)).

## Development Workflow

1. Make changes to `src/crosscodex_ingestion/`
2. Add tests to `tests/unit/` or `tests/integration/`
3. Run `task lint` and `task test`
4. Commit changes (see commit message style in git log)
5. Build container with `podman build -t crosscodex-ingestion:latest .`

## Support

- Issues: [GitHub Issues](https://github.com/complytime-labs/crosscodex-ingestion/issues)
- Docling: [Document extraction library](https://github.com/DS4SD/docling)
