# Telegram Bot Refactor — Design Spec

**Date:** 2026-04-10
**Status:** Approved
**Scope:** Full refactor of `docker/mcp/tools/telegram_bot.py` (773-line monolith) into a modular package with persistent state, hybrid classification, resilience, and comprehensive testing.

---

## 1. Goals

1. Break the monolith into focused, independently testable modules
2. Replace pure-LLM classification with a hybrid regex + LLM approach
3. Add Postgres-backed conversation history and task audit trail
4. Add rate limit handling and graceful degradation
5. Comprehensive test suite: unit (~60), integration (~10), contract (~8)

## 2. Non-Goals

- Multi-user support (remains single-user, single TELEGRAM_CHAT_ID)
- Replacing SSH → Claude Code execution path (kept as-is, just cleaner code)
- Switching to Anthropic API / Managed Agents
- Webhook mode (stays long-polling)

---

## 3. Module Structure

```
docker/mcp/tools/telegram/
├── __init__.py          # Public API: poll_loop
├── api.py               # Telegram API client (send, getUpdates, rate limit handling)
├── auth.py              # Authorization gate
├── classifier.py        # Hybrid classifier (regex patterns + LLM fallback)
├── config.py            # All env vars, constants, validation
├── db.py                # Postgres layer (conversations, tasks)
├── dispatcher.py        # Intent → handler routing
├── handlers/
│   ├── __init__.py
│   ├── tool.py          # Tool dispatch (MCP tools)
│   ├── code.py          # Code modification (SSH → Claude)
│   ├── create.py        # Project scaffolding (SSH → Claude)
│   ├── question.py      # Q&A via Gemma (with conversation context)
│   ├── status.py        # Task status
│   └── cancel.py        # Task cancellation
├── models.py            # Dataclasses (Intent, TaskState, ConversationMessage)
├── poller.py            # Poll loop, message routing
├── stream.py            # Claude stream-json parser, milestones, heartbeat
└── summary.py           # Summary builder
```

### Module Responsibilities

- **config.py**: Single source of truth for all env vars (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `IMAGE_GEN_BASE_URL`, `TEAM_SSH_*`, DB connection). Validates on import, exposes typed constants. Logs resolved values (token redacted).
- **api.py**: `send_message(text, reply_to)` and `get_updates(offset)`. Handles Markdown 400 retry, 429 rate limit (sleep + retry once), 4096 char truncation. Uses httpx.AsyncClient.
- **auth.py**: `is_authorized(message) -> bool`. Checks chat_id against config.
- **classifier.py**: `classify(text) -> Intent`. Regex table checked first (compiled at import), LLM fallback for non-matches. Exported for direct testing.
- **db.py**: asyncpg connection pool. Schema migration on init. `save_message()`, `get_history(chat_id, limit=10)`, `save_task()`, `update_task()`, `recover_stale_tasks()`. Graceful degradation — all writes wrapped in try/except, bot works without DB.
- **dispatcher.py**: `dispatch(intent, message, reply_to)`. Routes to handlers. Thin — just a routing table.
- **handlers/**: Each handler is a single async function. `tool.py` calls TOOL_HANDLERS. `code.py` and `create.py` spawn background tasks via stream.py. `question.py` pulls conversation history from db.py and calls Gemma. `status.py` and `cancel.py` operate on in-memory task dict + DB.
- **models.py**: `Intent`, `TaskState`, `ConversationMessage` dataclasses. Pure data, no logic.
- **poller.py**: `poll_loop()` — the main entry point. Long-poll loop, auth gate, spawns message handling, offset tracking, shutdown handling.
- **stream.py**: `stream_claude(ssh_command, reply_to, task_state) -> str`. Subprocess management, stream-json parsing, milestone detection, heartbeat task, timeout handling.
- **summary.py**: `build_summary(task_state, final_text, edited_files, last_output, returncode) -> str`. Pure function.

### Integration Point

`app.py` changes from:
```python
from tools.telegram_bot import poll_loop as _telegram_poll_loop
```
to:
```python
from tools.telegram import poll_loop as _telegram_poll_loop
```

Everything else in app.py stays the same (lifespan create_task / cancel pattern).

---

## 4. Hybrid Classifier

### Fast Path — Regex Patterns

Checked first, top-to-bottom, first match wins. Case-insensitive. Compiled at import time.

| Pattern | Intent | Examples |
|---|---|---|
| `^/?(status)$` | direct → status handler | "status", "/status" |
| `^/?(cancel)$` | direct → cancel handler | "cancel", "/cancel" |
| `(check\|show\|get)\s+(thermals?\|containers?\|disk\|gpu\|services?\|tailscale)` | tool:monitor:{match} | "check thermals", "show containers" |
| `(monitor\|overview\|fleet\|how.*fleet)` | tool:monitor:overview | "how's the fleet", "overview" |
| `(tail\|read\|show)\s+logs?\s*(for\|of\|from)?\s*(?P<svc>\S+)?` | tool:log:tail | "tail logs for mcp" |
| `git\s+(status\|log\|diff\|ci\|issues?)\s*(in\|for\|of)?\s*(?P<repo>\S+)?` | tool:git:{action} | "git status in aichat" |
| `(list\|show)\s+(devices?\|sensors?\|switches?)` | tool:iot:list_devices | "list devices" |
| `(ssh\|run)\s+(on\s+)?(?P<host>\S+)\s+(?P<cmd>.+)` | tool:ssh:exec | "ssh run uptime on amarillo" |
| `(send\|notify\|alert)\s+(?P<msg>.+)` | tool:notify:send | "send alert disk full" |

### Slow Path — LLM Fallback

Non-matching messages go to Gemma 4 E2B. Same system prompt (4 intent types: tool/code/create/question), same JSON output, same 15s timeout. On failure, falls back to `question` intent.

---

## 5. Postgres Schema

Direct asyncpg connection to `aichat-db:5432`. Schema managed by `db.py` via `CREATE TABLE IF NOT EXISTS` on first connect.

```sql
CREATE TABLE IF NOT EXISTS telegram_messages (
    id          SERIAL PRIMARY KEY,
    chat_id     BIGINT NOT NULL,
    role        TEXT NOT NULL,              -- 'user' or 'assistant'
    content     TEXT NOT NULL,
    message_id  INT,                        -- Telegram message_id
    created_at  TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_tg_messages_chat
    ON telegram_messages(chat_id, created_at DESC);

CREATE TABLE IF NOT EXISTS telegram_tasks (
    task_id     TEXT PRIMARY KEY,           -- 8-char UUID prefix
    chat_id     BIGINT NOT NULL,
    intent_type TEXT NOT NULL,              -- 'code' or 'create'
    repo        TEXT,
    description TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'running',
    files       TEXT[],
    commit_sha  TEXT,
    exit_code   INT,
    summary     TEXT,
    started_at  TIMESTAMPTZ DEFAULT now(),
    finished_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_tg_tasks_chat
    ON telegram_tasks(chat_id, started_at DESC);
```

### Behavior

- **Conversation**: every user message and bot response stored. Q&A handler pulls last 10 messages as context window.
- **Tasks**: inserted on create, updated on completion/failure/cancel. Fields like `files`, `commit_sha`, `exit_code` populated from stream results.
- **Startup recovery**: on poller startup, `UPDATE telegram_tasks SET status='failed', summary='Container restarted', finished_at=now() WHERE status='running'`.
- **Graceful degradation**: all DB writes in try/except. On failure, log warning and continue. Bot works fully without Postgres — just no persistence.

### Connection

```python
# db.py
_pool: asyncpg.Pool | None = None

async def init(dsn: str | None = None):
    global _pool
    dsn = dsn or os.environ.get(
        "TELEGRAM_DB_DSN",
        f"postgresql://aichat:{os.environ['POSTGRES_PASSWORD']}@aichat-db:5432/aichat"
    )
    _pool = await asyncpg.create_pool(dsn, min_size=1, max_size=3)
    await _migrate()

async def close():
    if _pool:
        await _pool.close()
```

DSN uses the same `POSTGRES_PASSWORD` env var as the rest of the stack. Overridable via `TELEGRAM_DB_DSN` env var. The `POSTGRES_PASSWORD` env var must be added to the aichat-mcp service in docker-compose.yml.

---

## 6. Error Handling & Resilience

### Rate Limiting
- Telegram 429 → read `retry_after`, sleep, retry once. Second 429 → log and drop.

### Retry Policy

| Operation | Retries | Backoff | On Failure |
|---|---|---|---|
| sendMessage | 1 (+ retry without parse_mode on 400) | retry_after on 429 | Log, drop |
| getUpdates | 0 (loop retries naturally) | 5s sleep | Log, continue |
| LM Studio classify | 0 | — | Fall back to `question` |
| LM Studio Q&A | 0 | — | Send error to user |
| SSH/Claude | 0 | — | Mark task `failed`, notify |
| Postgres write | 2 | 1s | Log, continue without DB |

### Graceful Degradation
- **Postgres down**: bot works, no history/persistence, logs warning per failed write (not per message — debounce).
- **LM Studio down**: regex patterns still work, LLM fallback returns `question`, Q&A sends "LM Studio unreachable".
- **SSH unreachable**: code/create fail with clear error, tool/question unaffected.

### Startup Sequence
1. Load and validate config (abort if token/chat_id missing)
2. Init DB pool, run migrations, recover stale tasks
3. Log config summary (token redacted)
4. Enter poll loop

---

## 7. Migration Plan

### File Changes
- **Delete**: `docker/mcp/tools/telegram_bot.py` (replaced by package)
- **Delete**: `tests/tools/test_telegram_bot.py` (replaced by test package)
- **Create**: `docker/mcp/tools/telegram/` package (14 files)
- **Create**: `tests/tools/telegram/` test package (~18 files)
- **Modify**: `docker/mcp/app.py` — update import path
- **Modify**: `docker/mcp/requirements.txt` — add `asyncpg`

### Import Compatibility
The only external consumer is `app.py`:
```python
from tools.telegram import poll_loop as _telegram_poll_loop
```
Same function signature, same behavior. No other files import from `telegram_bot.py`.

### Backward Compatibility
- All env vars preserved (same names, same defaults)
- Same Telegram bot token and chat ID
- Same MCP tool integration (TOOL_HANDLERS)
- New env var: `TELEGRAM_DB_DSN` (optional, has default)
- New dependency: `asyncpg`

---

## 8. Test Strategy

### Unit Tests (~60 tests)

| Module | Count | Coverage |
|---|---|---|
| config.py | 5 | Env loading, defaults, validation, missing vars, redaction |
| api.py | 8 | Send success, truncation, 400 retry, 429 rate limit + retry, getUpdates success/error, timeout |
| auth.py | 3 | Authorized, unauthorized, missing chat_id |
| classifier.py | 15 | Each regex pattern (9), named groups, case insensitivity, LLM fallback, malformed JSON, network error, ambiguous routing |
| models.py | 4 | Intent/TaskState/ConversationMessage construction, defaults |
| db.py | 8 | Insert message, fetch history, insert task, update task, startup recovery, connection failure, pool init, migration |
| dispatcher.py | 5 | Each intent type routed correctly, unknown tool, handler exception |
| handlers/ | 8 | Each handler happy + error path |
| stream.py | 6 | Milestone detection, heartbeat, timeout, commit SHA, edited files dedup, return code |
| summary.py | 3 | Full summary, partial data, zero files |
| poller.py | 4 | Auth gate, offset tracking, dispatch, shutdown |

### Integration Tests (~10 tests)

Test infrastructure:
- Fake Telegram HTTP server (aiohttp test_server or respx mock router)
- Real Postgres (aichat-db in compose, or testcontainers)
- Mock LM Studio (respx)

| Test | What It Proves |
|---|---|
| "check thermals" → regex → monitor dispatched → response sent | Full fast-path |
| Ambiguous message → LLM → correct handler | Full slow-path |
| "status" with active task → listing | Task tracking e2e |
| Code task → DB persist → status → completion → DB update | Task lifecycle |
| Q&A with prior messages → context in prompt | History from Postgres |
| Container restart → stale tasks marked failed | Startup recovery |
| 429 from Telegram → retry → delivered | Rate limit |
| Postgres down → bot works → logs warnings | Degradation |
| Rapid messages → all processed | Concurrency |
| LM Studio down → regex works, LLM falls back | Partial failure |

### Contract Tests (~8 tests)

Recorded fixtures in `tests/tools/telegram/fixtures/`:

| Fixture File | Tests | What It Catches |
|---|---|---|
| `telegram_responses.json` | 3 | sendMessage shape, getUpdates shape, 429 error shape |
| `lmstudio_responses.json` | 2 | Chat completion shape, error shape |
| `claude_stream.jsonl` | 3 | assistant event, tool_use event, result event |

Tests parse fixtures with the same code the bot uses. If the parser breaks against the fixture, upstream changed something.

---

## 9. Dependencies

### New
- `asyncpg` — async Postgres driver

### Existing (unchanged)
- `httpx` — HTTP client
- Standard library: `asyncio`, `dataclasses`, `json`, `logging`, `os`, `re`, `time`, `uuid`
