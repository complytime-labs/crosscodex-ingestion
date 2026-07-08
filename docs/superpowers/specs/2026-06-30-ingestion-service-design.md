# CrossCodex Ingestion Service — Design Specification

**Date:** 2026-06-30 (revised 2026-07-01)
**Status:** Draft
**Issue:** [crosscodex#18](https://github.com/complytime-labs/crosscodex/issues/18)
**Reference:** OllamaCrosswalker (`/workspace/OllamaCrosswalker`), CrossCodex monorepo (`complytime-labs/crosscodex`)

## Purpose

Async Python gRPC service that wraps Docling for multi-format document conversion. Accepts raw document bytes or URLs, converts via Docling in an isolated subprocess, stores structured JSON output in object storage, and provides job status tracking. Multi-tenant, cryptographically attested, and observable.

No OSCAL knowledge — compliance domain logic belongs to the Go Catalog service.

## Scope

### In scope

- gRPC service implementing `IngestionService` as defined in the monorepo's `ingestion.proto`
  - `ConvertDocument` (async: returns document_id + PENDING status)
  - `GetDocument` (poll for status/results)
  - `ListDocuments` (tenant-scoped document listing)
- Document conversion: PDF, DOCX, HTML, plain text via Docling
- Structural extraction: element types, hierarchy, tiers 1-3 pattern detection
- URL fetching with SSRF prevention (reusable library module)
- Format validation via byte-sniffing + ContentMetadata
- Subprocess isolation with configurable rlimits
- Multi-tenant isolation via TenantContext on every request
- Object storage for conversion output (S3-compatible, local filesystem fallback)
- In-toto attestation: signed links per conversion with OTel trace correlation
- NATS JetStream audit events with provenance headers
- OpenTelemetry tracing with gRPC metadata propagation + attestation bridge
- Prometheus metrics
- Standard + FIPS container images
- Unit, integration, and container-driven tests

### Out of scope

- OSCAL catalog structuring (Go Catalog service, `pkg/oscal/`)
- LLM-based section detection (tiers 4-5 from original project)
- LLM enrichment / validation
- Any compliance domain logic
- Embedding generation or relationship classification

## gRPC Proto Contract

Proto files are copied from the monorepo's `api/proto/crosscodex/v1/`. When the monorepo publishes `crosscodex-proto`, the local copy is replaced by a package dependency.

### ingestion.proto (from monorepo)

```protobuf
syntax = "proto3";
package crosscodex.v1;

import "crosscodex/v1/common.proto";

service IngestionService {
  rpc ConvertDocument(ConvertDocumentRequest) returns (ConvertDocumentResponse);
  rpc GetDocument(GetDocumentRequest) returns (GetDocumentResponse);
  rpc ListDocuments(ListDocumentsRequest) returns (ListDocumentsResponse);
}

message ConvertDocumentRequest {
  TenantContext tenant_context = 1;
  oneof source {
    bytes content = 2;
    string source_uri = 3;
  }
  ContentMetadata metadata = 4;
}

message ConvertDocumentResponse {
  string document_id = 1;
  JobStatus status = 2;  // PENDING or RUNNING
}

message Document {
  string document_id = 1;
  TenantContext tenant_context = 2;
  ContentMetadata metadata = 3;
  AuditMetadata audit = 4;
  JobStatus conversion_status = 5;
  string structured_json_uri = 6;  // Object storage path to output
  Error error = 7;
}

message GetDocumentRequest {
  TenantContext tenant_context = 1;
  string document_id = 2;
}

message GetDocumentResponse {
  Document document = 1;
}

message ListDocumentsRequest {
  TenantContext tenant_context = 1;
  ListOptions options = 2;
}

message ListDocumentsResponse {
  repeated Document documents = 1;
  PageInfo page_info = 2;
}
```

### Key common.proto types used

- **TenantContext**: `tenant_id` (required) + optional metadata map
- **ContentMetadata**: `mime_type`, `sha256`, `size_bytes`, `format_version`, `source_uri`
- **AuditMetadata**: `created_at`, `created_by`, `updated_at`, `correlation_id` (OTel trace ID)
- **ProvenanceMetadata**: `sources`, `transformations`, `attestation_link_id`, `quality_score`
- **JobStatus**: PENDING → RUNNING → COMPLETED / FAILED / CANCELLED
- **Error**: `ErrorCode` enum + message + details map
- **ListOptions**: Pagination, FieldFilter, SortOrder

### Structured JSON output format

The conversion output stored at `structured_json_uri` is a JSON document containing extracted elements with hierarchy:

```json
{
  "document_id": "uuid",
  "metadata": {
    "page_count": 42,
    "detected_format": "pdf",
    "element_count": 156,
    "title": "NIST SP 800-53 Rev 5",
    "processing_time_ms": 3200
  },
  "elements": [
    {
      "element_id": "e-001",
      "type": "heading",
      "content": "Access Control",
      "page": 1,
      "level": 1,
      "parent_id": null,
      "style": {"bold": true, "font_size": 14.0}
    },
    {
      "element_id": "e-002",
      "type": "paragraph",
      "content": "The organization...",
      "page": 1,
      "level": 0,
      "parent_id": "e-001",
      "style": {}
    }
  ]
}
```

Element types: `heading`, `paragraph`, `table`, `list`, `list_item`, `page_header`, `page_footer`.

### Completion signaling

Callers poll `GetDocument` until `conversion_status` reaches `JOB_STATUS_COMPLETED` or `JOB_STATUS_FAILED`. NATS events are for audit only, not completion notification. Streaming and event-driven notification are deferred to a future iteration — the staged ETL model (store output, poll for completion) is the correct architecture for this pipeline.

## Module Architecture

```
ConvertDocumentRequest
        │
        ▼
┌───────────────────────┐
│     service.py        │  Validates TenantContext + request
│  (IngestionService)   │  Assigns document_id (UUID)
│                       │  Returns PENDING immediately
└────────┬──────────────┘
         │ (background task)
         ▼
┌───────────────────────┐
│     worker.py         │  Async conversion worker
│  (ConversionWorker)   │  Manages job lifecycle: PENDING → RUNNING → COMPLETED/FAILED
└────────┬──────────────┘
         │
    ┌────┴────┐
    │         │
    ▼         ▼
┌────────┐ ┌──────────┐
│security│ │security  │  URL path: SSRF validation + fetch
│.validate│ │.fetch_url│  Bytes path: format sniffing + validation
└────┬───┘ └────┬─────┘
     │          │
     ▼          ▼
     document bytes on disk
         │
         ▼
┌─────────────────────────────────────────┐
│  converter.py  [SUBPROCESS BOUNDARY]    │
│                                         │
│  ┌───────────────┐   ┌───────────────┐  │
│  │ Docling       │──▶│ extractor.py  │  │
│  │ conversion    │   │ element types │  │
│  │ (PDF/DOCX/    │   │ hierarchy     │  │
│  │  HTML/TXT)    │   │ tiers 1-3    │  │
│  └───────────────┘   └──────┬────────┘  │
│                             │           │
│                    JSON output file      │
└─────────────────────────────┬───────────┘
                              │
                              ▼
┌───────────────────────┐
│     worker.py         │  Reads JSON output
│                       │  Uploads to object storage
│                       │  Creates in-toto signed link
│                       │  Emits NATS audit event
│                       │  Updates document status → COMPLETED
└───────────────────────┘
```

### Module responsibilities

| Module | Responsibility | Approx lines |
|--------|---------------|--------------|
| `server.py` | gRPC server lifecycle, OTel init, graceful shutdown, NATS connection | ~200 |
| `service.py` | RPC handlers: ConvertDocument, GetDocument, ListDocuments. Request validation, tenant isolation, document_id generation | ~250 |
| `worker.py` | Async conversion worker: job lifecycle, subprocess dispatch, storage upload, attestation, audit events | ~300 |
| `security.py` | SSRF prevention, format validation, URL fetching, size limits (reusable) | ~350 |
| `converter.py` | Subprocess isolation, rlimit config, Docling invocation, temp dir management | ~250 |
| `extractor.py` | Docling doc parsing, element type classification, hierarchy, tiers 1-3 | ~500 |
| `storage.py` | Object storage interface: S3-compatible + local filesystem fallback | ~150 |
| `attestation.py` | In-toto signed link generation, key management, FIPS algorithm enforcement | ~200 |
| `audit.py` | NATS JetStream publisher with provenance headers, fallback to structured logging | ~150 |
| `config.py` | Pydantic Settings class, env var mapping, defaults | ~120 |
| `state.py` | Document state interface + in-memory and Redis backends | ~200 |
| `dispatch.py` | Worker dispatch interface + asyncio pool and NATS JetStream backends | ~200 |

### Error mapping

| Condition | gRPC Status / ErrorCode |
|-----------|------------------------|
| Missing TenantContext or tenant_id | `INVALID_ARGUMENT` / `ERROR_CODE_INVALID_ARGUMENT` |
| Missing source (no content or source_uri) | `INVALID_ARGUMENT` |
| Format not in allowlist | `INVALID_ARGUMENT` |
| Format mismatch (declared vs detected) | `INVALID_ARGUMENT` |
| Document too large | `RESOURCE_EXHAUSTED` / `ERROR_CODE_RESOURCE_EXHAUSTED` |
| Blocked URL (SSRF) | `INVALID_ARGUMENT` |
| Document not found (GetDocument) | `NOT_FOUND` / `ERROR_CODE_NOT_FOUND` |
| Processing timeout | `DEADLINE_EXCEEDED` |
| Docling conversion failure | `INTERNAL` / `ERROR_CODE_INTERNAL` |
| Subprocess crash | `INTERNAL` |
| Storage upload failure | `INTERNAL` / `ERROR_CODE_UNAVAILABLE` |

## Execution Model

The service supports two execution modes via configuration, sharing the same conversion pipeline. This enables easy local development while targeting Kubernetes for production.

### Embedded mode (`EXECUTION_MODE=embedded`, default)

Single process: gRPC server + in-process async workers. No external infrastructure beyond storage.

```
┌─────────────────────────────────────┐
│          Single process             │
│                                     │
│  gRPC server ──► asyncio worker     │
│       │            pool             │
│       │              │              │
│  in-memory      subprocess          │
│  state store    (Docling)           │
└─────────────────────────────────────┘
```

- `asyncio.create_task()` dispatches conversions
- In-memory dict for document state (keyed by `(tenant_id, document_id)`)
- Worker pool capped at `MAX_CONCURRENT_CONVERSIONS` (default 4)
- On process restart, in-flight jobs are lost (acceptable — caller can resubmit)
- Ideal for: local development, testing, single-node deployments, CI

### Distributed mode (`EXECUTION_MODE=distributed`)

Thin gRPC dispatcher + external work queue + separate worker processes. Workers are ephemeral — they can be Kubernetes Jobs, scale-to-zero containers, or long-lived pods.

```
┌──────────────────┐     ┌──────────────┐     ┌──────────────────┐
│  gRPC dispatcher │────▶│  Work queue   │────▶│  Worker (N)      │
│  (thin, cheap)   │     │  (NATS JS)   │     │  (heavy, ephemeral)│
│                  │     │              │     │                  │
│  external state  │     └──────────────┘     │  subprocess      │
│  (Redis)         │◀────────────────────────│  (Docling)       │
└──────────────────┘     status updates       └──────────────────┘
```

- Dispatcher validates requests, assigns document_id, enqueues to NATS JetStream, returns PENDING
- Workers pull from queue, convert, upload to storage, update state, create attestation
- External state backend (Redis) for document records — survives restarts, supports multiple dispatcher replicas
- Workers scale independently from the dispatcher
- Ideal for: Kubernetes, production, multi-tenant at scale

### Pluggable backends

Two interfaces with swappable implementations:

| Interface | Embedded impl | Distributed impl |
|-----------|---------------|-------------------|
| `StateBackend` | In-memory dict | Redis |
| `WorkerBackend` | asyncio task pool | NATS JetStream consumer |

The conversion pipeline itself (`security.py` → `converter.py` → `extractor.py` → `storage.py` → `attestation.py` → `audit.py`) is identical in both modes. Only the dispatch and state management differ.

### Job lifecycle (both modes)

1. **ConvertDocument** validates TenantContext + input, assigns `document_id` (UUID v4)
2. Creates `Document` record with `JOB_STATUS_PENDING` in state backend
3. Returns `ConvertDocumentResponse{document_id, JOB_STATUS_PENDING}` immediately
4. Worker (in-process or external) picks up the job:
   - Sets status to `JOB_STATUS_RUNNING`
   - Fetches URL or writes bytes to temp dir
   - Validates format (byte sniffing)
   - Forks subprocess for Docling conversion + extraction
   - Uploads structured JSON to object storage
   - Creates in-toto signed link (materials → products)
   - Emits NATS audit event
   - Sets status to `JOB_STATUS_COMPLETED` (or `JOB_STATUS_FAILED` with `Error` details)
5. **GetDocument** returns current `Document` state including `structured_json_uri` when complete
6. **ListDocuments** returns tenant-scoped document list with pagination

### Concurrency

- Embedded: worker pool capped at `MAX_CONCURRENT_CONVERSIONS`
- Distributed: concurrency determined by number of worker replicas × 1 conversion per worker
- Both modes: subprocess-per-conversion provides natural isolation
- Backpressure: embedded mode queues excess jobs; distributed mode uses NATS JetStream delivery semantics

## Multi-Tenant Isolation

- Every RPC requires `TenantContext` with non-empty `tenant_id`
- Requests without valid TenantContext are rejected with `INVALID_ARGUMENT`
- Object storage paths are tenant-prefixed: `<tenant_id>/documents/<document_id>/output.json`
- NATS audit subjects are tenant-scoped: `tenant.<tenant_id>.audit.ingestion`
- Document state is keyed by `(tenant_id, document_id)` — no cross-tenant access
- ListDocuments only returns documents for the requesting tenant
- Attestation byproducts include `tenant_id`

## Security Hardening

### SSRF Prevention (ported from `url_fetcher.py`)

- Allowlisted schemes: `https`, `http` only
- DNS resolution before connect — all resolved IPs checked against blocked ranges
- Blocked ranges: loopback, link-local, multicast, private (RFC 1918), carrier-grade NAT (RFC 6598), IPv6 unique-local (`fc00::/7`)
- Redirect following with re-validation at every hop (max configurable, default 5)
- Configurable download size limit (default 100MB)
- Per-request timeout (default 30s)
- Content type determined by byte-sniffing, then Content-Type header, then URL extension

### Format Validation

- Byte-sniffing first: PDF magic `%PDF-`, HTML `<html`/`<!doctype`, ZIP `PK\x03\x04` for DOCX
- Content-Type header second (URL fetches)
- ContentMetadata.mime_type third
- Mismatch between declared and detected raises `INVALID_ARGUMENT`
- Strict allowlist: `pdf`, `docx`, `html`, `txt` (configurable via `ALLOWED_FORMATS`)
- ContentMetadata.sha256 verified against actual content hash when provided

### Resource Limits

| Limit | Default | Env var |
|-------|---------|---------|
| Max document size | 50MB | `MAX_DOCUMENT_SIZE` |
| Max processing time | 300s | `MAX_PROCESSING_TIME` |
| Max pages | 1000 | `MAX_PAGES` |
| Max text output | 10MB | `MAX_EXTRACTION_LENGTH` |
| Max URL download | 100MB | `MAX_URL_BYTES` |
| Max concurrent conversions | 4 | `MAX_CONCURRENT_CONVERSIONS` |

### Subprocess Isolation

- Each conversion forks a child process via `subprocess.run()` with list args (no shell)
- Configurable rlimits via env vars — any limit can be set to 0 for unlimited:
  - `RLIMIT_AS_MB` (virtual memory): default 4096MB
  - `RLIMIT_CPU` (CPU seconds): default matches `MAX_PROCESSING_TIME`
  - `RLIMIT_FSIZE_MB` (output file size): default 100MB
- Isolated temp directory per conversion, cleaned up on completion or failure
- Minimal environment passed to child (`PATH` only)
- Child runs both Docling conversion and element extraction, writes structured JSON to a file in the temp dir
- Parent process reads the JSON, uploads to storage, and creates attestation
- Temp file paths are generated — no user-controlled filenames reach the filesystem

## In-toto Attestation

### Per-conversion signed links

Each successful conversion creates an in-toto `SignedLink` matching the Go `pkg/attestation/` patterns:

- **Step name**: `ingestion.convert`
- **Materials**: Input document hash (`{source_uri_or_content_hash: {sha256: <hash>}}`)
- **Products**: Output structured JSON hash (`{structured_json_uri: {sha256: <hash>}}`)
- **Byproducts** (enriched, matching Go pattern):
  - `trace_id`: OTel trace ID from active span
  - `span_id`: OTel span ID
  - `tenant_id`: From TenantContext
  - `hostname`: Service instance hostname
  - `timestamp`: ISO 8601
  - `return_value`: 0 on success
  - `detected_format`: Actual format detected
  - `element_count`: Number of elements extracted
  - `processing_time_ms`: Conversion duration

### Key management

- **Ephemeral keys** (default): ECDSA P-256 key pair generated at service startup, held in memory. Appropriate for development and embedded mode.
- **File-based keys**: Load from `ATTESTATION_PRIVATE_KEY_PATH` / `ATTESTATION_PUBLIC_KEY_PATH`. For production deployments where keys are provisioned by infrastructure.
- **FIPS mode**: When `FIPS_MODE=true`, only ECDSA P-256/P-384/P-521 algorithms are permitted. RSA and Ed25519 are rejected.

### Attestation-trace bridge

- `AuditMetadata.correlation_id` is set to the OTel trace ID
- `ProvenanceMetadata.attestation_link_id` references the signed link's key ID + step name
- Bidirectional: given a trace ID, find the attestation; given an attestation, find the trace

### Storage

Signed links are stored alongside conversion output:
`<tenant_id>/documents/<document_id>/attestation.link.json`

### Dependencies

- `in-toto` (v3.x) — Link/Metadata creation and signing
- `securesystemslib` (v1.x) — Signer backends (SSlibSigner for ECDSA)

## NATS Audit Events

### Event structure

Each completed conversion emits an audit event to NATS JetStream:

- **Subject**: `tenant.<tenant_id>.audit.ingestion`
- **Headers** (matching Go `audit-streams.md` pattern):
  - `X-Trace-Id`: OTel trace ID
  - `X-Span-Id`: OTel span ID
  - `X-Tenant-Id`: Tenant ID
  - `X-Timestamp`: ISO 8601
  - `X-Content-SHA256`: SHA-256 of the event payload
- **Payload**: JSON with document_id, status, metadata, attestation_link_id

### Connection management

- NATS connection is optional — when `NATS_URL` is unset, audit events are logged via structured logging instead (degraded but functional)
- Connection with TLS/mTLS when `NATS_TLS_CERT` / `NATS_TLS_KEY` are provided
- Automatic reconnection with exponential backoff
- JetStream publish with at-least-once delivery

### Dependencies

- `nats-py` — NATS client with JetStream support

## Observability

### Tracing (OpenTelemetry)

- gRPC server interceptor via `opentelemetry-instrumentation-grpc`
- W3C `traceparent`/`tracestate` extracted from incoming gRPC metadata
- Resource attributes: `service.name`, `service.version`, `service.instance.id`, `host.name`
- Internal child spans:
  - `ingestion.validate` — request validation + format check
  - `ingestion.fetch_url` — URL download (URL path only)
  - `ingestion.convert` — subprocess Docling conversion
  - `ingestion.extract` — element extraction + hierarchy building
  - `ingestion.upload` — object storage upload
  - `ingestion.attest` — in-toto link creation + signing
  - `ingestion.audit` — NATS event publish
- Span attributes: `document.id`, `document.format`, `document.size_bytes`, `document.page_count`, `document.element_count`, `tenant.id`
- Trace ID embedded in attestation byproducts and audit event headers
- Graceful degradation: no-op when `OTEL_EXPORTER_OTLP_ENDPOINT` is unset

### Metrics (Prometheus)

- Exposed on `METRICS_PORT` (default 9090) via Prometheus exporter
- Counters: `ingestion_requests_total{format,status,tenant_id}`, `ingestion_errors_total{error_type}`
- Histograms: `ingestion_duration_seconds{format}`, `ingestion_document_size_bytes`
- Gauges: `ingestion_active_conversions`, `ingestion_queue_depth`

### Logging

- Structured JSON via `python-json-logger`
- Trace ID and span ID injected into every log line
- Tenant ID included in log context
- Configurable level via `LOG_LEVEL` (default `info`)

## Container Images

### Standard Dockerfile

Multi-stage build:
- **Builder stage**: `python:3.12-slim`, install deps via pip, generate proto stubs
- **Runtime stage**: `python:3.12-slim`, copy installed packages + source
  - Non-root user (UID 1000)
  - HEALTHCHECK via gRPC health check
  - Expose `GRPC_PORT` (8080) and `METRICS_PORT` (9090)

### FIPS Dockerfile

Multi-stage build:
- **Builder stage**: `registry.access.redhat.com/ubi9/python-312`, FIPS OpenSSL
- **Runtime stage**: `registry.access.redhat.com/ubi9-minimal`
  - FIPS mode inherited from host kernel
  - `FIPS_MODE=true` env var set — enforces ECDSA-only attestation signing
  - Same non-root user, healthcheck, port pattern
  - Larger image but meets FedRAMP/FIPS requirements

### docker-compose.yml (development)

```yaml
services:
  ingestion:
    build: .
    ports:
      - "8080:8080"
      - "9090:9090"
    environment:
      LOG_LEVEL: debug
      STORAGE_BACKEND: filesystem
      STORAGE_PATH: /data/documents
    volumes:
      - ingestion-data:/data/documents

  nats:
    image: nats:latest
    command: ["--jetstream"]
    ports:
      - "4222:4222"

  jaeger:
    image: jaegertracing/all-in-one:latest
    ports:
      - "16686:16686"
      - "4317:4317"
    environment:
      COLLECTOR_OTLP_ENABLED: "true"

volumes:
  ingestion-data:
```

## Environment Configuration

All configuration via environment variables, parsed by Pydantic Settings at startup. Bad config fails fast.

### Service

| Variable | Default | Description |
|----------|---------|-------------|
| `GRPC_PORT` | `8080` | gRPC listen port |
| `METRICS_PORT` | `9090` | Prometheus metrics port |
| `LOG_LEVEL` | `info` | Logging level |
| `EXECUTION_MODE` | `embedded` | `embedded` or `distributed` |
| `MAX_CONCURRENT_CONVERSIONS` | `4` | Worker pool size (embedded mode) |

### State Backend (distributed mode)

| Variable | Default | Description |
|----------|---------|-------------|
| `STATE_BACKEND` | `memory` | `memory` (embedded) or `redis` (distributed) |
| `REDIS_URL` | _(none)_ | Redis connection URL |

### Resource Limits

| Variable | Default | Description |
|----------|---------|-------------|
| `MAX_DOCUMENT_SIZE` | `52428800` (50MB) | Max input size bytes |
| `MAX_PROCESSING_TIME` | `300` | Conversion timeout seconds |
| `MAX_PAGES` | `1000` | Max pages to process |
| `MAX_EXTRACTION_LENGTH` | `10485760` (10MB) | Max text output bytes |
| `ALLOWED_FORMATS` | `pdf,docx,html,txt` | Comma-separated allowlist |

### Subprocess Isolation

| Variable | Default | Description |
|----------|---------|-------------|
| `RLIMIT_AS_MB` | `4096` | Virtual memory limit (0=unlimited) |
| `RLIMIT_CPU` | `300` | CPU seconds (0=unlimited) |
| `RLIMIT_FSIZE_MB` | `100` | Output file size (0=unlimited) |

### URL Fetching

| Variable | Default | Description |
|----------|---------|-------------|
| `MAX_URL_BYTES` | `104857600` (100MB) | Max download size |
| `URL_TIMEOUT` | `30` | Fetch timeout seconds |
| `MAX_REDIRECTS` | `5` | Max redirect hops |

### Object Storage

| Variable | Default | Description |
|----------|---------|-------------|
| `STORAGE_BACKEND` | `filesystem` | `filesystem` or `s3` |
| `STORAGE_PATH` | `/data/documents` | Filesystem storage root |
| `S3_ENDPOINT` | _(none)_ | S3-compatible endpoint |
| `S3_BUCKET` | `crosscodex-documents` | S3 bucket name |
| `S3_ACCESS_KEY` | _(none)_ | S3 access key |
| `S3_SECRET_KEY` | _(none)_ | S3 secret key |
| `S3_REGION` | `us-east-1` | S3 region |

### Attestation

| Variable | Default | Description |
|----------|---------|-------------|
| `ATTESTATION_ENABLED` | `true` | Enable in-toto attestation |
| `ATTESTATION_PRIVATE_KEY_PATH` | _(none)_ | Key file (empty=ephemeral) |
| `ATTESTATION_PUBLIC_KEY_PATH` | _(none)_ | Public key file |
| `FIPS_MODE` | `false` | ECDSA-only signing |

### NATS

| Variable | Default | Description |
|----------|---------|-------------|
| `NATS_URL` | _(none)_ | NATS server (empty=log fallback) |
| `NATS_TLS_CERT` | _(none)_ | Client certificate path |
| `NATS_TLS_KEY` | _(none)_ | Client key path |
| `NATS_TLS_CA` | _(none)_ | CA certificate path |

### OpenTelemetry

| Variable | Default | Description |
|----------|---------|-------------|
| `OTEL_EXPORTER_OTLP_ENDPOINT` | _(none)_ | Collector (no-op if unset) |
| `OTEL_EXPORTER_OTLP_PROTOCOL` | `grpc` | `grpc` or `http/protobuf` |
| `OTEL_SERVICE_NAME` | `crosscodex-ingestion` | Service name in traces |
| `OTEL_TRACES_SAMPLER` | `parentbased_always_on` | Sampling strategy |

## Repository Structure

```
crosscodex-ingestion/
├── proto/
│   └── crosscodex/
│       └── v1/
│           ├── ingestion.proto    # Copied from monorepo
│           └── common.proto       # Copied from monorepo
├── src/
│   └── crosscodex_ingestion/
│       ├── __init__.py
│       ├── __main__.py            # Entry point
│       ├── server.py              # gRPC server lifecycle
│       ├── service.py             # IngestionService RPC handlers
│       ├── worker.py              # Async conversion worker
│       ├── converter.py           # Subprocess Docling wrapper
│       ├── extractor.py           # Element extraction + hierarchy
│       ├── security.py            # SSRF, format validation, URL fetch
│       ├── storage.py             # Object storage (S3 / filesystem)
│       ├── attestation.py         # In-toto signed links
│       ├── audit.py               # NATS audit event publisher
│       ├── state.py               # State interface + memory/Redis backends
│       ├── dispatch.py            # Worker dispatch interface + asyncio/NATS backends
│       ├── config.py              # Pydantic Settings
│       └── proto/                 # Generated stubs (gitignored)
│           └── crosscodex/
│               └── v1/
│                   ├── ingestion_pb2.py
│                   ├── ingestion_pb2_grpc.py
│                   ├── common_pb2.py
│                   └── common_pb2_grpc.py
├── tests/
│   ├── conftest.py
│   ├── unit/
│   │   ├── test_security.py
│   │   ├── test_converter.py
│   │   ├── test_extractor.py
│   │   ├── test_service.py
│   │   ├── test_worker.py
│   │   ├── test_storage.py
│   │   ├── test_attestation.py
│   │   ├── test_audit.py
│   │   ├── test_state.py
│   │   └── test_config.py
│   ├── integration/
│   │   ├── test_conversion_e2e.py
│   │   ├── test_health.py
│   │   ├── test_security_e2e.py
│   │   ├── test_attestation_e2e.py
│   │   └── test_audit_e2e.py
│   ├── venom/
│   │   └── container_e2e.venom.yml
│   └── fixtures/
│       └── documents/
│           ├── sample.pdf
│           ├── sample.docx
│           ├── sample.html
│           └── sample.txt
├── Dockerfile
├── Dockerfile.fips
├── docker-compose.yml
├── pyproject.toml
├── requirements.txt
├── Taskfile.yml
├── .gitignore
└── README.md
```

## Testing Strategy

### Unit tests (`tests/unit/`)

| Test file | Coverage |
|-----------|----------|
| `test_security.py` | SSRF prevention (blocked IPs, redirect re-validation, scheme enforcement), format validation (byte sniffing, mismatch detection), size limits, URL fetching edge cases |
| `test_converter.py` | Subprocess spawning, rlimit configuration (including unlimited), temp dir lifecycle, timeout handling, child process failure modes |
| `test_extractor.py` | Element type classification, hierarchy building, tier 1-3 pattern detection, table/list structure, page number tracking |
| `test_service.py` | gRPC request validation, tenant isolation, error mapping, bytes vs URL dispatch, GetDocument/ListDocuments |
| `test_worker.py` | Job lifecycle (PENDING→RUNNING→COMPLETED/FAILED), concurrency limits, error handling, storage upload, attestation creation |
| `test_storage.py` | Filesystem and S3 backends, tenant-prefixed paths, upload/download, error handling |
| `test_attestation.py` | Signed link generation, key management (ephemeral + file-based), FIPS algorithm enforcement, byproduct enrichment, trace correlation |
| `test_audit.py` | NATS event publishing, header construction, fallback to logging, reconnection |
| `test_state.py` | State backend interface, in-memory impl, Redis impl (mocked), tenant isolation, concurrent access |
| `test_dispatch.py` | Dispatch backend interface, asyncio pool impl, NATS impl (mocked), job handoff |
| `test_config.py` | Env var parsing, defaults, validation errors, format allowlist parsing, execution mode switching |

Every test file includes negative cases.

### Integration tests (`tests/integration/`)

| Test file | Coverage |
|-----------|----------|
| `test_conversion_e2e.py` | Full async flow: ConvertDocument → poll GetDocument → verify output in storage, for each format |
| `test_health.py` | Health check (via gRPC health v1 or admin proto) |
| `test_security_e2e.py` | Oversized document rejected, blocked URL rejected, format mismatch rejected, missing tenant rejected |
| `test_attestation_e2e.py` | Conversion produces valid signed link, verifiable with public key, trace ID present |
| `test_audit_e2e.py` | NATS event received with correct headers and payload |

### Container tests (`tests/venom/`)

Venom YAML suites run against the containerized service via `podman-compose` (ingestion + NATS + storage). Both standard and FIPS images tested.

### Test infrastructure

- `pytest` with `pytest-cov` for coverage, `pytest-asyncio` for async tests
- `grpcio-testing` for unit-level gRPC tests
- `podman-compose` for container integration tests
- NATS test server (embedded or container)
- All artifacts routed to `.test-output/` (gitignored)
- Taskfile targets: `task test` (unit), `task test:integration`, `task test:container`, `task test MODE=llm`

## Build System (Taskfile.yml)

| Target | Description |
|--------|-------------|
| `task proto` | Generate Python stubs from proto files |
| `task lint` | Run ruff + mypy |
| `task test` | Unit tests (pytest) |
| `task test:integration` | Integration tests (requires Docling + NATS) |
| `task test:container` | Container tests (requires podman) |
| `task test:all` | All test suites |
| `task build` | Build Python package |
| `task docker` | Build standard container image |
| `task docker:fips` | Build FIPS container image |
| `task run` | Run service locally |
| `task clean` | Remove generated files + test artifacts |

## Dependencies

### Runtime

| Package | Purpose |
|---------|---------|
| `grpcio` | gRPC server |
| `protobuf` | Proto message serialization |
| `docling` | Document conversion engine |
| `pydantic-settings` | Configuration from env vars |
| `requests` | URL fetching |
| `python-json-logger` | Structured logging |
| `opentelemetry-api` | OTel tracing API |
| `opentelemetry-sdk` | OTel tracing SDK |
| `opentelemetry-instrumentation-grpc` | gRPC auto-instrumentation |
| `opentelemetry-exporter-otlp` | OTLP exporter |
| `prometheus-client` | Metrics exporter |
| `in-toto` | Signed link generation |
| `securesystemslib[crypto]` | ECDSA signing (add `[sigstore]` for Sigstore) |
| `nats-py` | NATS JetStream client (audit + distributed dispatch) |
| `boto3` | S3-compatible object storage |
| `redis` | State backend for distributed mode |

### Development

| Package | Purpose |
|---------|---------|
| `pytest` | Test runner |
| `pytest-cov` | Coverage reporting |
| `pytest-asyncio` | Async test support |
| `grpcio-testing` | gRPC unit test support |
| `grpcio-tools` | Proto stub generation |
| `mypy-protobuf` | Type stubs for proto code |
| `ruff` | Linter + formatter |
| `mypy` | Type checking |

## Migration Path to crosscodex-proto

When the monorepo publishes `crosscodex-proto`:

1. Add `crosscodex-proto>=0.1.0` to `pyproject.toml` dependencies
2. Remove `grpcio-tools` from dev dependencies
3. Remove `proto/` directory and `task proto` target
4. Delete generated files in `src/crosscodex_ingestion/proto/`
5. Update imports: none needed if the package uses the same `crosscodex.v1` namespace
