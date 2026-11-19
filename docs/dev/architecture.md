# Architecture

For contributors who need to find their way around the code. Back to the [README](../../README.md).

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
flowchart LR
  req["gRPC request"] --> svc["service.py"]
  svc --> disp["dispatch.py"]
  disp -->|embedded| worker["worker.py"]
  disp -->|distributed| nats["NATS JetStream, consumer outside this repo"]
  worker -->|document| conv["converter.py, Docling subprocess"]
  conv --> ext["extractor.py"]
  worker -->|mapping| struct["structured.py, structured subprocess"]
  ext --> check["output_schema.py check"]
  struct --> check
  check --> store["storage.py"]
  store --> state["state.py"]
  worker -.-> audit["audit.py, NATS JetStream audit events"]
  worker -.-> att["attestation.py, in-toto"]
  classDef sysA fill:#2f6dab,color:#ffffff,stroke:#7c8ba1
  classDef sysB fill:#1d7848,color:#ffffff,stroke:#7c8ba1
  classDef sysC fill:#7457b8,color:#ffffff,stroke:#7c8ba1
  classDef sysF fill:#5c6a82,color:#ffffff,stroke:#7c8ba1
  class req,svc,disp sysA
  class worker,conv,ext,struct,check sysB
  class store,state sysC
  class nats,audit,att sysF
```

The service has three primary components:

1. **gRPC Service** (`service.py`, `server.py`): Handles `ConvertDocument`, `GetDocument`, and `ListDocuments` RPCs.
2. **Dispatch Layer** (`dispatch.py`): Routes jobs to either embedded workers or NATS JetStream.
3. **Worker** (`worker.py`): Fetches, validates, converts, extracts, stores, and attests documents.

```
gRPC Request → Dispatch → Worker → [Converter → Extractor | Structured] → Output schema check → Storage → State → Response
```

**Security Modules:**
- `security.py`: SSRF prevention, URL validation, content type detection, resource limit enforcement. URL fetches connect directly and ignore `HTTP_PROXY`/`HTTPS_PROXY`/`ALL_PROXY`: the address actually connected to is checked against the blocked ranges, and through a proxy that check would only see the proxy.
- `converter.py`: Async subprocess-based Docling execution with rlimits and timeout; the subprocess's process group is killed on timeout or cancellation
- `extractor.py`: Structured element extraction from Docling output
- `heading_levels.py`: Heading levels for PDF headings Docling leaves flat, inferred from section numbering
- `html_backend.py`: Docling HTML backend that drops navigation and, on pages with a main landmark, page-level `<header>`/`<footer>` before conversion, and applies the HTML profile a page matches (design: [docs/html-profiles.md](../html-profiles.md))
- `output_schema.py`: Validates every output against the versioned output schema before it is stored (design: [docs/output-schema.md](../output-schema.md))
- `quality.py`: Measures structure signals (`metadata.structure`) from the extracted elements
- `html_profile.py`, `html_profiles/`: Load, validate and register HTML profiles at startup; the packaged profiles are the `*.yaml` files in `html_profiles/` (maintainer notes: [docs/html-profiles.md](../html-profiles.md))
- `mapping.py`: Loads, validates and registers structured-source mappings at startup
- `structured.py`: Reads JSON, CSV and XLSX through a mapping into elements, in the structured worker subprocess (design: [docs/structured-sources.md](../structured-sources.md))

**Supporting Modules:**
- `state.py`: Management of the state of document records (async interface; memory, or Redis via `redis.asyncio`)
- `storage.py`: Blob storage (filesystem or S3)
- `attestation.py`: Generation of metadata for in-toto links
- `audit.py`: Publisher of audit events to NATS JetStream
- `config.py`: Pydantic settings with environment variable overrides
