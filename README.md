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

# Run the server (embedded mode, filesystem storage, memory state)
task run

# Run tests
task test

# Run integration tests (requires docling)
task test:integration
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `GRPC_PORT` | `50051` | gRPC server port |
| `EXECUTION_MODE` | `embedded` | `embedded` or `distributed` (NATS) |
| `STATE_BACKEND` | `memory` | `memory` or `redis` |
| `STORAGE_BACKEND` | `filesystem` | `filesystem` or `s3` |
| `STORAGE_PATH` | `/var/lib/crosscodex/storage` | Filesystem storage root |
| `ALLOWED_FORMATS` | `pdf,docx,html,txt` | Comma-separated list of allowed formats |
| `MAX_DOCUMENT_SIZE` | `52428800` | Maximum document size in bytes (50 MB) |
| `MAX_URL_BYTES` | `52428800` | Maximum bytes to fetch from URLs |
| `URL_TIMEOUT` | `30` | URL fetch timeout in seconds |
| `CONVERSION_TIMEOUT` | `300` | Conversion subprocess timeout in seconds |
| `ATTESTATION_ENABLED` | `true` | Enable in-toto attestation generation |
| `ATTESTATION_KEY_PATH` | `keys/attestation.key` | Path to private key for attestation signing |
| `NATS_URL` | (empty) | NATS server URL (e.g., `nats://localhost:4222`) |
| `REDIS_URL` | (empty) | Redis URL for state backend |
| `S3_BUCKET` | (empty) | S3 bucket name for storage backend |

See `src/crosscodex_ingestion/config.py` for the full list.

## Container Build

```bash
# Standard build
podman build -t crosscodex-ingestion:latest .

# Run the container
podman run -p 50051:50051 \
  -e STORAGE_BACKEND=filesystem \
  -e STATE_BACKEND=memory \
  crosscodex-ingestion:latest
```

## Architecture

The service has three primary components:

1. **gRPC Service** (`service.py`, `server.py`): Handles `ConvertDocument`, `GetDocument`, and `ListDocuments` RPCs.
2. **Dispatch Layer** (`dispatch.py`): Routes jobs to either embedded workers or NATS JetStream.
3. **Worker** (`worker.py`): Fetches, validates, converts, extracts, stores, and attests documents.

```
gRPC Request → Dispatch → Worker → [Converter → Extractor] → Storage → State → Response
```

**Security Modules:**
- `security.py`: SSRF prevention, URL validation, content type detection, resource limit enforcement
- `converter.py`: Subprocess-based Docling execution with rlimits and timeout
- `extractor.py`: Structured element extraction from Docling output

**Supporting Modules:**
- `state.py`: Document record state management (memory or Redis)
- `storage.py`: Blob storage (filesystem or S3)
- `attestation.py`: In-toto link metadata generation
- `audit.py`: NATS JetStream audit event publisher
- `config.py`: Pydantic settings with environment variable overrides

## Testing

```bash
# Unit tests (fast, no external dependencies)
task test

# Integration tests (requires docling, may be slow)
task test:integration

# Lint
task lint

# Type check
mypy src/crosscodex_ingestion/ --ignore-missing-imports
```

Test coverage is ~137 tests across unit and integration suites.

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
