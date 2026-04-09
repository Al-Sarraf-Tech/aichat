# aichat-mcp Tools Expansion — Design Spec

**Date:** 2026-04-09
**Branch:** `feat/mcp-tools-expansion`
**Repo:** `~/git/aichat`

## Overview

Add 6 new tools to aichat-mcp: `monitor`, `git`, `notify`, `iot`, `ssh`, `log`. All tools are modularized under `docker/mcp/tools/` — existing tools in `app.py` remain untouched. A shared async SSH executor (`_ssh.py`) provides the foundation for 5 of the 6 tools.

## Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Architecture | Modularize new tools only (Option B) | Keep 498KB app.py stable; new tools get own modules |
| Module layout | Flat modules + shared SSH helper (Approach 1) | Explicit dispatch, debuggable, consistent with existing codebase |
| notify channel | Telegram bot | User's preferred communication channel |
| ssh host policy | Allowlist + Tailscale tailnet | Security: explicit fleet + any .ts.net host |
| git scope | Full gh proxy (read + write) | Multi-client use case needs full power |
| iot devices | Roku + Shield + extensible registry | Known devices first-class, future devices config-only |
| log scope | Search + aggregation | Manual grep gets tedious; watch/alert can layer on later |
| monitor scope | Fleet-wide + Tailscale | Complete infrastructure picture in one call |
| Thermal threshold | 85C warning | User-specified override |

## File Layout

```
docker/mcp/
  app.py                 # existing — add imports + registration block only
  tools/
    __init__.py           # exports TOOL_SCHEMAS + TOOL_HANDLERS for app.py
    _ssh.py               # shared async SSH executor (foundation)
    ssh.py                # direct SSH access tool
    monitor.py            # fleet-wide infrastructure dashboard
    git.py                # full git/GitHub proxy
    notify.py             # Telegram notifications
    iot.py                # IoT device control
    log.py                # log search and aggregation

tests/
  test_architecture.py    # existing — extend for new tools
  tools/
    conftest.py           # shared fixtures (mock SSH, mock httpx, mock Telegram)
    test_ssh_executor.py  # unit: _ssh.py foundation
    test_ssh_tool.py      # unit: ssh.py tool handlers
    test_monitor.py       # unit: monitor.py
    test_git.py           # unit: git.py
    test_notify.py        # unit: notify.py
    test_iot.py           # unit: iot.py
    test_log.py           # unit: log.py
    test_integration.py   # integration: live SSH + Docker stack
    test_e2e.py           # e2e: full MCP call chain via HTTP
```

## Foundation: `tools/_ssh.py`

### SSHExecutor Class

```python
class SSHExecutor:
    def __init__(self,
                 key_path: str = "/app/.ssh/team_key",
                 default_user: str = "jalsarraf",
                 default_port: int = 22,
                 host_allowlist: list[str] | None = None,
                 tailscale_suffix: str = ".ts.net",
                 timeout: float = 30.0,
                 max_failures: int = 3,
                 recovery_window: float = 30.0): ...

    async def run(self, host: str, command: str, *,
                  timeout: float | None = None,
                  user: str | None = None,
                  port: int | None = None) -> SSHResult: ...

    async def run_multi(self, hosts: list[str], command: str, *,
                        timeout: float | None = None) -> dict[str, SSHResult]: ...

    def is_host_allowed(self, host: str) -> bool: ...
```

### SSHResult

```python
@dataclasses.dataclass
class SSHResult:
    stdout: str
    stderr: str
    returncode: int
    host: str
    elapsed: float  # seconds
```

### Behaviors

- **Host validation:** Checks against explicit allowlist (`amarillo`, `dominus`, `sentinel`, `superemus`, `host.docker.internal`) plus any hostname ending in `.ts.net`. Rejects everything else before opening a connection.
- **Host resolution:** `amarillo` maps to `host.docker.internal` (container runs on amarillo). Other fleet names resolve via Tailscale DNS (e.g., `dominus.tail9bdca.ts.net`).
- **Circuit breaker:** Per-host. After `max_failures` consecutive failures, host is marked down for `recovery_window` seconds. Matches existing `agents.py` pattern.
- **Timeout:** Per-call override, default 30s. Uses `asyncio.wait_for` wrapping `asyncio.create_subprocess_exec`.
- **Error sanitization:** Strips internal IPs, paths, hostnames from error output via `_sanitize_ssh_error`.
- **Concurrency:** `run_multi` fires all hosts in parallel via `asyncio.gather`.
- **SSH flags:** `-o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 -o BatchMode=yes`.

---

## Tool 1: `ssh` — Direct SSH Access

### Actions

| Action | Required Params | Description |
|--------|----------------|-------------|
| `exec` | `host`, `command` | Run command on remote host, return stdout/stderr/exit code |
| `upload` | `host`, `local_path`, `remote_path` | SCP file from /workspace/ to remote host |
| `download` | `host`, `remote_path`, `local_path` | SCP file from remote host to /workspace/ |
| `test` | `host` | Connectivity check — latency and reachability |
| `list_hosts` | — | Return allowlist + live Tailscale node status |

### Security

- Host validated against allowlist + `.ts.net` suffix before any connection.
- Upload/download restricted to `/workspace/` on the local side.
- `list_hosts` runs `tailscale status --json` on amarillo via SSH.

---

## Tool 2: `monitor` — Fleet-Wide Infrastructure Dashboard

### Actions

| Action | Required Params | Description |
|--------|----------------|-------------|
| `overview` | — | Full fleet dashboard: thermals, CPU, RAM, disk, GPU, containers, Tailscale |
| `containers` | — (optional `host`) | Docker container status (default amarillo) |
| `thermals` | — | CPU/GPU temperatures across fleet, flags above 85C |
| `disk` | — | Disk usage across fleet, flags mounts above 85% |
| `gpu` | — | Intel Arc A380 — utilization, memory, active processes |
| `services` | — | Health check LM Studio, ComfyUI, Qdrant, aichat stack, GHA runners |
| `tailscale` | — | Node status — online/offline, last seen, IPs, OS |

### Implementation

- `overview` calls `run_multi` on all fleet hosts concurrently: `sensors -j 2>/dev/null; free -b; df -B1; nproc`
- `containers` runs `docker ps --format json` via SSH
- `thermals` uses `sensors -j` (Linux) or WMI query (dominus/PowerShell)
- `tailscale` runs `tailscale status --json` on amarillo
- `services` hits known health endpoints via SSH + curl
- Unreachable hosts within 5s timeout get `[unreachable]` marker, not an error

### Thermal Alert

If any reading exceeds 85C, response includes `WARNING` prefix. Does not auto-notify — user wires to `notify` if desired.

### Example `overview` Response

```
=== FLEET OVERVIEW ===

amarillo (local)
  CPU: 45C (4c/8t, 23% avg)  RAM: 12.4/31.8 GB  GPU: Arc A380 38C 0% util
  Disk: / 42% | /mnt/nvmeINT 18% | /mnt/ssd1tb 61%
  Containers: 12 running, 0 unhealthy
  Runners: shell-slim-1 ok  rust-slim-1 ok  python-slim-1 ok  node-slim-1 ok  haskell-slim-1 ok

dominus (192.168.50.2)
  CPU: 52C (16c/32t, 8% avg)  RAM: 24.1/64.0 GB
  Disk: C:\ 55% | D:\ 32%

sentinel / superemus
  [online/offline + basic vitals]

Tailscale: 4/4 nodes online
Services: LM Studio ok  ComfyUI ok  Qdrant ok  PostgreSQL ok
```

---

## Tool 3: `git` — Full Git/GitHub Proxy

### Actions

| Action | Required Params | Description |
|--------|----------------|-------------|
| `status` | — (optional `repo`) | Multi-repo overview: uncommitted changes, branches, ahead/behind |
| `log` | `repo` | Recent commits (default 10) |
| `diff` | `repo` | Show diff (staged, unstaged, or between refs) |
| `ci` | `repo` | CI run status — recent workflow runs, pass/fail/pending |
| `trigger_ci` | `repo`, `workflow` | Re-trigger a workflow run |
| `prs` | — (optional `repo`) | List open PRs |
| `create_pr` | `repo`, `title`, `branch` | Create a PR |
| `merge` | `repo`, `pr_number` | Merge a PR |
| `push` | `repo`, `branch` | Push a branch to remote |
| `issues` | `repo` | List or create issues |
| `scorecard` | — | CI health across ALL repos |

### Implementation

- All actions run `gh` or `git` CLI on amarillo via SSH.
- `status` iterates `~/git/*/` with `git -C <repo> status --porcelain` + `git rev-list --left-right --count HEAD...@{upstream}` concurrently.
- `scorecard` runs `gh run list --limit 1 --json conclusion` across all repos.
- `repo` parameter validated against actual directories in `~/git/`. Rejects anything else.

---

## Tool 4: `notify` — Telegram Notifications

### Actions

| Action | Required Params | Description |
|--------|----------------|-------------|
| `send` | `text` | Send text message (Markdown) |
| `send_photo` | `path` or `url` | Send image with optional caption |
| `send_document` | `path` | Send file with optional caption |
| `send_alert` | `text`, `severity` | Formatted alert with severity icon + timestamp |

### Implementation

- Direct httpx to `https://api.telegram.org/bot{token}/`. No SSH needed.
- Token from `TELEGRAM_BOT_TOKEN`, chat ID from `TELEGRAM_CHAT_ID` env vars.
- `send_alert` severity icons: info = information, warning = warning sign, critical = red siren.
- `send_photo` accepts workspace file path (multipart upload) or URL (Telegram fetches).
- All messages use `parse_mode=Markdown`.
- Rate limiting: returns error if Telegram returns 429, no retry.

### Alert Format

```
[severity icon] CRITICAL — 2026-04-09 14:32:01
amarillo CPU at 91C — throttle threshold exceeded
```

---

## Tool 5: `iot` — IoT Device Control

### Device Registry

```python
DEVICES = {
    "roku": {
        "name": "TCL Roku TV",
        "type": "roku",
        "host": "192.168.50.13",
        "port": 8060,
        "protocol": "http",
    },
    "shield": {
        "name": "NVIDIA Shield TV",
        "type": "shield",
        "host": "192.168.50.99",
        "port": 8022,
        "protocol": "ssh",
    },
}
```

### Actions

| Action | Required Params | Description |
|--------|----------------|-------------|
| `list_devices` | — | All registered devices + online/offline status |
| `power` | `device` | Power on (WOL) / off / toggle |
| `keypress` | `device`, `key` | Send remote control keypress |
| `launch` | `device`, `app` | Launch app by name or ID |
| `query` | `device` | Device state — active app, media player, device info |
| `apps` | `device` | List installed apps (Roku) |
| `command` | `device`, `command` | Raw command on SSH-based devices (Shield) |

### Protocol Handling

**Roku (HTTP/ECP):**
- `keypress` -> `POST /keypress/{key}`
- `launch` -> `POST /launch/{app_id}` (resolves name to ID via `GET /query/apps`)
- `query` -> `GET /query/active-app`, `GET /query/media-player`, `GET /query/device-info`
- `power` on -> WOL magic packet to MAC; off -> `POST /keypress/PowerOff`
- `apps` -> `GET /query/apps` (XML -> name/ID list)

**Shield (SSH via `_ssh.py`):**
- `keypress` -> `input keyevent KEYCODE_{key}`
- `launch` -> `am start -n {package}/{activity}`
- `query` -> `dumpsys media_session`, `dumpsys activity activities | grep mFocused`
- `power` on -> WOL; off -> `input keyevent KEYCODE_SLEEP`
- `command` -> arbitrary SSH command

**Adding devices:** Add entry to `DEVICES` dict. If protocol is `http`, generic HTTP handler. If `ssh`, routes through `_ssh.py`. New device *types* need a protocol adapter.

---

## Tool 6: `log` — Log Search and Aggregation

**Target:** `/mnt/nvmeINT/logs/` on amarillo via SSH.

### Actions

| Action | Required Params | Description |
|--------|----------------|-------------|
| `list` | — | Log files with sizes and last-modified times |
| `search` | `pattern` | Grep regex across logs — supports file filter, max results |
| `tail` | `file` | Last N lines (default 50, max 500) |
| `count` | `pattern` | Pattern occurrences grouped by time window (hour/day) |
| `errors` | — | Scan for ERROR/FATAL/Exception/panic/Traceback, grouped by file + frequency |
| `between` | `file`, `start`, `end` | Log lines between two ISO 8601 timestamps |

### Implementation

- `list` -> `ls -lhS --time-style=long-iso /mnt/nvmeINT/logs/` via SSH
- `search` -> `grep -rPn '{pattern}' /mnt/nvmeINT/logs/{file}` with `--max-count`
- `tail` -> `tail -n {lines} /mnt/nvmeINT/logs/{file}` clamped to 500
- `count` -> `grep -cP` + awk time-bucketing, returns table with spike markers
- `errors` -> scans `*.log` for common patterns, aggregates counts per file
- `between` -> awk timestamp range filter

### Security

- `file` parameter sanitized: strips `../`, rejects absolute paths, allows filenames/globs within `/mnt/nvmeINT/logs/` only.
- `pattern` passed to `grep -P` with proper SSH command quoting to prevent injection.
- Output capped at `max_results` (default 100, hard max 500).

---

## Docker Integration

### Environment Variables (added to docker-compose.yml)

```yaml
aichat-mcp:
  environment:
    - TELEGRAM_BOT_TOKEN=${TELEGRAM_BOT_TOKEN}
    - TELEGRAM_CHAT_ID=${TELEGRAM_CHAT_ID}
```

No new ports, volumes, or device mounts needed. SSH key already mounted at `/app/.ssh/team_key`. Roku ECP is HTTP-accessible from inside the container. Shield SSH goes through `_ssh.py`.

### .env File

```
TELEGRAM_BOT_TOKEN=<token>
TELEGRAM_CHAT_ID=7274294368
```

Already in `.env` (gitignored).

---

## Registration in app.py

Minimal changes to `app.py`:

1. **Import block** (top of file):
```python
from tools import TOOL_SCHEMAS, TOOL_HANDLERS
```

2. **Schema registration** (after existing `_TOOLS` list):
```python
_TOOLS.extend(TOOL_SCHEMAS)
```

3. **Dispatch** (in `_call_tool`, after `_resolve_mega_tool`):
```python
if name in TOOL_HANDLERS:
    return await TOOL_HANDLERS[name](args)
```

Three lines added to app.py. Everything else is in `tools/`.

---

## Testing Strategy

### Layer 1: Unit Tests (~150 tests)

Each tool module gets its own test file with mocked dependencies.

| File | Coverage | Mock Strategy |
|------|----------|---------------|
| `test_ssh_executor.py` | Allowlist, circuit breaker, timeout, sanitization, run_multi | Mock `asyncio.create_subprocess_exec` |
| `test_ssh_tool.py` | All 5 actions, arg validation, path restrictions | Mock `SSHExecutor` |
| `test_monitor.py` | Output parsing, fleet aggregation, 85C threshold, unreachable hosts | Mock `SSHExecutor.run_multi` |
| `test_git.py` | All 11 actions, repo validation, scorecard formatting | Mock `SSHExecutor` |
| `test_notify.py` | All 4 actions, Markdown formatting, rate limit handling | Mock `httpx.AsyncClient` |
| `test_iot.py` | Registry lookup, ECP XML parsing, WOL, Shield commands | Mock `httpx` + `SSHExecutor` |
| `test_log.py` | Path traversal prevention, grep parsing, time-bucket aggregation | Mock `SSHExecutor` |

**Every action gets:** happy path + at least 2 error paths (missing arg, unreachable service, malformed response).

**Security tests:** Path traversal attempts, host allowlist bypass, command injection strings.

### Layer 2: Integration Tests (~30 tests)

Requires live Docker stack + SSH. Skipped in CI.

```python
@pytest.mark.integration
@pytest.mark.skipif(not SSH_REACHABLE, reason="SSH tunnel not available")
```

Non-destructive smoke test per tool against real infrastructure.

### Layer 3: E2E Tests (~20 tests)

Full MCP protocol chain via httpx POST to `http://localhost:8096/mcp`.

Covers: each tool's primary action via MCP, response block format, error format, tool discovery.

### Layer 4: Architecture Contract Tests

Extend existing `test_architecture.py` (runs in CI, no Docker):

- All 6 new tools in `_TOOLS` schema list
- Valid JSON Schema on every `inputSchema`
- Every enum action has a handler
- No duplicate tool names
- Description format compliance

### Layer 5: Metadata and Code Reviews

| Review | Checks |
|--------|--------|
| Schema | Valid JSON Schema Draft 7, required fields correct, descriptions actionable |
| Security | Path traversal, allowlist enforcement, no secrets in output, error sanitization |
| Response format | All handlers return `list[dict]`, error format `_text("tool: msg")` |
| Dependency | No new pip dependencies (httpx + asyncio already available) |
| Docker | .env vars passed through compose, no new ports/volumes/mounts |

### Test Commands

```bash
# Unit tests (fast, no Docker)
pytest tests/tools/ -v --ignore=tests/tools/test_integration.py --ignore=tests/tools/test_e2e.py

# Architecture contracts (CI-safe)
pytest tests/test_architecture.py -v

# Integration (needs Docker stack + SSH)
pytest tests/tools/test_integration.py -v -m integration

# E2E (needs full stack)
pytest tests/tools/test_e2e.py -v -m e2e

# Full suite
pytest tests/ -v
```

---

## Build Sequence

1. `_ssh.py` — foundation, testable in isolation
2. `ssh.py` — depends only on `_ssh.py`
3. `notify.py` — no SSH dependency, fully independent
4. `log.py` — depends on `_ssh.py`
5. `monitor.py` — depends on `_ssh.py`, benefits from `log.py` patterns
6. `git.py` — depends on `_ssh.py`
7. `iot.py` — depends on `_ssh.py` + httpx
8. `tools/__init__.py` — registry, imports all modules
9. `app.py` changes — 3-line integration
10. Docker compose — add Telegram env vars
11. Unit tests — all 7 test files
12. Architecture contract test updates
13. Integration tests
14. E2E tests
15. Code review + metadata review
