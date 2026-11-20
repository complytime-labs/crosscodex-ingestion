# Environment Variables

For operators who deploy and configure the service. Back to the [README](../../README.md).

| Variable                                                                  | Default             | Description                                                                                                                                                                                                                      |
|---------------------------------------------------------------------------|---------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `DOCLING_ARTIFACTS_PATH`                                                  | (required)          | Directory with pre-downloaded Docling models; server refuses to start without it                                                                                                                                                 |
| `GRPC_PORT`                                                               | `8080`              | gRPC server port                                                                                                                                                                                                                 |
| `METRICS_PORT`                                                            | `9090`              | Prometheus metrics port; startup fails if it cannot be bound                                                                                                                                                                                                        |
| `LOG_LEVEL`                                                               | `info`              | Log level                                                                                                                                                                                                                        |
| `EXECUTION_MODE`                                                          | `embedded`          | `embedded` or `distributed` (NATS)                                                                                                                                                                                               |
| `MAX_CONCURRENT_CONVERSIONS`                                              | `4`                 | Conversions running at once (each is a separate subprocess)                                                                                                                                                                      |
| `SHUTDOWN_GRACE_SECONDS`                                                  | `25`                | Seconds SIGTERM gives in-flight conversions to finish (`>= 0`); see "Graceful shutdown"                                                                                                                                          |
| `STATE_BACKEND`                                                           | `memory`            | `memory` or `redis`                                                                                                                                                                                                              |
| `STORAGE_BACKEND`                                                         | `filesystem`        | `filesystem` or `s3`                                                                                                                                                                                                             |
| `STORAGE_PATH`                                                            | `/data/documents`   | Filesystem storage root                                                                                                                                                                                                          |
| `ALLOWED_FORMATS`                                                         | `pdf,docx,html,txt,json,csv,xlsx` | Comma-separated list of allowed formats (`json`, `csv` and `xlsx` also need a mapping)                                                                                                                             |
| `MAPPINGS_PATH`                                                           | (empty)             | Directory of extra structured-source mappings (`*.yaml`); every mapping is validated at startup (see [Structured sources](structured-sources.md), which also lists the structured input limits)                                                         |
| `HTML_PROFILES_PATH`                                                      | (empty)             | Directory of extra HTML profiles (`*.yaml`); every profile is validated at startup (see [HTML profiles](output-format.md#html-profiles))                                                                                                                         |
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
| `NATS_URL`                                                                | (empty)             | NATS server URL (e.g., `nats://localhost:4222`). Empty: audit events go to the log. Set: see [NATS](#nats); required for `distributed`                                                                                           |
| `NATS_TLS_CERT`, `NATS_TLS_KEY`, `NATS_TLS_CA`                            | (empty)             | TLS to NATS (PEM files); see [NATS](#nats) for the valid combinations                                                                                                                                                            |
| `REDIS_URL`                                                               | (empty)             | Redis URL for state backend                                                                                                                                                                                                      |
| `S3_ENDPOINT`, `S3_BUCKET`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_REGION` | (see `config.py`)   | S3 storage backend settings                                                                                                                                                                                                      |
| `OTEL_EXPORTER_OTLP_ENDPOINT`                                             | (empty)             | OTLP endpoint for OpenTelemetry traces; tracing is off when empty                                                                                                                                                                |
| `OTEL_EXPORTER_OTLP_PROTOCOL`, `OTEL_SERVICE_NAME` | (see `config.py`) | OTLP protocol (`grpc`) and service name (`crosscodex-ingestion`) for tracing |
| `OTEL_TRACES_SAMPLER` | `parentbased_always_on` | One of `always_on`, `always_off`, `traceidratio`, `parentbased_always_on`, `parentbased_always_off`, `parentbased_traceidratio`; any other value is a startup error |
| `OTEL_TRACES_SAMPLER_ARG` | (empty, meaning `1.0`) | Sampling ratio from 0 to 1 for the `traceidratio` and `parentbased_traceidratio` samplers; ignored by the others |

See `src/crosscodex_ingestion/config.py` for the full list.

## Command line

`python -m crosscodex_ingestion --help` prints usage and `--version` prints `crosscodex-ingestion <version>`;
both exit without starting the server. There are no other flags: the service is configured only through the
environment variables above.

## Startup checks

The server checks all of its configuration before it opens a port: the models directory, mappings, HTML
profiles, attestation keys, gRPC and NATS TLS, `NATS_URL` in distributed mode, the sampler settings and
`METRICS_PORT`. It reports every problem at once and exits with a non-zero status:

```text
Configuration errors:
  - DOCLING_ARTIFACTS_PATH is not set ...
  - OTEL_TRACES_SAMPLER 'bogus' is not one of: always_on, ...
```

A single problem is reported as `Configuration error: <message>`. A `METRICS_PORT` that cannot be bound is a
startup error: the service does not run without its metrics. Every log line, including these checks'
warnings, is JSON on stderr.

## Metrics

Prometheus metrics are served on `METRICS_PORT`.

- `ingestion_requests_total{format,status}`: `format` is the format of the declared MIME type (`pdf`, `html`,
  `docx`, `txt`, `json`, `csv`, `xlsx`), `mapping` when the request names a mapping, or `unknown`. Caller text
  such as `format_version` is never a label value.
- `ingestion_document_size_bytes` buckets: 1 KB, 10 KB, 100 KB, 1 MB, 10 MB, 50 MB, 100 MB.
- `ingestion_duration_seconds` buckets: 1, 5, 15, 30, 60, 120, 300, 600, 1200, 1800 seconds.

## Limits and resource sizing

The defaults are sized from measurements of real documents (Docling offline, models pre-downloaded):

| Document         | Pages | Wall time | CPU time | VmPeak  | RSS peak |
|------------------|-------|-----------|----------|---------|----------|
| NIST CSF 2.0     | 32    | 44 s      | 186 s    | ~40 GB  | 2.7 GB   |
| NIST SP 800-53r5 | 492   | 807 s     | 3221 s   | 43.2 GB | 4.3 GB   |

- **Time**: Docling uses several threads, and `RLIMIT_CPU` counts CPU time across all threads (CPU is
  roughly 4-6x wall time). `MAX_PROCESSING_TIME=3600` and `RLIMIT_CPU=21600` leave headroom for documents
  several times larger than SP 800-53r5. A conversion that hits `RLIMIT_CPU` fails with a message naming
  the limit and its value.
- **Memory**: `RLIMIT_AS` bounds *virtual address space only*. Importing torch alone needs more than 4 GB of
  address space and conversions peak at ~40 GB `VmPeak` while using only a few GB resident, so the default is
  64 GB. It is not a memory cap. **The real memory bound is the container memory limit**: allow at least
  6 GB per concurrent conversion (`MAX_CONCURRENT_CONVERSIONS`). A worker killed by `SIGKILL` (most likely
  the OOM killer) or `SIGSEGV` (most likely `RLIMIT_AS_MB` set too low) is logged with that signal and
  its likely cause.
- **Pages and output size**: `MAX_PAGES` and `MAX_EXTRACTION_LENGTH` fail the conversion with a clear
  message instead of truncating.

## Graceful shutdown

```mermaid
%%{init: {'theme': 'base', 'themeVariables': {
  'primaryColor': '#2f6dab',
  'primaryTextColor': '#1e1e1e',
  'primaryBorderColor': '#7c8ba1',
  'lineColor': '#7c8ba1',
  'edgeLabelBackground': '#eef2f8',
  'tertiaryColor': 'transparent',
  'tertiaryTextColor': '#7c8ba1',
  'tertiaryBorderColor': '#7c8ba1',
  'clusterBkg': 'transparent',
  'clusterBorder': '#7c8ba1',
  'titleColor': '#7c8ba1',
  'noteBkgColor': '#eef2f8',
  'noteTextColor': '#1e1e1e',
  'fontFamily': 'system-ui, sans-serif'
}, 'themeCSS': '.node .nodeLabel{color:#ffffff!important;fill:#ffffff!important;}'}}%%
stateDiagram-v2
  [*] --> Waiting
  Waiting --> Running
  Waiting --> Failed : SIGTERM while waiting for a slot
  Running --> Completed : finishes within SHUTDOWN_GRACE_SECONDS
  Running --> FailedUnavailable : grace expires, subprocess SIGKILLed
  Running --> StuckRunning : runtime stop timeout shorter than grace plus cleanup
  Failed --> [*]
  Completed --> [*]
  FailedUnavailable --> [*]
  StuckRunning --> [*]
  state "FAILED" as Failed
  state "COMPLETED" as Completed
  state "FAILED, UNAVAILABLE, audit event published" as FailedUnavailable
  state "stays RUNNING" as StuckRunning
```

On SIGTERM or SIGINT the service stops accepting RPCs and then drains conversions within a single budget of
`SHUTDOWN_GRACE_SECONDS` (default 25):

1. Documents still waiting for a conversion slot are marked `FAILED` immediately.
2. Running conversions may finish within the grace period and complete normally.
3. Conversions still running when it expires are cancelled: the Docling subprocess (and anything it spawned) is
   SIGKILLed, and the document ends `FAILED` with error code `UNAVAILABLE` ("The service shut down during
   conversion; resubmit the document."). An audit event is published for each. Resubmitting is safe: conversion
   uses local open-weight models, so a retry costs only CPU time.

Shutdown takes at most `SHUTDOWN_GRACE_SECONDS` plus about 2 s of cleanup. Keep the timeout that the
container runtime waits for before it stops the container above that, or the runtime SIGKILLs the process
mid-cleanup and documents stay `RUNNING`: Kubernetes defaults to 30 s (`terminationGracePeriodSeconds`),
while `podman stop` and `docker stop` default to 10 s. Under podman or docker either
raise the timeout (`--stop-timeout 30`, or `stop_grace_period: 30s` in compose) or lower `SHUTDOWN_GRACE_SECONDS` to
about 7. A 492-page PDF takes about 13.5 minutes, so documents that large are cancelled by any realistic grace
period and must be resubmitted after a restart.

In `distributed` mode this service only publishes jobs to NATS JetStream; the consumer is not part of this
repository, so that worker must redeliver in-flight messages on shutdown (negative acknowledgement).

## NATS

Without `NATS_URL`, audit events are written to the log, and only `embedded` mode is available
(`distributed` without `NATS_URL` stops at startup).

With `NATS_URL` set, audit events go to `tenant.<tenant_id>.audit.ingestion` as a core NATS publish with no
JetStream acknowledgement. To keep them, define a JetStream stream on `tenant.*.audit.ingestion`. Without
such a stream, an event that no subscriber receives is lost. If `NATS_URL` includes credentials
(`nats://user:pass@host`), they are removed from logs and error messages.

The service does not wait for NATS at startup. It opens the gRPC port at once and connects in the
background. A failed attempt is logged as one line, and the wait between attempts doubles from 1 s up to
60 s:

```
NATS at nats://nats:4222 unreachable ([Errno 111] Connect call failed ('10.89.0.5', 4222)); retrying in 4s
```

Each attempt makes two tries of up to 5 s each. Once connected, the client reconnects on its own and never
gives up. While NATS is not connected, before the first connect or during a reconnect, `ConvertDocument`
returns `UNAVAILABLE`:

```
NATS is unavailable; audit events cannot be recorded. Retry later.
```

The message leaves out the NATS address. The service logs that address at WARNING for each rejected request.
Retry such a request after a delay. `GetDocument` and `ListDocuments` keep working.

In `distributed` mode, NATS can drop after this check but before the job is published. In that case the
request also gets `UNAVAILABLE`, and the document record it created is marked `FAILED` with the same message,
so it does not stay `PENDING`. A conversion that is already running when NATS drops is marked `FAILED`
because its audit event cannot be published. The lost audit event is logged at WARNING.

TLS to NATS depends on which `NATS_TLS_*` variables are set:

| Set                                                | Result                                                                   |
|----------------------------------------------------|--------------------------------------------------------------------------|
| none                                               | Plaintext; with a `tls://` URL or a server that requires TLS, nats-py verifies against the system CAs |
| `NATS_TLS_CA`                                      | TLS; the server certificate is verified against that CA                  |
| `NATS_TLS_CERT` and `NATS_TLS_KEY`                 | Mutual TLS; the server certificate is verified against the system CAs    |
| `NATS_TLS_CERT`, `NATS_TLS_KEY` and `NATS_TLS_CA`  | Mutual TLS; the server certificate is verified against that CA           |
| only one of `NATS_TLS_CERT` and `NATS_TLS_KEY`     | Startup fails with `Configuration error: …` naming the missing variable  |

## gRPC authentication

The gRPC port uses mutual TLS by default and the service refuses to start without `GRPC_TLS_CERT`,
`GRPC_TLS_KEY` and `GRPC_TLS_CA` (or an explicit `GRPC_TLS_MODE=off`). A client certificate identifies the
calling *service* (for example the CrossCodex gateway), not a tenant: the service still trusts `tenant_id` in
the request, so only issue client certificates to services that enforce tenant isolation themselves, and list
them in `GRPC_ALLOWED_CLIENTS`. TLS settings use the gRPC and OpenSSL defaults; no cryptographic algorithms are
chosen by this service, so the FIPS image inherits whatever its OpenSSL FIPS provider permits.

The container health check only opens a TCP connection to the gRPC port, because a gRPC channel check would
need a client certificate.

## Dev and test certificates

`task certs` runs `scripts/devpki.py`, which writes a throwaway PKI to `.test-output/certs` (override with
`CERTS_DIR=...`): `ca.pem`/`ca.key`, `server.pem`/`server.key` (SANs `localhost`, `127.0.0.1`, `ingestion`,
`crosscodex-ingestion`), `client.pem`/`client.key` (CN `crosscodex-gateway`) and an ECDSA P-256 attestation pair
(`attestation.key`, `attestation.pub`). Key files are mode 0600. Never use this CA outside development.

- **Stale** means any of: a file missing or unparseable; a leftover temp directory from an interrupted run;
  a key file readable by group/other; `ca.pem` not a CA; the server or client certificate not issued by the CA or missing the
  right extended key usage; a private key not matching its certificate; a required server SAN missing;
  the attestation keys not a pair; or less than 24 hours of validity left (`--min-valid-hours`).
  `task certs` skips work when the set is fresh and `python scripts/devpki.py --verify` reports why it is not.
- **Regeneration replaces the whole set with a new CA**, never individual files, so every previously issued
  client certificate and any verifier holding the old `attestation.pub` must be re-issued or updated.
- Concurrent runs are safe: generation holds a file lock next to the output directory, re-checks inside it, and
  swaps the finished set in with a rename. pytest generates its own PKI in a temporary directory per run.
- `task compose:up` generates the certs and starts `docker-compose.yml` with mutual TLS and attestation on.
  - A one-shot `certs-init` container copies the server certificate, server key and attestation keys into
    the `ingestion-certs` volume, owned by the service user (keys 0400), on every start, so it works for any
    host uid under rootless podman or docker and never serves a stale set.
  - The CA key and client key are not copied.
  - `compose:up` passes `--force-recreate`: podman-compose otherwise keeps containers whose compose config is
    unchanged, so after regenerating the PKI the service would keep trusting the old CA.
  - Verifiers use `.test-output/certs/attestation.pub`; callers use `client.pem`, `client.key` and `ca.pem`.

## Attestation keys

Attestations are signed with the key at `ATTESTATION_PRIVATE_KEY_PATH` and are verifiable with the key at
`ATTESTATION_PUBLIC_KEY_PATH`. There is no ephemeral-key fallback. Generate a pair with, for example:

```bash
openssl ecparam -name prime256v1 -genkey -noout | openssl pkcs8 -topk8 -nocrypt -out attestation.key
openssl pkey -in attestation.key -pubout -out attestation.pub
```

Keys on curves the signer (securesystemslib) does not support, such as secp256k1, are rejected at startup.

To check a conversion against the public key, see [Verifying an attestation](attestation.md).

## Identifiers

`tenant_id` must match `^[a-z][a-z0-9-]{1,50}[a-z0-9]$` (3-52 characters: lowercase letters, digits, hyphens;
starts with a letter, does not end with a hyphen). It is used in storage paths and NATS subjects, so other
values are rejected with `INVALID_ARGUMENT`. `GetDocument` requires the `document_id` returned by
`ConvertDocument` (a UUID).
