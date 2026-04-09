# MCP Tools Expansion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add 6 new modularized tools (ssh, monitor, git, notify, iot, log) to aichat-mcp under `docker/mcp/tools/`, with full test coverage at unit, integration, and e2e layers.

**Architecture:** New tools live in `docker/mcp/tools/` as separate modules. A shared `_ssh.py` executor provides async SSH with host allowlisting and circuit breaker. `tools/__init__.py` exports `TOOL_SCHEMAS` and `TOOL_HANDLERS` dicts. Three lines added to `app.py` wire everything in. TDD throughout: tests written before implementation.

**Tech Stack:** Python 3.14, asyncio, httpx, pytest + pytest-asyncio, dataclasses. No new pip dependencies.

**Spec:** `docs/superpowers/specs/2026-04-09-mcp-tools-expansion-design.md`

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `docker/mcp/tools/__init__.py` | Create | Registry: exports TOOL_SCHEMAS + TOOL_HANDLERS |
| `docker/mcp/tools/_ssh.py` | Create | Shared async SSH executor with allowlist + circuit breaker |
| `docker/mcp/tools/ssh.py` | Create | SSH tool: exec, upload, download, test, list_hosts |
| `docker/mcp/tools/monitor.py` | Create | Monitor tool: overview, containers, thermals, disk, gpu, services, tailscale |
| `docker/mcp/tools/git.py` | Create | Git tool: status, log, diff, ci, trigger_ci, prs, create_pr, merge, push, issues, scorecard |
| `docker/mcp/tools/notify.py` | Create | Notify tool: send, send_photo, send_document, send_alert via Telegram |
| `docker/mcp/tools/iot.py` | Create | IoT tool: list_devices, power, keypress, launch, query, apps, command |
| `docker/mcp/tools/log.py` | Create | Log tool: list, search, tail, count, errors, between |
| `docker/mcp/app.py:1068` | Modify | Add import + extend _TOOLS + dispatch hook (3 lines) |
| `docker-compose.yml` | Modify | Add TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID env vars to aichat-mcp |
| `tests/tools/__init__.py` | Create | Empty package init |
| `tests/tools/conftest.py` | Create | Shared fixtures: mock SSHExecutor, mock httpx, mock Telegram |
| `tests/tools/test_ssh_executor.py` | Create | Unit tests for _ssh.py |
| `tests/tools/test_ssh_tool.py` | Create | Unit tests for ssh.py |
| `tests/tools/test_monitor.py` | Create | Unit tests for monitor.py |
| `tests/tools/test_git.py` | Create | Unit tests for git.py |
| `tests/tools/test_notify.py` | Create | Unit tests for notify.py |
| `tests/tools/test_iot.py` | Create | Unit tests for iot.py |
| `tests/tools/test_log.py` | Create | Unit tests for log.py |
| `tests/tools/test_integration.py` | Create | Integration tests against live stack |
| `tests/tools/test_e2e.py` | Create | E2E tests via MCP HTTP endpoint |
| `tests/test_architecture.py:64` | Modify | Add new tools to _STANDALONE_TOOLS, add schema checks |

---

## Build Sequence

12 tasks, each with TDD (test first, implement, verify, commit):

1. Foundation: `_ssh.py` + `__init__.py` + test fixtures + unit tests
2. SSH tool + unit tests
3. Notify tool + unit tests (no SSH dependency — can parallelize)
4. Log tool + unit tests
5. Monitor tool + unit tests
6. Git tool + unit tests
7. IoT tool + unit tests
8. App.py integration + Docker compose env vars
9. Architecture contract test updates
10. Integration tests (live stack)
11. E2E tests (MCP endpoint)
12. Full test run + code review + orchestrator scan

---

## Task 1: Foundation — `tools/_ssh.py` + Unit Tests

**Files:**
- Create: `docker/mcp/tools/__init__.py`
- Create: `docker/mcp/tools/_ssh.py`
- Create: `tests/tools/__init__.py`
- Create: `tests/tools/conftest.py`
- Create: `tests/tools/test_ssh_executor.py`

- [ ] **Step 1.1:** Create `docker/mcp/tools/__init__.py` — tool registry with `register()`, `TOOL_SCHEMAS`, `TOOL_HANDLERS`
- [ ] **Step 1.2:** Create `tests/tools/__init__.py` (empty) and `tests/tools/conftest.py` with `SSHResult` dataclass, `mock_ssh` fixture (AsyncMock SSHExecutor), `mock_httpx` fixture
- [ ] **Step 1.3:** Create `tests/tools/test_ssh_executor.py` with tests for: host allowlist (6 tests), host resolution (3 tests), circuit breaker (3 tests), run command (3 tests), run_multi (2 tests), error sanitization (3 tests)
- [ ] **Step 1.4:** Run tests, verify they fail (ModuleNotFoundError)
- [ ] **Step 1.5:** Create `docker/mcp/tools/_ssh.py` — SSHExecutor class with `is_host_allowed()`, `_resolve_host()`, circuit breaker (`_is_circuit_open`, `_record_failure`, `_record_success`), `_exec_ssh()`, `run()`, `run_multi()`, `_sanitize_ssh_error()`
- [ ] **Step 1.6:** Run tests, verify all pass
- [ ] **Step 1.7:** Commit: `feat(mcp): add SSH executor foundation with allowlist + circuit breaker`

**Key implementation details for _ssh.py:**

- Default allowlist: `amarillo, dominus, sentinel, superemus, host.docker.internal`
- Host aliases: `amarillo` -> `host.docker.internal`
- Bare hostnames get `{name}.tail9bdca.ts.net` suffix
- Tailscale suffix check: must end with `.ts.net` but not `.ts.net.something`
- SSH flags: `-i /app/.ssh/team_key -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 -o BatchMode=yes`
- Circuit breaker: per-host, `max_failures=3`, `recovery_window=30s`, resets on success
- `run_multi`: `asyncio.gather` with safe wrappers that catch exceptions per-host

---

## Task 2: SSH Tool + Unit Tests

**Files:**
- Create: `docker/mcp/tools/ssh.py`
- Create: `tests/tools/test_ssh_tool.py`

- [ ] **Step 2.1:** Create `tests/tools/test_ssh_tool.py` with tests for: exec (5 tests), test action (2 tests), list_hosts (1 test), upload path restriction (2 tests), download path restriction (1 test), unknown/missing action (2 tests)
- [ ] **Step 2.2:** Run tests, verify they fail
- [ ] **Step 2.3:** Create `docker/mcp/tools/ssh.py` — SCHEMA dict + `handle()` with exec/upload/download/test/list_hosts actions. Upload/download restricted to `/workspace/`. Calls `register(SCHEMA, handle)` at module level.
- [ ] **Step 2.4:** Run tests, verify all pass
- [ ] **Step 2.5:** Commit: `feat(mcp): add SSH tool -- exec, upload, download, test, list_hosts`

**Key implementation details for ssh.py:**

- `exec`: validate host + command required, call `ssh.run()`, format stdout/stderr/exit code
- `test`: call `ssh.run(host, "echo ok", timeout=5)`, return reachable/unreachable
- `list_hosts`: call `ssh.run("amarillo", "tailscale status --json")`, parse + format
- `upload`/`download`: validate path starts with `/workspace/`, use SCP via SSH
- All errors sanitized via `_sanitize_ssh_error()`

---

## Task 3: Notify Tool + Unit Tests

**Files:**
- Create: `docker/mcp/tools/notify.py`
- Create: `tests/tools/test_notify.py`

- [ ] **Step 3.1:** Create `tests/tools/test_notify.py` with tests for: send (3 tests), send_alert format (4 tests), send_photo (2 tests), send_document (1 test), rate limiting (1 test), unknown action (1 test)
- [ ] **Step 3.2:** Run tests, verify they fail
- [ ] **Step 3.3:** Create `docker/mcp/tools/notify.py` — SCHEMA dict + `handle()` + `_format_alert()`. Direct httpx to Telegram Bot API. Severity icons: info=info, warning=warning, critical=siren. Token/chat_id from env vars.
- [ ] **Step 3.4:** Run tests, verify all pass
- [ ] **Step 3.5:** Commit: `feat(mcp): add Telegram notify tool -- send, alert, photo, document`

**Key implementation details for notify.py:**

- No SSH dependency — direct httpx to `https://api.telegram.org/bot{token}/`
- `_format_alert()`: icon + severity label + UTC timestamp + text
- `send_photo`: accepts URL (Telegram fetches) or workspace file path (multipart upload)
- `send_document`: workspace path only, multipart upload
- Rate limit: return error on 429, no retry
- All messages use `parse_mode=Markdown`

---

## Task 4: Log Tool + Unit Tests

**Files:**
- Create: `docker/mcp/tools/log.py`
- Create: `tests/tools/test_log.py`

- [ ] **Step 4.1:** Create `tests/tools/test_log.py` with tests for: list (1 test), search (4 tests including path traversal), tail (3 tests), count (1 test), errors (1 test), between (2 tests), unknown action (1 test)
- [ ] **Step 4.2:** Run tests, verify they fail
- [ ] **Step 4.3:** Create `docker/mcp/tools/log.py` — SCHEMA dict + `handle()` + `_validate_file_param()` + `_shell_quote()`. All ops via SSH to amarillo targeting `/mnt/nvmeINT/logs/`.
- [ ] **Step 4.4:** Run tests, verify all pass
- [ ] **Step 4.5:** Commit: `feat(mcp): add log tool -- search, tail, count, errors, between`

**Key implementation details for log.py:**

- `_validate_file_param()`: reject `..`, absolute paths, allow only `[\w.*?\-]+`
- `_shell_quote()`: single-quote with escaped inner quotes
- `search`: `grep -rPn --max-count={max_results}` with shell-quoted pattern
- `tail`: `tail -n {lines}` clamped to 500
- `count`: `grep -rP | awk` time-bucketing by hour or day
- `errors`: `grep -rcP 'ERROR|FATAL|Exception|panic|Traceback' *.log | sort -rn`
- `between`: `awk` timestamp range filter

---

## Task 5: Monitor Tool + Unit Tests

**Files:**
- Create: `docker/mcp/tools/monitor.py`
- Create: `tests/tools/test_monitor.py`

- [ ] **Step 5.1:** Create `tests/tools/test_monitor.py` with tests for: overview (1 test), thermals normal + warning (2 tests), containers (1 test), disk normal + warning (2 tests), gpu (1 test), tailscale (1 test), services (1 test), unreachable host handling (1 test), unknown action (1 test)
- [ ] **Step 5.2:** Run tests, verify they fail
- [ ] **Step 5.3:** Create `docker/mcp/tools/monitor.py` — SCHEMA dict + `handle()` + parsers (`_parse_temps`, `_parse_mem`, `_parse_df`). Thermal warning at 85C. Disk warning at 85%.
- [ ] **Step 5.4:** Run tests, verify all pass
- [ ] **Step 5.5:** Commit: `feat(mcp): add monitor tool -- fleet dashboard, thermals, disk, containers, tailscale`

**Key implementation details for monitor.py:**

- `FLEET_HOSTS = ["amarillo", "dominus", "sentinel", "superemus"]`
- `_VITALS_CMD`: single command gathering sensors + free + df + nproc with delimiters
- `overview`: `run_multi` for vitals, then individual calls for containers + tailscale
- `_parse_temps`: parse `sensors -j` JSON, extract `*_input` values
- `_parse_mem`: parse `free -b`, extract Mem line
- `_parse_df`: parse df output, extract mount + percent
- Unreachable hosts: `[unreachable]` marker, not an error
- Service checks: curl to known health endpoints via SSH

---

## Task 6: Git Tool + Unit Tests

**Files:**
- Create: `docker/mcp/tools/git.py`
- Create: `tests/tools/test_git.py`

- [ ] **Step 6.1:** Create `tests/tools/test_git.py` with tests for: status (2 tests), log (2 tests), diff (1 test), ci (1 test), scorecard (1 test), create_pr (2 tests), merge (1 test), push (1 test), trigger_ci (1 test), issues list + create (2 tests), repo validation (2 tests), unknown action (1 test)
- [ ] **Step 6.2:** Run tests, verify they fail
- [ ] **Step 6.3:** Create `docker/mcp/tools/git.py` — SCHEMA dict + `handle()` + `_validate_repo()` + `_shell_quote()`. All ops via SSH to amarillo running gh/git CLI.
- [ ] **Step 6.4:** Run tests, verify all pass
- [ ] **Step 6.5:** Commit: `feat(mcp): add git tool -- full gh proxy with status, CI, PRs, scorecard`

**Key implementation details for git.py:**

- `GIT_BASE = "$HOME/git"` — expanded by remote shell
- `_validate_repo()`: reject `..`, `/`, allow only `[\w\-]+`
- `status` (no repo): iterate `~/git/*/` with porcelain + branch + upstream count
- `scorecard`: iterate all repos, `gh run list --limit 1 --json conclusion`
- `create_pr`: `gh pr create --title --base --head --body`
- `merge`: `gh pr merge {number} --merge`
- Diff output capped at 8000 chars

---

## Task 7: IoT Tool + Unit Tests

**Files:**
- Create: `docker/mcp/tools/iot.py`
- Create: `tests/tools/test_iot.py`

- [ ] **Step 7.1:** Create `tests/tools/test_iot.py` with tests for: list_devices (1 test), roku keypress (2 tests), roku launch (1 test), roku query (1 test), roku apps list (1 test), shield keypress (1 test), shield command (1 test), power off roku (1 test), unknown device (1 test), missing device (1 test), unknown action (1 test)
- [ ] **Step 7.2:** Run tests, verify they fail
- [ ] **Step 7.3:** Create `docker/mcp/tools/iot.py` — SCHEMA dict + `handle()` + `DEVICES` registry + `_parse_roku_xml_apps()` + `_send_wol()`. Roku via httpx, Shield via SSH.
- [ ] **Step 7.4:** Run tests, verify all pass
- [ ] **Step 7.5:** Commit: `feat(mcp): add IoT tool -- Roku ECP + Shield SSH with extensible registry`

**Key implementation details for iot.py:**

- `DEVICES` dict: `roku` (HTTP, 192.168.50.13:8060), `shield` (SSH, 192.168.50.99:8022)
- Roku ECP: `POST /keypress/{key}`, `POST /launch/{app_id}`, `GET /query/*`
- App name resolution: `GET /query/apps` XML parse, fuzzy match
- Shield: `input keyevent KEYCODE_{key}`, `am start`, `dumpsys`
- WOL: standard magic packet via UDP broadcast (requires MAC in registry)
- `_parse_roku_xml_apps()`: regex on `<app id="(\d+)"[^>]*>([^<]+)</app>`

---

## Task 8: App.py Integration + Docker Compose

**Files:**
- Modify: `docker/mcp/app.py` (3 lines)
- Modify: `docker-compose.yml` (2 env vars)

- [ ] **Step 8.1:** Add import block to `app.py` (after existing imports, ~line 30): import `TOOL_SCHEMAS`, `TOOL_HANDLERS` from tools, import all 6 tool modules
- [ ] **Step 8.2:** After `_TOOLS` list closing bracket (line 1930): `_TOOLS.extend(TOOL_SCHEMAS)`
- [ ] **Step 8.3:** In `_call_tool` after `_resolve_mega_tool` (line 3781): `if name in TOOL_HANDLERS: return await TOOL_HANDLERS[name](args)`
- [ ] **Step 8.4:** Add `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` to aichat-mcp environment in `docker-compose.yml`
- [ ] **Step 8.5:** Verify `.env` has `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` values (already written earlier in session)
- [ ] **Step 8.6:** Verify: `python -c "from tools import TOOL_SCHEMAS; assert len(TOOL_SCHEMAS) == 6"`
- [ ] **Step 8.7:** Commit: `feat(mcp): wire 6 new tools into app.py + add Telegram env vars`

---

## Task 9: Architecture Contract Test Updates

**Files:**
- Modify: `tests/test_architecture.py`

- [ ] **Step 9.1:** Update `_STANDALONE_TOOLS` (line 64) to include all 6 new tools
- [ ] **Step 9.2:** Add schema contract tests: each new tool has `action` property, expected params, non-empty description with `Actions:` section, no duplicate names
- [ ] **Step 9.3:** Run architecture tests, verify pass
- [ ] **Step 9.4:** Commit: `test(mcp): extend architecture contracts for 6 new tools`

---

## Task 10: Integration Tests

**Files:**
- Create: `tests/tools/test_integration.py`

- [ ] **Step 10.1:** Create integration tests: SSH exec echo, monitor thermals + containers + tailscale, git status + log + scorecard, notify send, log list, iot roku query. All skip when services unreachable.
- [ ] **Step 10.2:** Run integration tests (requires live stack)
- [ ] **Step 10.3:** Commit: `test(mcp): add integration tests for all 6 new tools`

---

## Task 11: E2E Tests

**Files:**
- Create: `tests/tools/test_e2e.py`

- [ ] **Step 11.1:** Create e2e tests: JSON-RPC `tools/call` via httpx to `http://localhost:8096/mcp`. Test each tool's primary action + tool discovery (`tools/list` includes all 6). Skip when MCP unreachable.
- [ ] **Step 11.2:** Run e2e tests (requires running aichat-mcp)
- [ ] **Step 11.3:** Commit: `test(mcp): add e2e tests for all 6 new tools via MCP endpoint`

---

## Task 12: Full Test Run + Code Review + Final

- [ ] **Step 12.1:** Run all unit tests (expect ~150 pass)
- [ ] **Step 12.2:** Run architecture contracts (expect pass)
- [ ] **Step 12.3:** Rebuild + restart aichat-mcp container
- [ ] **Step 12.4:** Verify health endpoint shows 25 tools (19 + 6)
- [ ] **Step 12.5:** Run integration tests
- [ ] **Step 12.6:** Run e2e tests
- [ ] **Step 12.7:** Code review checklist: schema validity, security (path traversal, allowlist, sanitization), response format, no new deps, docker changes minimal
- [ ] **Step 12.8:** Run orchestrator-enterprise scan
- [ ] **Step 12.9:** Fix any issues, commit cleanup if needed
