# GitHub Actions Workflows Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add CI and MegaLinter GitHub Actions workflows to crosscodex-ingestion, delegating all build/lint/test work to `task` commands.

**Architecture:** Two independent workflows — `ci.yml` for build/lint/test (two jobs: `build` gates `integration`) and `megalinter.yml` for language-agnostic linting via the shared `trevor-vaughan/megalint-config` action. Both follow the patterns from `complytime-labs/crosscodex`.

**Tech Stack:** GitHub Actions, Task (taskfile.dev), uv (astral.sh), trevor-vaughan/megalint-config

## Global Constraints

- All third-party actions pinned by full commit SHA with version comment
- `persist-credentials: false` on all checkout steps
- `permissions` block on every workflow, least-privilege
- All build/lint/test work delegates to `task` — no raw `uv run`, `pytest`, or `ruff` in workflow YAML
- Target branch: `main`

## File Map

| Path | Action | Responsibility |
|------|--------|----------------|
| `.github/workflows/ci.yml` | Create | CI pipeline: build + integration jobs |
| `.github/workflows/megalinter.yml` | Create | MegaLinter analysis with SARIF + artifacts |

## Action SHA Reference

These are the pinned SHAs used throughout the plan:

| Action | SHA | Version |
|--------|-----|---------|
| `actions/checkout` | `9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0` | v7.0.0 |
| `astral-sh/setup-uv` | `11f9893b081a58869d3b5fccaea48c9e9e46f990` | v8.3.2 |
| `go-task/setup-task` | `01a4adf9db2d14c1de7a560f09170b6e0df736aa` | v2.1.0 |
| `trevor-vaughan/megalint-config` | `2da93ca6173ca20edcde4be599de20f5a79197c6` | v0.4.0 |
| `actions/upload-artifact` | `043fb46d1a93c77aae656e7c1c64a875d1fc6a0a` | v7.0.1 |
| `github/codeql-action/upload-sarif` | `54f647b7e1bb85c95cddabcd46b0c578ec92bc1a` | v4.36.3 |

---

### Task 1: Create CI workflow

**Files:**
- Create: `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: Taskfile tasks `setup`, `proto`, `lint`, `build`, `test:default`, `test:integration`
- Produces: CI status checks for `build` and `integration` jobs

- [ ] **Step 1: Create the `.github/workflows/` directory**

```bash
mkdir -p .github/workflows
```

- [ ] **Step 2: Write `.github/workflows/ci.yml`**

```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

permissions:
  contents: read

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0 # v7.0.0
        with:
          persist-credentials: false

      - uses: astral-sh/setup-uv@11f9893b081a58869d3b5fccaea48c9e9e46f990 # v8.3.2

      - uses: go-task/setup-task@01a4adf9db2d14c1de7a560f09170b6e0df736aa # v2.1.0
        with:
          repo-token: ${{ github.token }}

      - name: Setup
        run: task setup

      - name: Generate proto stubs
        run: task proto

      - name: Lint
        run: task lint

      - name: Build
        run: task build

      - name: Unit tests
        run: task test:default

  integration:
    needs: [build]
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0 # v7.0.0
        with:
          persist-credentials: false

      - uses: astral-sh/setup-uv@11f9893b081a58869d3b5fccaea48c9e9e46f990 # v8.3.2

      - uses: go-task/setup-task@01a4adf9db2d14c1de7a560f09170b6e0df736aa # v2.1.0
        with:
          repo-token: ${{ github.token }}

      - name: Setup
        run: task setup

      - name: Generate proto stubs
        run: task proto

      - name: Integration tests
        run: task test:integration
```

- [ ] **Step 3: Validate YAML syntax**

```bash
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml'))"
```

Expected: no output (valid YAML).

- [ ] **Step 4: Verify task targets exist**

```bash
task --list | grep -E '(setup|proto|lint|build|test:default|test:integration)'
```

Expected: all six tasks listed.

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: add CI workflow with build and integration jobs

Delegates all work to task commands: setup, proto, lint, build,
test:default, test:integration. Integration job gates on build."
```

---

### Task 2: Create MegaLinter workflow

**Files:**
- Create: `.github/workflows/megalinter.yml`

**Interfaces:**
- Consumes: `trevor-vaughan/megalint-config` action
- Produces: MegaLinter status check, SARIF upload, PR comments, archived reports

- [ ] **Step 1: Write `.github/workflows/megalinter.yml`**

```yaml
name: MegaLinter

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]
  schedule:
    - cron: "17 3 * * 1"
  workflow_dispatch:

permissions:
  contents: read

concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: ${{ github.ref != 'refs/heads/main' }}

jobs:
  megalinter:
    name: MegaLinter Analysis
    runs-on: ubuntu-latest

    permissions:
      contents: read
      security-events: write
      pull-requests: write

    steps:
      - name: Checkout code
        uses: actions/checkout@9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0 # v7.0.0
        with:
          fetch-depth: 0
          persist-credentials: false

      - name: MegaLinter
        id: ml
        uses: trevor-vaughan/megalint-config@2da93ca6173ca20edcde4be599de20f5a79197c6 # v0.4.0
        with:
          validate-all-codebase: ${{ github.event_name == 'schedule' }}
          github-comment: "true"

      - name: Archive reports
        if: success() || failure()
        uses: actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a # v7.0.1
        with:
          name: megalinter-reports
          path: ${{ steps.ml.outputs.reports-dir }}

      - name: Upload SARIF to GitHub Code Scanning
        if: ${{ (success() || failure()) && steps.ml.outputs.sarif-has-results == 'true' }}
        uses: github/codeql-action/upload-sarif@54f647b7e1bb85c95cddabcd46b0c578ec92bc1a # v4.36.3
        with:
          sarif_file: ${{ steps.ml.outputs.sarif-file }}
```

- [ ] **Step 2: Validate YAML syntax**

```bash
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/megalinter.yml'))"
```

Expected: no output (valid YAML).

- [ ] **Step 3: Verify workflow structure matches crosscodex pattern**

Compare key elements against the reference:
- `concurrency` block with cancel-in-progress for non-main ✓
- `validate-all-codebase` conditional on schedule ✓
- `sarif-has-results` gate on SARIF upload ✓
- `success() || failure()` on artifact/SARIF steps ✓
- All actions pinned by SHA ✓

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/megalinter.yml
git commit -m "ci: add MegaLinter workflow with SARIF and PR comments

Uses trevor-vaughan/megalint-config shared action. Full codebase
scan on weekly schedule, changed-files only on push/PR. Archives
reports and uploads SARIF to GitHub Code Scanning."
```

---

### Task 3: Add megalinter-reports to .gitignore

**Files:**
- Modify: `.gitignore`

**Interfaces:**
- Consumes: MegaLinter report output directory name
- Produces: gitignore entry preventing accidental commit of local MegaLinter runs

- [ ] **Step 1: Append megalinter-reports to .gitignore**

Add the following line to the end of `.gitignore`:

```
megalinter-reports/
```

- [ ] **Step 2: Verify the entry was added**

```bash
grep 'megalinter-reports' .gitignore
```

Expected: `megalinter-reports/`

- [ ] **Step 3: Commit**

```bash
git add .gitignore
git commit -m "chore: gitignore megalinter-reports directory"
```
