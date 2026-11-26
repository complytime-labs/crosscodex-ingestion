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
- Security hardening (SSRF prevention, format validation, resource-limited conversion subprocess)
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
- A container engine and compose tool (for `task compose:up`): `task compose:up` uses the first of
  `podman-compose`, `docker compose` or `podman compose` that it finds. docker users need the compose plugin;
  podman alone is enough to build and run the image.
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
printf '{"tenant_context": {"tenant_id": "default"}, "content": "%s", "metadata": {"mime_type": "text/html"}}' \
  "$(base64 -w0 policy.html)" |
  $G -d @ localhost:8080 crosscodex.v1.IngestionService/ConvertDocument

# Poll until conversionStatus is JOB_STATUS_COMPLETED (or JOB_STATUS_FAILED, with an error)
$G -d '{"tenant_context": {"tenant_id": "default"}, "document_id": "<documentId>"}' \
  localhost:8080 crosscodex.v1.IngestionService/GetDocument
```

The request goes to grpcurl on stdin (`-d @`) because the kernel limits a single execve
argument to 128 KiB (`MAX_ARG_STRLEN`), so any document over about 96 KiB fails with "Argument list too long" when
inlined with `-d "…"`. The `printf … |` form works because `printf` is a shell builtin: no execve, so the limit does not apply.

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

## Upgrading

### Schema 1.4: documents are returned as structure

- **Layout is gone.** A converted PDF, DOCX, HTML or text document no longer returns raw headings and
  paragraphs. It returns the controls the fallback structurer found, with `id`, `role`, `title`, `text` and
  `page` (`title`, `text` and `page` may be `null`), and `metadata.structure_method` (`heading`, `table` or `pattern`). A consumer that read layout
  must read elements with an `id`, and one that validates against the schema needs the 1.4 copy.
- **Prose-only documents now fail.** A document with no headings, tables or numbered lines the structurer
  recognises fails with `ERROR_CODE_INVALID_ARGUMENT` (`No controls found in this <format> document ...`)
  and stores nothing. The message suggests a document mapping, but document mappings are planned (issue #38,
  phase 3) and none exist yet: until then this version cannot convert such a document, and a consumer that
  needs it stays on a pre-1.4 ingestion.
- **New `section` role.** A clause that splits into sub-clauses has role `section`: it is not itself a
  requirement and has clause children.
- **Larger documents.** Document output now repeats text (each node's `content` and `text`; a section's `text`
  includes its clauses), so a document near `max_extraction_length` may now exceed it.
- **The container offline check** (`task test:container`) now converts the NIST excerpt, because the old
  `sample.pdf` is prose-only and fails.

See [Output format](docs/usage/output-format.md#document-structure).

## Docling models

The conversion worker is a resource-limited subprocess (see [Limits](docs/usage/configuration.md)) that runs
with `HF_HUB_OFFLINE=1`, which stops Docling fetching models from Hugging Face, so Docling's layout, table, and
OCR models must already be on disk. `HF_HUB_OFFLINE=1` is not a network block, and the worker is not a
security sandbox: it runs as the same uid as the service, so it can read the attestation signing key. Restrict
the container's network at the container or cluster level (for example `--network=none` or a network policy);
that is the operator's job.

```bash
uv run python scripts/download_models.py <dir>
export DOCLING_ARTIFACTS_PATH=<dir>
```

The server **refuses to start** if `DOCLING_ARTIFACTS_PATH` is unset or not a directory. The container
images download the models at build time (`/opt/docling-models` in the standard image,
`/opt/app-root/docling-models` in the FIPS image) and set `DOCLING_ARTIFACTS_PATH` for you.

`scripts/download_models.py`, which the images use too, is preferred over `docling-tools models download`: the CLI has no revision
option and Docling requests the moving `main` ref for most of its models, so pinning the docling package does
not fix their contents. The script pins each Hugging Face repository to a commit SHA (edit the table in the
script to bump one). RapidOCR checkpoints come from URLs fixed by the installed Docling and RapidOCR versions.
Both images install exactly the versions in `uv.lock`, hash-checked, so the models and Docling stay matched.

## Container Build

```bash
# Standard build (downloads CPU-only torch and the Docling models; slow, image about 3.4 GB)
podman build --format docker -t crosscodex-ingestion:latest .

# FIPS build (UBI9; image about 4.8 GB)
podman build --format docker -t crosscodex-ingestion:fips -f Dockerfile.fips .
```

`--format docker` is required with podman: its default OCI image format silently drops the Dockerfile
`HEALTHCHECK`, so `podman healthcheck run` would report no healthcheck. (`task compose:up` is unaffected:
compose declares its own `healthcheck:`.)

Run the standard image with the keys from `task certs` (run it from the repository root):

```bash
podman run --detach --name crosscodex-ingestion --memory 24g -p 8080:8080 \
  --userns keep-id:uid=1000,gid=1000 \
  --volume "$PWD/.test-output/certs:/certs:ro" \
  --volume crosscodex-data:/data/documents \
  --env GRPC_TLS_MODE=mutual \
  --env GRPC_TLS_CERT=/certs/server.pem \
  --env GRPC_TLS_KEY=/certs/server.key \
  --env GRPC_TLS_CA=/certs/ca.pem \
  --env GRPC_ALLOWED_CLIENTS=crosscodex-gateway \
  --env ATTESTATION_ENABLED=true \
  --env ATTESTATION_PRIVATE_KEY_PATH=/certs/attestation.key \
  --env ATTESTATION_PUBLIC_KEY_PATH=/certs/attestation.pub \
  crosscodex-ingestion:latest
```

- The key files are mode 0600, so they must be readable by the image's uid: 1000 in the standard image, 1001 in
  the FIPS image (use `uid=1001,gid=1001` and `crosscodex-ingestion:fips`). `--userns keep-id:uid=…,gid=…`
  maps your host user onto that uid so the mounted files stay readable. Without it the key is
  unreadable to the service user.
- `--memory 24g` is 6 GB for each of the default `MAX_CONCURRENT_CONVERSIONS=4`. For less memory, lower the
  concurrency to match, for example `--memory 12g --env MAX_CONCURRENT_CONVERSIONS=2`.

Without a volume, converted output is stored inside the container (`/data/documents`) and lost when it is
removed. Mount a volume at `/data/documents` to keep it. The container needs the TLS and attestation key
files mounted (see [gRPC authentication](docs/usage/configuration.md#grpc-authentication) and
[Attestation keys](docs/usage/configuration.md#attestation-keys)); for a throwaway local run add
`-e GRPC_TLS_MODE=off -e ATTESTATION_ENABLED=false` (and drop the `/certs` volume).

`task test:container` builds both images, runs each image's `HEALTHCHECK` against a service started with mutual
TLS and attestation, and converts `tests/fixtures/documents/nist-sp800-171r3-excerpt.pdf` in a container started with
`--network=none`. It needs `podman`, is slow (it downloads torch and the models), and runs in CI on every PR.
If `podman build` fails with "mounting an overlay over build context directory" (podman nested inside another
container), point `TMPDIR` at a directory on a non-overlay filesystem.

## Uninstall / cleanup

First use leaves these behind. Remove what you no longer want:

| Left behind by | What | Remove with |
|---|---|---|
| `task setup`, `task proto` | `.venv`, generated stubs in `src/crosscodex_ingestion/proto` | `task clean` (also removes the items below under `.test-output/`) |
| `task certs` | Dev CA, client, server and attestation keys in `.test-output/certs` | `rm -rf .test-output/certs` |
| `task test:integration` | Pinned Docling models in `.test-output/docling-models` | `rm -rf .test-output/docling-models` |
| Source run | Converted documents under your `STORAGE_PATH`, models under `DOCLING_ARTIFACTS_PATH` | `rm -rf` those directories |
| `podman build` | Images `crosscodex-ingestion:latest`, `:fips`, `:test-standard`, `:test-fips` | `podman rmi <image>` |
| `podman run` example | Container `crosscodex-ingestion` and volume `crosscodex-data` | `podman rm --force crosscodex-ingestion; podman volume rm crosscodex-data` |
| `task compose:up` | Containers `crosscodex-ingestion`, `crosscodex-nats`, `crosscodex-jaeger`, volumes `ingestion-data` and `ingestion-certs`, and the nats and jaeger images | `podman-compose -f docker-compose.yml down --volumes` (or `docker compose -f docker-compose.yml down --volumes`), then `podman rmi` the images |

**`task clean` deletes all of `.test-output/`, including the dev CA and the downloaded models** (a multi-GB
download). Regenerate the CA with `task certs`; any service still running with the old CA will need a restart.

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
5. Build container with `podman build --format docker -t crosscodex-ingestion:latest .`

## Support

- Issues: [GitHub Issues](https://github.com/complytime-labs/crosscodex-ingestion/issues)
- Docling: [Document extraction library](https://github.com/DS4SD/docling)
