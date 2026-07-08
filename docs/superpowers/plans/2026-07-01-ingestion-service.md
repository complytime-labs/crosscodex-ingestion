# CrossCodex Ingestion Service Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an async Python gRPC ingestion service that wraps Docling for multi-format document conversion, aligned with the CrossCodex monorepo proto contract.

**Architecture:** Pluggable execution model (embedded/distributed) with a shared conversion pipeline. gRPC service accepts documents (bytes or URL), dispatches async workers that convert via Docling in isolated subprocesses, store output in object storage, create in-toto attestations, and emit NATS audit events. Multi-tenant via TenantContext on every request.

**Tech Stack:** Python 3.12, grpcio, Docling, Pydantic Settings, OpenTelemetry, in-toto, NATS, boto3, Redis, pytest, ruff, mypy

## Global Constraints

- Python >= 3.12
- Proto definitions copied from monorepo `api/proto/crosscodex/v1/` — do NOT modify the proto messages
- All env var config via Pydantic Settings — fail fast on invalid config
- No OSCAL knowledge in any module — pure format conversion
- Subprocess isolation for all Docling invocations — configurable rlimits, 0 = unlimited
- TenantContext required on every data-touching RPC
- Storage paths tenant-prefixed: `<tenant_id>/documents/<document_id>/`
- Test artifacts to `.test-output/`, gitignored
- TDD: write failing test → implement → verify pass → commit
- Security: no shell invocation, no user-controlled filenames, SSRF prevention on all URL fetches
- Reference implementation: `/workspace/OllamaCrosswalker/src/extraction/`

---

### Task 1: Project scaffold + proto codegen

**Files:**
- Create: `pyproject.toml`
- Create: `Taskfile.yml`
- Create: `.gitignore`
- Create: `proto/crosscodex/v1/ingestion.proto`
- Create: `proto/crosscodex/v1/common.proto`
- Create: `src/crosscodex_ingestion/__init__.py`
- Create: `src/crosscodex_ingestion/py.typed`
- Create: `tests/__init__.py`
- Create: `tests/unit/__init__.py`
- Create: `tests/integration/__init__.py`
- Create: `tests/conftest.py`

**Interfaces:**
- Produces: Generated proto stubs at `src/crosscodex_ingestion/proto/crosscodex/v1/ingestion_pb2.py`, `common_pb2.py`, and their `_grpc.py` counterparts. All subsequent tasks import from `crosscodex_ingestion.proto.crosscodex.v1`.

- [ ] **Step 1: Create `.gitignore`**

```gitignore
__pycache__/
*.py[cod]
*.egg-info/
dist/
build/
.eggs/
*.egg
.test-output/
.venv/
src/crosscodex_ingestion/proto/
.mypy_cache/
.ruff_cache/
.pytest_cache/
```

- [ ] **Step 2: Create `pyproject.toml`**

```toml
[project]
name = "crosscodex-ingestion"
version = "0.1.0"
description = "Python gRPC service wrapping Docling for multi-format document conversion"
readme = "README.md"
requires-python = ">=3.12"
dependencies = [
    "grpcio>=1.60.0,<2",
    "protobuf>=4.25.0,<6",
    "docling>=2.0.0",
    "pydantic-settings>=2.0.0,<3",
    "requests>=2.31.0,<3",
    "python-json-logger>=2.0.0,<3",
    "opentelemetry-api>=1.20.0",
    "opentelemetry-sdk>=1.20.0",
    "opentelemetry-instrumentation-grpc>=0.41b0",
    "opentelemetry-exporter-otlp>=1.20.0",
    "prometheus-client>=0.20.0,<1",
    "in-toto>=3.0.0,<4",
    "securesystemslib[crypto]>=1.0.0,<2",
    "nats-py>=2.6.0,<3",
    "boto3>=1.34.0,<2",
    "redis>=5.0.0,<6",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0.0,<9",
    "pytest-cov>=5.0.0,<6",
    "pytest-asyncio>=0.23.0,<1",
    "grpcio-tools>=1.60.0,<2",
    "grpcio-testing>=1.60.0,<2",
    "mypy-protobuf>=3.5.0,<4",
    "ruff>=0.4.0",
    "mypy>=1.8.0,<2",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/crosscodex_ingestion"]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src"]
asyncio_mode = "auto"

[tool.ruff]
line-length = 120
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "W", "I", "UP", "S", "B"]

[tool.mypy]
python_version = "3.12"
warn_return_any = true
warn_unused_configs = true
disallow_untyped_defs = true
```

- [ ] **Step 3: Copy proto files from monorepo**

Copy `ingestion.proto` and `common.proto` verbatim from the monorepo. Add a `go_package` line comment and `option py_generic_services = false;` if not already present. These files must NOT be modified — they are the source of truth from the monorepo.

Fetch via:
```bash
gh api repos/complytime-labs/crosscodex/contents/api/proto/crosscodex/v1/ingestion.proto \
  -H "Accept: application/vnd.github.raw" > proto/crosscodex/v1/ingestion.proto

gh api repos/complytime-labs/crosscodex/contents/api/proto/crosscodex/v1/common.proto \
  -H "Accept: application/vnd.github.raw" > proto/crosscodex/v1/common.proto
```

If `gh` is not authenticated, copy the proto content from the design spec at `docs/superpowers/specs/2026-06-30-ingestion-service-design.md`.

- [ ] **Step 4: Create `Taskfile.yml`**

```yaml
version: '3'

vars:
  PROTO_DIR: proto
  PROTO_OUT: src/crosscodex_ingestion/proto
  PYTHON: python3

tasks:
  setup:
    desc: Install dependencies
    cmds:
      - pip install -e ".[dev]"

  proto:
    desc: Generate Python stubs from proto files
    cmds:
      - mkdir -p {{.PROTO_OUT}}
      - touch {{.PROTO_OUT}}/__init__.py
      - mkdir -p {{.PROTO_OUT}}/crosscodex/__init__.py || true
      - >-
        {{.PYTHON}} -m grpc_tools.protoc
        --proto_path={{.PROTO_DIR}}
        --python_out={{.PROTO_OUT}}
        --grpc_python_out={{.PROTO_OUT}}
        --mypy_out={{.PROTO_OUT}}
        crosscodex/v1/common.proto
        crosscodex/v1/ingestion.proto
      - |
        for d in {{.PROTO_OUT}}/crosscodex {{.PROTO_OUT}}/crosscodex/v1; do
          touch "$d/__init__.py"
        done
    sources:
      - "{{.PROTO_DIR}}/crosscodex/v1/*.proto"
    generates:
      - "{{.PROTO_OUT}}/crosscodex/v1/*_pb2.py"

  lint:
    desc: Run ruff + mypy
    cmds:
      - ruff check src/ tests/
      - ruff format --check src/ tests/
      - mypy src/crosscodex_ingestion/ --ignore-missing-imports

  test:
    desc: Run unit tests
    vars:
      MODE: '{{.MODE | default "human"}}'
    cmds:
      - >-
        pytest tests/unit/ -v
        {{if eq .MODE "llm"}}--tb=short --no-header -q{{end}}
        --junitxml=.test-output/unit-results.xml
        --cov=crosscodex_ingestion --cov-report=html:.test-output/coverage

  test:integration:
    desc: Integration tests (requires Docling)
    cmds:
      - pytest tests/integration/ -v --junitxml=.test-output/integration-results.xml

  test:container:
    desc: Container tests (requires podman)
    cmds:
      - podman-compose -f docker-compose.yml up -d --build
      - venom run tests/venom/ --output-dir=.test-output/venom || true
      - podman-compose -f docker-compose.yml down

  test:all:
    desc: Run all test suites
    cmds:
      - task: test
      - task: test:integration

  build:
    desc: Build Python package
    cmds:
      - python -m build

  run:
    desc: Run service locally
    cmds:
      - "{{.PYTHON}} -m crosscodex_ingestion"

  clean:
    desc: Remove generated files + test artifacts
    cmds:
      - rm -rf {{.PROTO_OUT}} .test-output/ dist/ build/ *.egg-info
```

- [ ] **Step 5: Create empty package files**

```bash
mkdir -p src/crosscodex_ingestion tests/unit tests/integration tests/fixtures/documents
touch src/crosscodex_ingestion/__init__.py
touch src/crosscodex_ingestion/py.typed
touch tests/__init__.py tests/unit/__init__.py tests/integration/__init__.py
```

- [ ] **Step 6: Create `tests/conftest.py`**

```python
import os
import sys

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures", "documents")
```

- [ ] **Step 7: Install dependencies and generate proto stubs**

```bash
pip install -e ".[dev]"
task proto
```

Expected: proto stubs generated at `src/crosscodex_ingestion/proto/crosscodex/v1/`.

- [ ] **Step 8: Verify proto stubs import correctly**

```bash
python -c "from crosscodex_ingestion.proto.crosscodex.v1 import ingestion_pb2, common_pb2; print('Proto stubs OK')"
```

Expected: `Proto stubs OK`

- [ ] **Step 9: Commit**

```bash
git add pyproject.toml Taskfile.yml .gitignore proto/ src/ tests/
git commit -m "feat: project scaffold with proto codegen"
```

---

### Task 2: Configuration module

**Files:**
- Create: `src/crosscodex_ingestion/config.py`
- Create: `tests/unit/test_config.py`

**Interfaces:**
- Produces: `IngestionSettings` class (Pydantic BaseSettings). All subsequent modules import `from crosscodex_ingestion.config import IngestionSettings` and call `IngestionSettings()` to load from env.

Key fields (all with defaults — see spec "Environment Configuration" for complete list):
- `grpc_port: int = 8080`
- `metrics_port: int = 9090`
- `log_level: str = "info"`
- `execution_mode: Literal["embedded", "distributed"] = "embedded"`
- `max_concurrent_conversions: int = 4`
- `max_document_size: int = 52428800`
- `max_processing_time: int = 300`
- `max_pages: int = 1000`
- `max_extraction_length: int = 10485760`
- `allowed_formats: str = "pdf,docx,html,txt"` (property `allowed_formats_set -> set[str]`)
- `rlimit_as_mb: int = 4096` (0=unlimited)
- `rlimit_cpu: int = 300` (0=unlimited)
- `rlimit_fsize_mb: int = 100` (0=unlimited)
- `max_url_bytes: int = 104857600`
- `url_timeout: int = 30`
- `max_redirects: int = 5`
- `storage_backend: Literal["filesystem", "s3"] = "filesystem"`
- `storage_path: str = "/data/documents"`
- `s3_endpoint: str = ""`
- `s3_bucket: str = "crosscodex-documents"`
- `s3_access_key: str = ""`
- `s3_secret_key: str = ""`
- `s3_region: str = "us-east-1"`
- `attestation_enabled: bool = True`
- `attestation_private_key_path: str = ""`
- `attestation_public_key_path: str = ""`
- `fips_mode: bool = False`
- `nats_url: str = ""`
- `nats_tls_cert: str = ""`
- `nats_tls_key: str = ""`
- `nats_tls_ca: str = ""`
- `state_backend: Literal["memory", "redis"] = "memory"`
- `redis_url: str = ""`

- [ ] **Step 1: Write failing tests**

Write `tests/unit/test_config.py` testing:
- Default values load correctly with no env vars set
- `allowed_formats_set` property parses comma-separated string into a set
- `GRPC_PORT` env var overrides default
- Invalid `execution_mode` raises `ValidationError`
- `rlimit_as_mb = 0` is accepted (unlimited)
- All format strings are lowercased and stripped

```python
import os
import pytest
from pydantic import ValidationError


def test_defaults():
    from crosscodex_ingestion.config import IngestionSettings
    settings = IngestionSettings()
    assert settings.grpc_port == 8080
    assert settings.log_level == "info"
    assert settings.execution_mode == "embedded"
    assert settings.max_document_size == 52_428_800
    assert settings.attestation_enabled is True
    assert settings.storage_backend == "filesystem"


def test_allowed_formats_set():
    from crosscodex_ingestion.config import IngestionSettings
    settings = IngestionSettings()
    assert settings.allowed_formats_set == {"pdf", "docx", "html", "txt"}


def test_allowed_formats_custom(monkeypatch):
    from crosscodex_ingestion.config import IngestionSettings
    monkeypatch.setenv("ALLOWED_FORMATS", " PDF , Html ")
    settings = IngestionSettings()
    assert settings.allowed_formats_set == {"pdf", "html"}


def test_env_override(monkeypatch):
    from crosscodex_ingestion.config import IngestionSettings
    monkeypatch.setenv("GRPC_PORT", "9999")
    settings = IngestionSettings()
    assert settings.grpc_port == 9999


def test_invalid_execution_mode(monkeypatch):
    from crosscodex_ingestion.config import IngestionSettings
    monkeypatch.setenv("EXECUTION_MODE", "invalid")
    with pytest.raises(ValidationError):
        IngestionSettings()


def test_rlimit_zero_unlimited():
    from crosscodex_ingestion.config import IngestionSettings
    settings = IngestionSettings(rlimit_as_mb=0)
    assert settings.rlimit_as_mb == 0
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
pytest tests/unit/test_config.py -v
```

Expected: `ModuleNotFoundError: No module named 'crosscodex_ingestion.config'`

- [ ] **Step 3: Implement `config.py`**

```python
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings


class IngestionSettings(BaseSettings):
    model_config = {"env_prefix": "", "case_sensitive": False}

    grpc_port: int = 8080
    metrics_port: int = 9090
    log_level: str = "info"
    execution_mode: Literal["embedded", "distributed"] = "embedded"
    max_concurrent_conversions: int = 4

    max_document_size: int = 52_428_800
    max_processing_time: int = 300
    max_pages: int = 1000
    max_extraction_length: int = 10_485_760
    allowed_formats: str = "pdf,docx,html,txt"

    rlimit_as_mb: int = 4096
    rlimit_cpu: int = 300
    rlimit_fsize_mb: int = 100

    max_url_bytes: int = 104_857_600
    url_timeout: int = 30
    max_redirects: int = 5

    storage_backend: Literal["filesystem", "s3"] = "filesystem"
    storage_path: str = "/data/documents"
    s3_endpoint: str = ""
    s3_bucket: str = "crosscodex-documents"
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_region: str = "us-east-1"

    attestation_enabled: bool = True
    attestation_private_key_path: str = ""
    attestation_public_key_path: str = ""
    fips_mode: bool = False

    nats_url: str = ""
    nats_tls_cert: str = ""
    nats_tls_key: str = ""
    nats_tls_ca: str = ""

    state_backend: Literal["memory", "redis"] = "memory"
    redis_url: str = ""

    otel_exporter_otlp_endpoint: str = ""
    otel_exporter_otlp_protocol: str = "grpc"
    otel_service_name: str = "crosscodex-ingestion"
    otel_traces_sampler: str = "parentbased_always_on"

    @property
    def allowed_formats_set(self) -> set[str]:
        return {f.strip().lower() for f in self.allowed_formats.split(",") if f.strip()}

    @field_validator("log_level")
    @classmethod
    def normalize_log_level(cls, v: str) -> str:
        return v.lower()
```

- [ ] **Step 4: Run tests — verify they pass**

```bash
pytest tests/unit/test_config.py -v
```

Expected: all 6 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/crosscodex_ingestion/config.py tests/unit/test_config.py
git commit -m "feat: configuration module with Pydantic Settings"
```

---

### Task 3: Security module — SSRF prevention + format validation

**Files:**
- Create: `src/crosscodex_ingestion/security.py`
- Create: `tests/unit/test_security.py`

**Interfaces:**
- Consumes: `IngestionSettings` from Task 2
- Produces:
  - `validate_url(url: str) -> None` — raises `ValueError` on blocked URLs
  - `fetch_url(url: str, max_bytes: int, timeout: int, max_redirects: int) -> Iterator[tuple[str, str]]` — context manager yielding `(local_path, detected_type)`
  - `sniff_format(content: bytes, mime_type: str = "", url: str = "") -> str` — returns format string (`"pdf"`, `"html"`, etc.)
  - `validate_format(content: bytes, declared_format: str, allowed_formats: set[str]) -> str` — returns detected format or raises `ValueError`
  - `validate_content_hash(content: bytes, declared_sha256: str) -> None` — raises `ValueError` on mismatch

Port from `/workspace/OllamaCrosswalker/src/extraction/url_fetcher.py`. Adapt the format allowlist to the 4 formats from the spec (pdf, docx, html, txt). Keep the SSRF prevention logic (IP blocking, redirect re-validation) identical.

- [ ] **Step 1: Write failing tests**

Write `tests/unit/test_security.py` with tests for:
- `validate_url` rejects `ftp://`, `file://`, `gopher://` schemes
- `validate_url` rejects loopback (`127.0.0.1`), link-local (`169.254.x.x`), private (`10.x.x.x`, `192.168.x.x`, `172.16.x.x`), carrier-grade NAT (`100.64.x.x`)
- `validate_url` accepts valid public HTTPS URLs
- `validate_url` rejects URLs with no hostname
- `sniff_format` detects PDF via `%PDF-` magic bytes
- `sniff_format` detects HTML via `<html` / `<!doctype`
- `sniff_format` detects DOCX via ZIP magic `PK\x03\x04` + `.docx` URL
- `sniff_format` falls back to MIME type, then URL extension
- `validate_format` raises on format mismatch (PDF content declared as HTML)
- `validate_format` raises on format not in allowlist
- `validate_content_hash` passes when SHA-256 matches
- `validate_content_hash` raises when SHA-256 mismatches
- `validate_content_hash` does nothing when declared hash is empty

**Minimum 15 test functions.**

Use `unittest.mock.patch` to mock `socket.getaddrinfo` for IP resolution tests — do not make real DNS queries in unit tests.

- [ ] **Step 2: Run tests — verify they fail**

```bash
pytest tests/unit/test_security.py -v
```

Expected: import error.

- [ ] **Step 3: Implement `security.py`**

Port from `/workspace/OllamaCrosswalker/src/extraction/url_fetcher.py`:
- `_is_blocked_ip()` — identical logic
- `validate_url()` — identical logic (scheme check + DNS resolve + IP block check)
- `_sniff_type()` → rename to `sniff_format()` — same byte-sniffing cascade, reduced format set
- `fetch_url()` — identical redirect-following logic
- Add `validate_format()` — new function: calls `sniff_format()`, checks against allowlist, checks against declared format
- Add `validate_content_hash()` — new function: SHA-256 verification

Keep the `_EXTRA_BLOCKED`, `_ALLOWED_SCHEMES`, `_CT_MAP`, `_EXT_MAP` constants. Reduce `_TYPE_SUFFIX` to the 4 supported formats.

- [ ] **Step 4: Run tests — verify they pass**

```bash
pytest tests/unit/test_security.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/crosscodex_ingestion/security.py tests/unit/test_security.py
git commit -m "feat: security module with SSRF prevention and format validation"
```

---

### Task 4: Extractor module — Docling output → structured elements

**Files:**
- Create: `src/crosscodex_ingestion/extractor.py`
- Create: `tests/unit/test_extractor.py`
- Create: `tests/fixtures/documents/sample.html`
- Create: `tests/fixtures/documents/sample.txt`

**Interfaces:**
- Produces:
  - `extract_elements(doc: DoclingDocument | None, markdown: str, format_hint: str) -> ExtractionResult`
  - `ExtractionResult` — dataclass with `elements: list[dict]`, `metadata: dict` (page_count, detected_format, element_count, title, processing_time_ms)

Each element dict has: `element_id`, `type`, `content`, `page`, `level`, `parent_id`, `style`.

Port from `/workspace/OllamaCrosswalker/src/extraction/docling_extractor.py`:
- `_parse_docling_document()` → adapt to output element dicts instead of OSCAL controls
- `_parse_headings()` → adapt for hierarchy building
- `_detect_section_pattern()` + `_parse_with_pattern()` → tiers 1-3 pattern cascade
- `_parse_paragraphs()` → final fallback
- `_clean_text()` → text cleaning
- Skip `_llm_detect_section_pattern()` (tier 4) and `_llm_extract_structure()` (tier 5) and `_llm_enrich_controls()` — these are out of scope
- Skip `_save_oscal_output()` — no OSCAL in this service
- Skip `_decompose_section()` — this is compliance domain logic (Go side)

The element type mapping from DocItemLabel:
- `SECTION_HEADER` → `"heading"`
- `TEXT`, `PARAGRAPH` → `"paragraph"`
- `TABLE` → `"table"`
- `LIST_ITEM` → `"list_item"`
- `PAGE_HEADER` → `"page_header"` (included only if requested)
- `PAGE_FOOTER` → `"page_footer"` (included only if requested)

- [ ] **Step 1: Create test fixture files**

`tests/fixtures/documents/sample.html`:
```html
<!DOCTYPE html>
<html>
<head><title>Test Document</title></head>
<body>
<h1>Section One</h1>
<p>First paragraph content.</p>
<h2>Subsection A</h2>
<p>Nested content here.</p>
<h1>Section Two</h1>
<p>Second section content.</p>
<table><tr><th>Header</th></tr><tr><td>Value</td></tr></table>
</body>
</html>
```

`tests/fixtures/documents/sample.txt`:
```
Section 1. Introduction

This document provides guidance for compliance requirements.

Section 2. Access Control

Organizations shall implement access control mechanisms.

Section 3. Audit Logging

All access events must be logged and monitored.
```

- [ ] **Step 2: Write failing tests**

Write `tests/unit/test_extractor.py` testing:
- `extract_elements` with a mock DoclingDocument produces heading + paragraph elements
- Heading hierarchy is tracked: h2 under h1 gets correct `parent_id`
- Element IDs are generated sequentially (`e-001`, `e-002`, ...)
- Markdown heading parsing (tier 2): `# H1` → heading level 1
- Pattern detection (tier 3): `Section 1.` repeating pattern detected
- Paragraph fallback: plain text without patterns → paragraph elements
- Spurious heading filtering: headings repeated 3+ times are skipped
- Empty content produces empty element list
- Element type mapping is correct for each DocItemLabel

**Minimum 10 test functions.** For Docling document tests, create a mock/fake DoclingDocument class that yields typed items — do not import real Docling (it's a heavy dependency).

- [ ] **Step 3: Run tests — verify they fail**

```bash
pytest tests/unit/test_extractor.py -v
```

- [ ] **Step 4: Implement `extractor.py`**

Port the extraction logic from `docling_extractor.py`, stripping OSCAL-specific code. Output format is a list of element dicts matching the structured JSON spec:

```python
{
    "element_id": "e-001",
    "type": "heading",
    "content": "Access Control",
    "page": 1,
    "level": 1,
    "parent_id": None,
    "style": {"bold": True, "font_size": 14.0}
}
```

Key adaptations:
- Replace `self.controls.append(oscal_dict)` with `self.elements.append(element_dict)`
- Remove OSCAL `class`, `parts`, `props` — use flat element structure
- Keep `_HEADING_PATTERN`, `_CANDIDATE_PATTERNS`, `_MIN_PATTERN_MATCHES` constants
- Keep heading stack logic for parent_id tracking
- Keep `_make_section_id()` → but rename to `_make_element_id()` and use sequential numbering
- Keep `_clean_text()`, `_extract_first_lines()`, `_count_pattern_matches()`, `_detect_section_pattern()`

- [ ] **Step 5: Run tests — verify they pass**

```bash
pytest tests/unit/test_extractor.py -v
```

- [ ] **Step 6: Commit**

```bash
git add src/crosscodex_ingestion/extractor.py tests/unit/test_extractor.py tests/fixtures/
git commit -m "feat: extractor module with Docling parsing and tier 1-3 pattern cascade"
```

---

### Task 5: Converter module — subprocess isolation

**Files:**
- Create: `src/crosscodex_ingestion/converter.py`
- Create: `tests/unit/test_converter.py`

**Interfaces:**
- Consumes: `IngestionSettings` (Task 2), `extract_elements()` (Task 4)
- Produces:
  - `convert_document(input_path: str, format_hint: str, settings: IngestionSettings) -> dict` — runs Docling + extraction in a subprocess, returns the structured JSON dict (elements + metadata)
  - `ConversionError` — exception class for conversion failures
  - `ConversionTimeout` — exception class for timeout

The converter:
1. Creates a temp directory
2. Copies input file into it
3. Forks a subprocess that:
   a. Sets rlimits (RLIMIT_AS, RLIMIT_CPU, RLIMIT_FSIZE) from settings
   b. Runs Docling conversion on the input file
   c. Calls `extract_elements()` on the Docling output
   d. Writes the result as JSON to `<temp_dir>/output.json`
4. Parent reads `output.json` and returns the parsed dict
5. Cleans up temp directory

The subprocess is a Python script invoked via `subprocess.run()` with `sys.executable` — not a shell command. Environment is minimal (`PATH` only).

- [ ] **Step 1: Write failing tests**

Write `tests/unit/test_converter.py` testing:
- Successful conversion returns dict with `elements` and `metadata` keys
- Subprocess timeout raises `ConversionTimeout`
- Subprocess crash (non-zero exit) raises `ConversionError`
- Temp directory is cleaned up after success
- Temp directory is cleaned up after failure
- Rlimits are set when non-zero (mock `resource.setrlimit`)
- Rlimits are skipped when set to 0 (unlimited)
- Minimal environment is passed to child (no `HOME`, `USER`, etc.)

Use `unittest.mock.patch` to mock `subprocess.run` for most tests. One test can use a real subprocess with a trivial Python script to verify the IPC mechanism works.

- [ ] **Step 2: Run tests — verify they fail**

```bash
pytest tests/unit/test_converter.py -v
```

- [ ] **Step 3: Implement `converter.py`**

The converter uses a two-file approach:
- `converter.py` contains `convert_document()` which handles subprocess management
- A `_worker_script` string constant containing the child process code — written to a temp file and executed via `subprocess.run([sys.executable, script_path, ...])`

The child process script:
```python
import json
import resource
import sys

def main():
    input_path, output_path, format_hint = sys.argv[1:4]
    rlimit_as = int(sys.argv[4])
    rlimit_cpu = int(sys.argv[5])
    rlimit_fsize = int(sys.argv[6])

    if rlimit_as > 0:
        resource.setrlimit(resource.RLIMIT_AS, (rlimit_as, rlimit_as))
    if rlimit_cpu > 0:
        resource.setrlimit(resource.RLIMIT_CPU, (rlimit_cpu, rlimit_cpu))
    if rlimit_fsize > 0:
        resource.setrlimit(resource.RLIMIT_FSIZE, (rlimit_fsize, rlimit_fsize))

    # Import here so rlimits are set before Docling loads
    from crosscodex_ingestion.extractor import extract_elements
    from docling.document_converter import DocumentConverter

    converter = DocumentConverter()
    result = converter.convert(input_path)
    doc = result.document
    markdown = doc.export_to_markdown()

    extraction = extract_elements(doc, markdown, format_hint)

    with open(output_path, "w") as f:
        json.dump(extraction, f)

if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests — verify they pass**

```bash
pytest tests/unit/test_converter.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/crosscodex_ingestion/converter.py tests/unit/test_converter.py
git commit -m "feat: converter module with subprocess isolation and configurable rlimits"
```

---

### Task 6: Storage module — filesystem + S3

**Files:**
- Create: `src/crosscodex_ingestion/storage.py`
- Create: `tests/unit/test_storage.py`

**Interfaces:**
- Consumes: `IngestionSettings` (Task 2)
- Produces:
  - `StorageBackend` — abstract base with `upload(tenant_id: str, document_id: str, filename: str, data: bytes) -> str` (returns URI), `download(uri: str) -> bytes`, `exists(uri: str) -> bool`
  - `FilesystemStorage(base_path: str)` — writes to `<base_path>/<tenant_id>/documents/<document_id>/<filename>`
  - `S3Storage(endpoint: str, bucket: str, access_key: str, secret_key: str, region: str)` — uses boto3
  - `create_storage(settings: IngestionSettings) -> StorageBackend` — factory

- [ ] **Step 1: Write failing tests**

Test `FilesystemStorage`:
- `upload` creates tenant-prefixed path and writes data
- `upload` returns the correct URI
- `download` reads back the uploaded data
- `exists` returns True for uploaded, False for missing
- Tenant isolation: two tenants can upload same filename without conflict

Test `S3Storage` (mock boto3):
- `upload` calls `s3_client.put_object` with correct bucket, key, body
- Key is tenant-prefixed: `<tenant_id>/documents/<document_id>/<filename>`

Test `create_storage`:
- Returns `FilesystemStorage` when `storage_backend == "filesystem"`
- Returns `S3Storage` when `storage_backend == "s3"`

Use `tmp_path` fixture for filesystem tests. Mock `boto3.client` for S3 tests.

- [ ] **Step 2: Run tests — verify they fail**
- [ ] **Step 3: Implement `storage.py`**
- [ ] **Step 4: Run tests — verify they pass**
- [ ] **Step 5: Commit**

```bash
git add src/crosscodex_ingestion/storage.py tests/unit/test_storage.py
git commit -m "feat: storage module with filesystem and S3 backends"
```

---

### Task 7: State module — document state management

**Files:**
- Create: `src/crosscodex_ingestion/state.py`
- Create: `tests/unit/test_state.py`

**Interfaces:**
- Consumes: `IngestionSettings` (Task 2), proto types from Task 1
- Produces:
  - `DocumentRecord` — dataclass holding document_id, tenant_id, status (JobStatus int), metadata (ContentMetadata dict), audit (AuditMetadata dict), structured_json_uri, error (Error dict or None), created_at (ISO string), updated_at (ISO string)
  - `StateBackend` — abstract base with:
    - `create(record: DocumentRecord) -> None`
    - `get(tenant_id: str, document_id: str) -> DocumentRecord | None`
    - `update(tenant_id: str, document_id: str, **fields) -> None`
    - `list_documents(tenant_id: str, page_size: int, page_token: str) -> tuple[list[DocumentRecord], str]` (returns records + next_page_token)
  - `MemoryStateBackend()` — in-memory dict keyed by `(tenant_id, document_id)`
  - `RedisStateBackend(redis_url: str)` — serializes DocumentRecord to JSON, stores in Redis hash
  - `create_state_backend(settings: IngestionSettings) -> StateBackend` — factory

- [ ] **Step 1: Write failing tests**

Test `MemoryStateBackend`:
- `create` then `get` returns same record
- `get` returns None for missing document
- `update` changes specific fields
- `list_documents` returns only records for the given tenant
- `list_documents` pagination works (page_size=2 on 5 records)
- Tenant isolation: tenant A cannot see tenant B's documents

Test `RedisStateBackend` (mock redis):
- `create` calls `redis.hset` with serialized JSON
- `get` calls `redis.hget` and deserializes

Test `create_state_backend`:
- Returns `MemoryStateBackend` when `state_backend == "memory"`
- Returns `RedisStateBackend` when `state_backend == "redis"`

- [ ] **Step 2: Run tests — verify they fail**
- [ ] **Step 3: Implement `state.py`**
- [ ] **Step 4: Run tests — verify they pass**
- [ ] **Step 5: Commit**

```bash
git add src/crosscodex_ingestion/state.py tests/unit/test_state.py
git commit -m "feat: state module with memory and Redis backends"
```

---

### Task 8: Attestation module — in-toto signed links

**Files:**
- Create: `src/crosscodex_ingestion/attestation.py`
- Create: `tests/unit/test_attestation.py`

**Interfaces:**
- Consumes: `IngestionSettings` (Task 2)
- Produces:
  - `AttestationManager` — class with:
    - `__init__(settings: IngestionSettings)` — generates ephemeral ECDSA P-256 key or loads from file
    - `create_link(step_name: str, materials: dict[str, dict], products: dict[str, dict], byproducts: dict) -> dict` — returns serialized signed link dict
    - `public_key_id() -> str` — returns the key ID
  - `create_attestation_manager(settings: IngestionSettings) -> AttestationManager | None` — returns None if `attestation_enabled` is False

Key implementation details:
- Use `in_toto.models.link.Link` for link creation
- Use `in_toto.models.metadata.Metadata` for signing
- Use `securesystemslib.signer.CryptoSigner` for ECDSA key generation/loading
- FIPS mode: only allow ECDSA P-256/P-384/P-521. Reject if RSA or Ed25519 key is loaded.
- Byproducts include `trace_id`, `span_id`, `tenant_id`, `hostname`, `timestamp`, `return_value`, `detected_format`, `element_count`, `processing_time_ms`

- [ ] **Step 1: Write failing tests**

Test:
- Ephemeral key generation produces valid ECDSA key
- `create_link` produces dict with `signed.materials`, `signed.products`, `signed.byproducts`
- `create_link` signature is present and non-empty
- Signed link can be verified with the public key
- File-based key loading works (write temp key files, load them)
- FIPS mode rejects non-ECDSA algorithms
- `create_attestation_manager` returns None when disabled
- Byproducts contain all required fields

- [ ] **Step 2: Run tests — verify they fail**
- [ ] **Step 3: Implement `attestation.py`**

Verify the in-toto API exists before using it — run `python -c "from in_toto.models.link import Link; print(dir(Link))"` to check the actual class interface. Do not trust training data for API signatures.

- [ ] **Step 4: Run tests — verify they pass**
- [ ] **Step 5: Commit**

```bash
git add src/crosscodex_ingestion/attestation.py tests/unit/test_attestation.py
git commit -m "feat: attestation module with in-toto signed links and FIPS enforcement"
```

---

### Task 9: Audit module — NATS events + logging fallback

**Files:**
- Create: `src/crosscodex_ingestion/audit.py`
- Create: `tests/unit/test_audit.py`

**Interfaces:**
- Consumes: `IngestionSettings` (Task 2)
- Produces:
  - `AuditPublisher` — class with:
    - `__init__(settings: IngestionSettings)` — connects to NATS or sets up log fallback
    - `async publish(tenant_id: str, event: dict, trace_id: str, span_id: str) -> None`
    - `async close() -> None`
  - Headers on NATS messages: `X-Trace-Id`, `X-Span-Id`, `X-Tenant-Id`, `X-Timestamp`, `X-Content-SHA256`
  - Subject: `tenant.<tenant_id>.audit.ingestion`

When `nats_url` is empty, `publish()` logs the event via structured logging instead of publishing to NATS.

- [ ] **Step 1: Write failing tests**

Test:
- NATS publish sends correct subject, headers, and payload (mock `nats.connect`)
- Headers include all 5 required fields
- `X-Content-SHA256` is SHA-256 of the JSON payload
- Log fallback: when no NATS URL, event is logged at INFO level
- TLS connection when cert/key/ca are provided (mock ssl context)
- Close cleans up NATS connection

- [ ] **Step 2: Run tests — verify they fail**
- [ ] **Step 3: Implement `audit.py`**
- [ ] **Step 4: Run tests — verify they pass**
- [ ] **Step 5: Commit**

```bash
git add src/crosscodex_ingestion/audit.py tests/unit/test_audit.py
git commit -m "feat: audit module with NATS JetStream and logging fallback"
```

---

### Task 10: Worker module — conversion pipeline orchestration

**Files:**
- Create: `src/crosscodex_ingestion/worker.py`
- Create: `tests/unit/test_worker.py`

**Interfaces:**
- Consumes: `IngestionSettings` (Task 2), `security.validate_url`/`security.fetch_url`/`security.validate_format` (Task 3), `converter.convert_document` (Task 5), `StorageBackend` (Task 6), `StateBackend`/`DocumentRecord` (Task 7), `AttestationManager` (Task 8), `AuditPublisher` (Task 9)
- Produces:
  - `async run_conversion(document_id: str, tenant_id: str, content: bytes | None, source_uri: str | None, metadata: dict, settings: IngestionSettings, state: StateBackend, storage: StorageBackend, attestation: AttestationManager | None, audit: AuditPublisher) -> None`

This is the core pipeline function called by the dispatch layer. It:
1. Updates state to `JOB_STATUS_RUNNING`
2. Resolves input: fetch URL or use provided bytes
3. Validates format (byte sniffing + allowlist)
4. Writes bytes to temp file
5. Calls `convert_document()` (subprocess)
6. Uploads structured JSON to storage
7. Creates in-toto signed link (if attestation enabled)
8. Uploads attestation link to storage
9. Publishes NATS audit event
10. Updates state to `JOB_STATUS_COMPLETED` with `structured_json_uri`

On any error: updates state to `JOB_STATUS_FAILED` with `Error` details.

- [ ] **Step 1: Write failing tests**

Test the full pipeline with mocked dependencies:
- Successful bytes conversion: state transitions PENDING→RUNNING→COMPLETED
- Successful URL conversion: fetch_url called, same completion flow
- Format validation failure: state set to FAILED with INVALID_ARGUMENT error
- Conversion timeout: state set to FAILED with appropriate error
- Storage upload failure: state set to FAILED
- Attestation creates link with correct materials/products hashes
- Audit event published with correct document_id and status
- Temp files cleaned up after success
- Temp files cleaned up after failure

- [ ] **Step 2: Run tests — verify they fail**
- [ ] **Step 3: Implement `worker.py`**
- [ ] **Step 4: Run tests — verify they pass**
- [ ] **Step 5: Commit**

```bash
git add src/crosscodex_ingestion/worker.py tests/unit/test_worker.py
git commit -m "feat: worker module orchestrating the conversion pipeline"
```

---

### Task 11: Dispatch module — embedded + distributed backends

**Files:**
- Create: `src/crosscodex_ingestion/dispatch.py`
- Create: `tests/unit/test_dispatch.py`

**Interfaces:**
- Consumes: `IngestionSettings` (Task 2), `run_conversion` (Task 10), `StateBackend` (Task 7), `StorageBackend` (Task 6), `AttestationManager` (Task 8), `AuditPublisher` (Task 9)
- Produces:
  - `DispatchBackend` — abstract base with `async dispatch(document_id: str, tenant_id: str, content: bytes | None, source_uri: str | None, metadata: dict) -> None` and `async shutdown() -> None`
  - `EmbeddedDispatch(settings, state, storage, attestation, audit)` — runs `run_conversion` via `asyncio.create_task()` with a semaphore for concurrency limiting
  - `NatsDispatch(settings)` — publishes job to NATS JetStream work queue `ingestion.jobs`
  - `create_dispatch(settings, state, storage, attestation, audit) -> DispatchBackend` — factory

- [ ] **Step 1: Write failing tests**

Test `EmbeddedDispatch`:
- `dispatch` creates an asyncio task
- Task calls `run_conversion` with correct arguments
- Semaphore limits concurrency to `max_concurrent_conversions`
- `shutdown` waits for in-flight tasks

Test `NatsDispatch` (mock nats):
- `dispatch` publishes serialized job to correct subject
- Job payload includes document_id, tenant_id, content (base64), source_uri, metadata

Test `create_dispatch`:
- Returns `EmbeddedDispatch` when `execution_mode == "embedded"`
- Returns `NatsDispatch` when `execution_mode == "distributed"`

- [ ] **Step 2: Run tests — verify they fail**
- [ ] **Step 3: Implement `dispatch.py`**
- [ ] **Step 4: Run tests — verify they pass**
- [ ] **Step 5: Commit**

```bash
git add src/crosscodex_ingestion/dispatch.py tests/unit/test_dispatch.py
git commit -m "feat: dispatch module with embedded asyncio and NATS backends"
```

---

### Task 12: gRPC service + server + entry point

**Files:**
- Create: `src/crosscodex_ingestion/service.py`
- Create: `src/crosscodex_ingestion/server.py`
- Create: `src/crosscodex_ingestion/__main__.py`
- Create: `tests/unit/test_service.py`

**Interfaces:**
- Consumes: all previous modules
- Produces: The complete gRPC service — `IngestionServiceServicer` implementing ConvertDocument, GetDocument, ListDocuments. `serve()` function that starts the gRPC server with OTel interceptors.

`service.py` — the gRPC servicer:
- `ConvertDocument`: validate TenantContext, validate input (content or source_uri must be set), validate format against allowlist, check document size, assign UUID, create DocumentRecord in state backend, dispatch to worker, return ConvertDocumentResponse
- `GetDocument`: validate TenantContext, look up in state, return GetDocumentResponse
- `ListDocuments`: validate TenantContext, query state backend with pagination

`server.py` — server lifecycle:
- Initialize OTel (TracerProvider, OTLP exporter, gRPC interceptor), Prometheus metrics server, structured logging
- Create settings, state backend, storage backend, attestation manager, audit publisher, dispatch backend
- Start gRPC server with OTel server interceptor
- Graceful shutdown on SIGTERM/SIGINT

`__main__.py`:
```python
from crosscodex_ingestion.server import serve
serve()
```

- [ ] **Step 1: Write failing tests**

Write `tests/unit/test_service.py`:
- `ConvertDocument` with valid bytes returns document_id and PENDING status
- `ConvertDocument` with valid URL returns document_id and PENDING status
- `ConvertDocument` missing TenantContext returns INVALID_ARGUMENT
- `ConvertDocument` missing content and source_uri returns INVALID_ARGUMENT
- `ConvertDocument` with oversized content returns RESOURCE_EXHAUSTED
- `ConvertDocument` with disallowed format returns INVALID_ARGUMENT
- `GetDocument` returns COMPLETED document with structured_json_uri
- `GetDocument` for missing document returns NOT_FOUND
- `GetDocument` tenant isolation: tenant A cannot get tenant B's document
- `ListDocuments` returns only requesting tenant's documents
- `ListDocuments` pagination works

Use `grpcio-testing` or mock the servicer directly (instantiate `IngestionServiceServicer` with mocked dependencies, call methods with mock `ServicerContext`).

- [ ] **Step 2: Run tests — verify they fail**
- [ ] **Step 3: Implement `service.py`, `server.py`, `__main__.py`**
- [ ] **Step 4: Run tests — verify they pass**
- [ ] **Step 5: Commit**

```bash
git add src/crosscodex_ingestion/service.py src/crosscodex_ingestion/server.py \
  src/crosscodex_ingestion/__main__.py tests/unit/test_service.py
git commit -m "feat: gRPC service, server lifecycle, and entry point"
```

---

### Task 13: Observability — OTel tracing + Prometheus metrics

**Files:**
- Modify: `src/crosscodex_ingestion/server.py` (add OTel init + metrics server)
- Modify: `src/crosscodex_ingestion/worker.py` (add child spans)
- Modify: `src/crosscodex_ingestion/service.py` (add span attributes)

**Interfaces:**
- Consumes: `IngestionSettings` (Task 2)
- Produces: Child spans on all worker operations, Prometheus counters/histograms/gauges, structured JSON logging with trace correlation

This task wires OTel and metrics into the existing modules. No new test files — extend existing tests.

- [ ] **Step 1: Add OTel initialization to `server.py`**

Add `_init_telemetry(settings: IngestionSettings)` function:
- If `otel_exporter_otlp_endpoint` is set: configure TracerProvider with OTLP exporter, resource attributes (service.name, service.version, host.name), sampler
- If not set: no-op TracerProvider
- Configure structured JSON logging with `python-json-logger`, inject trace_id/span_id via OTel log handler
- Start Prometheus HTTP server on `metrics_port`

- [ ] **Step 2: Add child spans to `worker.py`**

Wrap each pipeline step in a span:
- `ingestion.validate`, `ingestion.fetch_url`, `ingestion.convert`, `ingestion.upload`, `ingestion.attest`, `ingestion.audit`
- Add span attributes: `document.id`, `document.format`, `document.size_bytes`, `tenant.id`

- [ ] **Step 3: Add Prometheus metrics**

In `service.py` and `worker.py`:
- Counter: `ingestion_requests_total` (labels: format, status, tenant_id)
- Counter: `ingestion_errors_total` (labels: error_type)
- Histogram: `ingestion_duration_seconds` (labels: format)
- Histogram: `ingestion_document_size_bytes`
- Gauge: `ingestion_active_conversions`
- Gauge: `ingestion_queue_depth`

- [ ] **Step 4: Run all unit tests**

```bash
task test
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/crosscodex_ingestion/server.py src/crosscodex_ingestion/worker.py \
  src/crosscodex_ingestion/service.py
git commit -m "feat: OpenTelemetry tracing, Prometheus metrics, structured logging"
```

---

### Task 14: Container images + docker-compose

**Files:**
- Create: `Dockerfile`
- Create: `Dockerfile.fips`
- Create: `docker-compose.yml`

**Interfaces:**
- Consumes: all source code, proto files, pyproject.toml

- [ ] **Step 1: Create `Dockerfile`**

Multi-stage build:
```dockerfile
FROM python:3.12-slim AS builder
WORKDIR /app
COPY pyproject.toml .
RUN pip install --no-cache-dir .
COPY proto/ proto/
RUN pip install grpcio-tools mypy-protobuf && \
    mkdir -p src/crosscodex_ingestion/proto && \
    python -m grpc_tools.protoc \
      --proto_path=proto \
      --python_out=src/crosscodex_ingestion/proto \
      --grpc_python_out=src/crosscodex_ingestion/proto \
      crosscodex/v1/common.proto crosscodex/v1/ingestion.proto && \
    for d in src/crosscodex_ingestion/proto/crosscodex src/crosscodex_ingestion/proto/crosscodex/v1; do \
      touch "$d/__init__.py"; \
    done
COPY src/ src/

FROM python:3.12-slim
WORKDIR /app
RUN groupadd -g 1000 appuser && useradd -u 1000 -g appuser -m appuser
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /app/src /app/src
USER appuser
EXPOSE 8080 9090
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
  CMD python -c "import grpc; ch=grpc.insecure_channel('localhost:8080'); grpc.channel_ready_future(ch).result(timeout=5)" || exit 1
ENTRYPOINT ["python", "-m", "crosscodex_ingestion"]
```

- [ ] **Step 2: Create `Dockerfile.fips`**

Same structure but based on `registry.access.redhat.com/ubi9/python-312` (builder) and `registry.access.redhat.com/ubi9-minimal` (runtime). Set `ENV FIPS_MODE=true`.

- [ ] **Step 3: Create `docker-compose.yml`**

Per the spec — ingestion service + NATS (JetStream) + Jaeger (OTLP collector). Use `podman-compose` compatible syntax.

- [ ] **Step 4: Verify standard image builds**

```bash
podman build -t crosscodex-ingestion:latest . 2>&1 | tail -5
```

Expected: `Successfully tagged localhost/crosscodex-ingestion:latest`

- [ ] **Step 5: Commit**

```bash
git add Dockerfile Dockerfile.fips docker-compose.yml
git commit -m "feat: standard and FIPS container images with docker-compose"
```

---

### Task 15: Integration tests + test fixtures

**Files:**
- Create: `tests/integration/test_conversion_e2e.py`
- Create: `tests/integration/test_health.py`
- Create: `tests/integration/test_security_e2e.py`
- Create: `tests/fixtures/documents/sample.pdf` (programmatically generated)
- Create: `tests/fixtures/documents/sample.docx` (programmatically generated)
- Modify: `tests/conftest.py` (add shared fixtures)

**Interfaces:**
- Consumes: all modules (full stack integration)

Integration tests start a real gRPC server in-process (embedded mode, filesystem storage to a temp dir) and exercise the full pipeline.

- [ ] **Step 1: Add shared fixtures to `conftest.py`**

```python
import asyncio
import os
import tempfile
import pytest
import grpc

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures", "documents")


@pytest.fixture
def sample_html():
    with open(os.path.join(FIXTURES_DIR, "sample.html"), "rb") as f:
        return f.read()


@pytest.fixture
def sample_txt():
    with open(os.path.join(FIXTURES_DIR, "sample.txt"), "rb") as f:
        return f.read()
```

- [ ] **Step 2: Generate test PDF and DOCX fixtures**

Use `reportlab` (for PDF) and `python-docx` (for DOCX) to programmatically create small test documents with headings, paragraphs, and tables. Add these as dev dependencies and create a `tests/fixtures/generate_fixtures.py` script. Run it once to create the fixture files, then commit the fixtures (not the generator script dependency).

Alternatively, create minimal valid files:
- PDF: use `fpdf2` to create a 2-page PDF with headings and paragraphs
- DOCX: use `python-docx` to create a document with Heading 1, paragraphs, a table

- [ ] **Step 3: Write `test_conversion_e2e.py`**

Tests that start the server, send a ConvertDocument request with each format, poll GetDocument until COMPLETED, and verify the structured JSON output contains expected element types.

- [ ] **Step 4: Write `test_health.py` and `test_security_e2e.py`**

- `test_health.py`: verify the server is reachable and responds
- `test_security_e2e.py`: verify oversized document is rejected, missing tenant is rejected

- [ ] **Step 5: Run integration tests**

```bash
task test:integration
```

- [ ] **Step 6: Commit**

```bash
git add tests/ 
git commit -m "feat: integration tests with generated fixtures for all formats"
```

---

### Task 16: Taskfile finalization + README update + lint pass

**Files:**
- Modify: `Taskfile.yml` (fix any issues found during integration)
- Modify: `README.md` (update with actual usage, not aspirational)
- Modify: any files with lint/type errors

- [ ] **Step 1: Run full lint pass**

```bash
task lint
```

Fix all ruff and mypy errors.

- [ ] **Step 2: Run full test suite**

```bash
task test
task test:integration
```

All tests must pass.

- [ ] **Step 3: Update README.md**

Replace the aspirational README with actual content reflecting what was built:
- How to install and run locally
- How to run tests
- Environment variables
- Container build instructions
- Architecture overview (brief, not duplicating the spec)

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "chore: lint fixes, README update, Taskfile finalization"
```
