# CI/CD Hardening Report

**Repository:** jalsarraf0/aichat
**Date:** 2026-03-14
**Branch:** ci/assurance-hardening

---

## Executive Summary

The aichat repository has a production-grade CI/CD pipeline across 6 GitHub Actions workflows totaling approximately 700+ lines of workflow YAML. The pipeline implements multi-stage gating, parallel testing, SBOM generation in dual formats, vulnerability scanning, build attestations, and automated release packaging. This report documents the current hardening posture and identifies any remaining gaps.

---

## Workflow Inventory

| Workflow | File | Trigger | Lines | Purpose |
|---|---|---|---|---|
| CI | `ci.yml` | push (main/dev), PR, manual | 384 | Full pipeline: lint, test, build, smoke, SBOM, cleanup |
| Regression Batched | `ci-batched.yml` | manual | 55 | Resource-efficient batched test runner (4 batches) |
| Security CI | `security.yml` | push (main/dev), PR, manual, weekly cron | 146 | SAST, dependency audit, secret scan, shell lint |
| SBOM & Vuln Audit | `sbom.yml` | push (main), weekly cron, manual | 237 | Standalone deep SBOM + vulnerability analysis |
| Release | `release.yml` | semver tag, manual | 247 | Build, SBOM, attest, publish GitHub Release |
| Vision Stack CI | `vision-ci.yml` | push/PR (vision/** paths), manual | 207 | Isolated pipeline for vision subsystem |

**Total:** 6 workflows, ~1,276 lines of workflow configuration.

---

## Hardening Controls Audit

### Supply Chain Security

| Control | Status | Implementation |
|---|---|---|
| Build provenance attestation | Present | `actions/attest-build-provenance@v2` on binary + wheel |
| OIDC-based signing | Present | `id-token: write` for Sigstore integration |
| SBOM generation (SPDX) | Present | `anchore/sbom-action@v0` per service image |
| SBOM generation (CycloneDX) | Present | `syft` per service image |
| Python dependency SBOM | Present | `cyclonedx-bom` for pip environment |
| Vulnerability scanning | Present | `grype --fail-on high` (CI), `--fail-on critical` (standalone) |
| Pinned action versions | Present | All actions use `@v4`, `@v5`, `@v3`, `@v2` major version pins |
| OCI image labels | Present | `org.opencontainers.image.revision`, `.source`, `.version` |

### Static Analysis

| Control | Status | Implementation |
|---|---|---|
| Python linting | Present | `ruff check` on `docker/`, `src/`, `tests/` |
| Architecture contract tests | Present | `pytest tests/test_architecture.py` gates all downstream |
| Type checking (vision) | Present | `mypy` on vision subsystem (continue-on-error) |

### Security Scanning

| Control | Status | Implementation |
|---|---|---|
| SAST (Python) | Present | `bandit -r` on `src/` and `docker/` separately, `-ll` threshold |
| Secret detection | Present | `trufflehog git --only-verified --fail` with full history |
| Dependency audit | Present | `pip-audit` on main package + 6 service requirements.txt files |
| Shell script lint | Present | `shellcheck` on all shell scripts |
| Shell formatting | Present | `shfmt -d` diff-mode check |

### Test Infrastructure

| Control | Status | Implementation |
|---|---|---|
| Parallel regression | Present | 25 independent jobs, one per test module |
| Ephemeral containers | Present | Unique `COMPOSE_PROJECT_NAME` per job |
| Guaranteed teardown | Present | `if: always()` on all cleanup steps |
| Smoke tests | Present | Full-stack integration test after build + regression |
| Vision unit tests | Present | Isolated pytest suite for vision subsystem |
| Fail-fast disabled | Present | `fail-fast: false` — all matrix jobs run to completion |

### Build Infrastructure

| Control | Status | Implementation |
|---|---|---|
| Concurrency control | Present | `cancel-in-progress: true` prevents duplicate runs |
| Layer cache reuse | Present | `:cache` tag persisted on self-hosted runner |
| Ephemeral image cleanup | Present | `:ci-<sha>` tags removed; dangling layers pruned |
| Build timeout guards | Present | Per-job `timeout-minutes` (5-45 min depending on job) |
| Self-hosted runners | Present | `[self-hosted, Linux, X64, docker]` labels |

### Release Pipeline

| Control | Status | Implementation |
|---|---|---|
| Tag-triggered release | Present | `v*.*.*` pattern |
| Manual release option | Present | `workflow_dispatch` with tag input |
| Multi-format packaging | Present | Wheel, sdist, PyInstaller binary, Docker images |
| SBOM attached to release | Present | SPDX + CycloneDX per image uploaded as release assets |
| Release notes generation | Present | Auto-generated with architecture summary |
| Release concurrency guard | Present | `cancel-in-progress: false` |

---

## Identified Strengths

1. **Defense in depth**: Five independent security scanners (bandit, TruffleHog, pip-audit, shellcheck, shfmt) across two dedicated workflows, plus grype for container vulnerabilities.

2. **Complete SBOM coverage**: Dual-format (SPDX + CycloneDX) SBOMs for every service image, plus Python environment SBOM. Both CI-integrated and standalone weekly audit.

3. **Ephemeral-everything testing**: Every test job runs in a completely isolated container with a unique project name. No shared state between parallel test runs.

4. **Self-healing runner**: The cleanup stage runs unconditionally (`if: always()`) and removes all ephemeral artifacts. `:cache` tags are intentionally preserved for build acceleration.

5. **Build attestation**: OIDC-based build provenance via Sigstore for both the Python package and standalone binary.

6. **Isolated vision pipeline**: The vision subsystem has its own lint, security, test, build, and SBOM pipeline that only triggers on relevant path changes.

---

## Recommendations

| Priority | Item | Detail |
|---|---|---|
| Low | Pin actions to SHA | Current major-version pins (`@v4`) are standard but SHA pins prevent supply chain attacks via compromised action releases. Consider for high-sensitivity workflows (release, SBOM). |
| Low | Add CodeQL or Semgrep to CI | The `security-checks` Makefile target includes semgrep, but it is not in the GitHub Actions security workflow. Consider adding as a CI job. |
| Low | SARIF upload | Security CI has `security-events: write` but does not currently upload SARIF results. Adding SARIF upload would enable GitHub Security tab integration. |
| Info | mypy strictness | Vision CI runs mypy with `continue-on-error: true`. Consider promoting to a blocking check as type coverage improves. |

---

## Conclusion

The aichat CI/CD pipeline is production-grade with comprehensive coverage across linting, testing, security scanning, SBOM generation, vulnerability management, and release automation. The multi-stage DAG architecture ensures that failures are caught early and resources are not wasted on downstream jobs when prerequisites fail. All recommendations above are low-priority enhancements — no critical or high-priority gaps were identified.
