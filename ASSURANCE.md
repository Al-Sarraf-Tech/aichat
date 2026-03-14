# Software Assurance

This document describes the automated quality and security controls enforced on every change to the aichat platform.

---

## Pipeline Architecture

The CI/CD system is organized as a multi-stage directed acyclic graph (DAG). Each stage gates the next — no downstream job can run until its prerequisites pass. All jobs run on self-hosted Linux runners with Docker.

```
push / PR to main or dev
    |
    v
Stage 1 — Static Analysis
    |   ruff lint (docker/, src/, tests/)
    |   architecture contract tests (pytest tests/test_architecture.py)
    |
    +---> Stage 2a — Regression Tests (25 parallel jobs)
    |       One ephemeral container per test file
    |       Each job: checkout -> pip install -> pytest -> teardown
    |       fail-fast: false (all 25 run to completion)
    |
    +---> Stage 2b — Build Images (6 parallel jobs)
            One build per service: data, vision, docs, sandbox, mcp, jupyter
            Layer cache persisted via :cache tag on self-hosted runner
            Ephemeral :ci-<sha> tag for downstream consumption
            OCI labels: revision, source URL
            |
            +---> Stage 3a — Smoke Tests
            |       Requires: regression + build-images
            |       Full stack brought up (db, vector, data, vision, docs, sandbox, searxng, mcp)
            |       Health-check polling loop (up to 2 min)
            |       Runner joins Docker network for direct service-to-service access
            |       pytest -m smoke tests/test_smoke.py
            |       Teardown: always (even on failure)
            |
            +---> Stage 3b — Package Build
            |       Requires: regression
            |       Python wheel + sdist (python -m build)
            |       Standalone binary (PyInstaller --onefile)
            |       Build provenance attestation (actions/attest-build-provenance@v2)
            |       Artifact uploaded for release consumption
            |
            +---> Stage 3c — SBOM + Vulnerability Scan (6 parallel jobs)
                    Requires: build-images
                    Per-service SPDX-JSON SBOM (anchore/sbom-action)
                    Per-service CycloneDX-JSON SBOM (syft)
                    Vulnerability scan (grype --fail-on high)
                    Ephemeral :ci-<sha> image removed after scan
                    |
                    v
            Stage 4 — Cleanup (runs always)
                    Remove all :ci-<sha> image tags
                    Prune dangling layers
                    Retain :cache tags for next run
```

---

## Regression Testing

**25 parallel test jobs** run independently in ephemeral containers:

| Test Module | Coverage Area |
|---|---|
| `test_tui` | Terminal UI rendering and interaction |
| `test_tool_args` | Tool argument parsing and validation |
| `test_tool_scheduler` | Scheduled task execution |
| `test_toolkit` | Toolkit container integration |
| `test_streaming` | LLM streaming response handling |
| `test_thinking` | Reasoning/thinking token handling |
| `test_ssh_keys` | SSH key management |
| `test_sanitizer` | Input sanitization |
| `test_keybinds` | Keyboard shortcut handling |
| `test_cli_mcp_server` | CLI-to-MCP server integration |
| `test_compaction` | Context compaction |
| `test_compaction_enhancements` | Advanced compaction features |
| `test_conversation_db` | Conversation persistence |
| `test_database_tools` | Database tool operations |
| `test_orchestrate` | Multi-step orchestration |
| `test_browser_gpu` | Browser + GPU integration |
| `test_image_pipeline` | Image processing pipeline |
| `test_image_rendering` | Image rendering |
| `test_face_recognition` | Face detection and recognition |
| `test_new_services` | Consolidated service tests |
| `test_finetuning` | Fine-tuning pipeline |
| `test_batch_jobs` | Batch job processing |
| `test_image_search_e2e` | End-to-end image search |
| `test_vision_tools` | Vision tool integration |
| `test_tools_e2e` | End-to-end tool execution |

Each job uses an isolated `COMPOSE_PROJECT_NAME` (including the GitHub run ID and test name) so parallel containers never collide. Containers are torn down with `if: always()` regardless of pass/fail.

A **batched variant** (`ci-batched.yml`) groups tests into 4 logical batches (core, compaction, services, platform) with `max-parallel: 4` for resource-constrained scenarios. This is triggered manually via `workflow_dispatch`.

---

## Security Scanning

The **Security CI** workflow (`security.yml`) runs on every push, every PR, and weekly (Monday 04:00 UTC):

| Scanner | Scope | Configuration |
|---|---|---|
| **Bandit** (SAST) | `src/` and `docker/` separately | `-ll` (medium+ severity), fail on findings |
| **pip-audit** | Main package + each service `requirements.txt` (7 targets) | Fail on known vulnerabilities |
| **TruffleHog** | Full git history (`fetch-depth: 0`) | `--only-verified --fail` |
| **shellcheck** | `install.sh`, `uninstall.sh`, `entrypoint`, `scripts/`, `.github/scripts/` | Fail on errors |
| **shfmt** | Same shell files | Diff mode (`-d`), fail on formatting violations |

### Vision Stack Security (Isolated Pipeline)

The **Vision Stack CI** (`vision-ci.yml`) runs on changes to `vision/`:

- Bandit SAST on `vision/mcp-server/app` and `vision/services/vision-router/app`
- TruffleHog secret scan on `vision/` subtree
- mypy type checking (continue-on-error for gradual adoption)

---

## SBOM and Vulnerability Management

### CI-Integrated SBOM (per commit)

Every CI run generates SBOMs for all 6 service images:

- **SPDX-JSON** via `anchore/sbom-action`
- **CycloneDX-JSON** via `syft`
- **Vulnerability scan** via `grype --fail-on high`

Ephemeral images are removed after scanning. SBOM artifacts are retained for 30 days in CI.

### Dedicated SBOM Workflow (weekly + on push to main)

The standalone **SBOM & Vulnerability Audit** workflow (`sbom.yml`) provides deeper analysis:

- Builds all 5 core service images from scratch
- Generates SPDX + CycloneDX SBOMs per image
- Generates human-readable SBOM summary tables
- Runs grype with `--fail-on critical` threshold
- Annotates GitHub workflow with vulnerability counts
- Python dependency SBOM via `cyclonedx-bom` (CycloneDX for pip environment)
- `pip-audit` against each service's `requirements.txt`
- **90-day artifact retention** for all SBOMs and vulnerability reports
- Aggregate summary job collating all results

---

## Release Pipeline

Triggered by semver tags (`v*.*.*`) or manual `workflow_dispatch`:

1. **Build Python distribution** — wheel + sdist with build provenance attestation
2. **Build standalone binary** — PyInstaller single-file Linux binary with build provenance attestation
3. **Build all 6 Docker images** — tagged with release version, OCI labels for revision + version + source
4. **Generate SBOMs** — SPDX + CycloneDX per release image
5. **Create GitHub Release** — all assets (binary, wheel, image tarballs, SBOMs) attached via `softprops/action-gh-release`

Build provenance is attested via `actions/attest-build-provenance` using OIDC (`id-token: write`).

---

## Vision Stack Pipeline

The vision subsystem has its own isolated CI (`vision-ci.yml`) triggered only by changes under `vision/`:

1. **Lint** — ruff + mypy on `mcp-server` and `vision-router`
2. **Security** — bandit + TruffleHog
3. **Unit tests** — `vision/tests/unit` with pytest
4. **Build** — Docker images for `vision-mcp` and `vision-router` (GHA cache)
5. **SBOM** — SPDX generation + grype vulnerability scan

---

## Concurrency and Resource Management

| Control | Implementation |
|---|---|
| Duplicate run cancellation | `concurrency.cancel-in-progress: true` per workflow |
| Ephemeral containers | Unique `COMPOSE_PROJECT_NAME` per job (includes run ID) |
| Guaranteed teardown | `if: always()` on all teardown steps |
| Layer cache reuse | `:cache` tag persisted on self-hosted runner between runs |
| Dangling image cleanup | Final cleanup job prunes layers and removes `:ci-<sha>` tags |
| Release concurrency | `cancel-in-progress: false` (releases must complete) |
| SBOM workflow | `cancel-in-progress: false` (audit must complete) |

---

## Artifact Retention

| Artifact | Retention |
|---|---|
| CI image tarballs (`:ci-<sha>`) | 1 day (ephemeral, consumed by downstream jobs) |
| SBOM files (SPDX, CycloneDX) | 90 days |
| Vulnerability reports | 90 days |
| Python SBOM (`cyclonedx-bom`) | 90 days |
| Package artifacts (wheel, binary) | Default (GitHub Actions default) |
| Unit test results | Default |

---

## Running Locally

```bash
# Lint (ruff + mypy)
make lint

# Full test suite
make test

# Smoke tests (requires running stack)
make smoke

# Security checks (shellcheck, bandit, safety, semgrep, trivy)
make security-checks

# Individual test module
pytest tests/test_tool_args.py -v --tb=short

# Run all regression tests (no smoke)
pytest -q -m "not smoke" tests/

# Smoke tests only (requires docker compose up -d)
pytest -m smoke tests/test_smoke.py -v
```

---

## Permissions Model

| Workflow | Permissions |
|---|---|
| CI (`ci.yml`) | `contents: write`, `packages: write`, `id-token: write`, `actions: write`, `attestations: write` |
| Security CI | `contents: read`, `security-events: write`, `actions: write` |
| SBOM | `contents: read`, `packages: write`, `id-token: write` |
| Release | `contents: write`, `packages: write`, `id-token: write`, `actions: write`, `attestations: write` |
| Batched regression | `contents: read` |
| Vision CI | Default (read) |

The `id-token: write` permission enables OIDC-based Sigstore signing for build attestations and SBOM attestations.
