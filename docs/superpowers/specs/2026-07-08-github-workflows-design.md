# GitHub Actions Workflows Design

## Goal

Add GitHub Actions CI and MegaLinter workflows to the crosscodex-ingestion
repository, following the patterns established in
[complytime-labs/crosscodex](https://github.com/complytime-labs/crosscodex).

**Key constraint:** all build/lint/test work is delegated to `task` commands.
The workflows orchestrate jobs and provide GitHub-specific integrations
(caching, artifacts, SARIF upload) but never duplicate logic that already
lives in the Taskfile.

## Workflows

### 1. `.github/workflows/ci.yml` — CI Pipeline

**Triggers:** push to `main`, pull requests targeting `main`.

**Permissions:** `contents: read` (least-privilege).

**Jobs:**

#### `build` (ubuntu-latest)

Steps:
1. `actions/checkout` with `persist-credentials: false`
2. Install [uv](https://docs.astral.sh/uv/) via `astral-sh/setup-uv`
3. Install [Task](https://taskfile.dev) via `go-task/setup-task`
4. `task setup` — create venv, install deps
5. `task proto` — generate gRPC stubs
6. `task lint` — ruff check + ruff format --check + mypy
7. `task build` — build Python package
8. `task test:default` — unit tests with coverage

#### `integration` (ubuntu-latest, `needs: [build]`)

Steps:
1. `actions/checkout` with `persist-credentials: false`
2. Install uv + Task (same as build)
3. `task setup` → `task proto`
4. `task test:integration` — integration tests (requires Docling)

The integration job gates on the build job so runner time is not wasted
on heavier tests when basic checks fail.

### 2. `.github/workflows/megalinter.yml` — MegaLinter Analysis

**Triggers:** push to `main`, pull requests targeting `main`, weekly schedule
(Monday 03:17 UTC), manual `workflow_dispatch`.

**Permissions:** `contents: read`, `security-events: write` (SARIF upload),
`pull-requests: write` (PR comments).

**Concurrency:** grouped by workflow + ref, cancel-in-progress for non-main
branches.

**Job:** `megalinter` (ubuntu-latest)

Steps:
1. `actions/checkout` with `fetch-depth: 0`, `persist-credentials: false`
2. Run MegaLinter via `trevor-vaughan/megalint-config` action (pinned hash)
   - `validate-all-codebase`: true on schedule, false otherwise
   - `github-comment`: true (PR summary comments)
3. Archive `megalinter-reports` as artifact
4. Upload SARIF to GitHub Code Scanning (gated on `sarif-has-results`)

MegaLinter handles language-agnostic linting (secrets, YAML, Dockerfile,
Markdown, security scanners) that `task lint` does not cover. It does not
re-run ruff or mypy.

## Files Created

| Path | Purpose |
|------|---------|
| `.github/workflows/ci.yml` | CI pipeline (build + integration jobs) |
| `.github/workflows/megalinter.yml` | MegaLinter analysis |

## What Is NOT Included

- **Release workflow:** no tag-triggered release. The Python package build is
  covered by `task build` in CI. A release workflow can be added later when
  a publishing strategy (PyPI, container registry) is decided.
- **Container tests in CI:** `task test:container` requires podman-compose and
  a running container stack. This can be added as a future job if needed.
- **Custom GitHub Actions:** crosscodex has `.github/actions/go-setup` and
  `.github/actions/integration-setup` composite actions for Go toolchain
  setup. This project uses uv + task which are simpler — off-the-shelf
  actions suffice, no custom composites needed.

## Action Pinning

All third-party actions are pinned by commit SHA (not tag) per crosscodex
convention, with a trailing comment noting the version for human readers.
