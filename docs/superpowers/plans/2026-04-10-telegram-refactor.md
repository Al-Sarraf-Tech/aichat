# Telegram Bot Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the 773-line `telegram_bot.py` monolith with a modular package featuring hybrid regex+LLM classification, Postgres persistence, and comprehensive tests.

**Architecture:** Python package at `docker/mcp/tools/telegram/` with 14 focused modules. Hybrid classifier uses compiled regex patterns for common commands (instant) and falls back to Gemma 4 E2B for ambiguous messages. asyncpg connects directly to the existing `aichat-db` Postgres container for conversation history and task audit trail. All DB operations degrade gracefully — bot works without Postgres.

**Tech Stack:** Python 3.12, asyncpg, httpx, asyncio, pytest, respx (HTTP mocking), aiohttp (fake test server)

---

## File Structure

### New Files (create)

```
docker/mcp/tools/telegram/__init__.py      — Public API: poll_loop, init_db, close_db
docker/mcp/tools/telegram/config.py        — Env vars, constants, validation
docker/mcp/tools/telegram/models.py        — Intent, TaskState, ConversationMessage dataclasses
docker/mcp/tools/telegram/api.py           — Telegram API client (send, getUpdates, rate limits)
docker/mcp/tools/telegram/auth.py          — Authorization gate
docker/mcp/tools/telegram/db.py            — asyncpg pool, migrations, CRUD
docker/mcp/tools/telegram/classifier.py    — Hybrid regex + LLM classifier
docker/mcp/tools/telegram/dispatcher.py    — Intent → handler routing
docker/mcp/tools/telegram/handlers/__init__.py
docker/mcp/tools/telegram/handlers/tool.py     — MCP tool dispatch
docker/mcp/tools/telegram/handlers/code.py     — Code modification via SSH→Claude
docker/mcp/tools/telegram/handlers/create.py   — Project scaffolding via SSH→Claude
docker/mcp/tools/telegram/handlers/question.py — Q&A via Gemma with conversation context
docker/mcp/tools/telegram/handlers/status.py   — Task status listing
docker/mcp/tools/telegram/handlers/cancel.py   — Task cancellation
docker/mcp/tools/telegram/stream.py        — Claude stream-json parser, milestones, heartbeat
docker/mcp/tools/telegram/summary.py       — Summary builder
docker/mcp/tools/telegram/poller.py        — Poll loop, message routing

tests/tools/telegram/__init__.py
tests/tools/telegram/conftest.py           — Shared fixtures, fake servers, DB helpers
tests/tools/telegram/fixtures/telegram_responses.json
tests/tools/telegram/fixtures/lmstudio_responses.json
tests/tools/telegram/fixtures/claude_stream.jsonl
tests/tools/telegram/unit/__init__.py
tests/tools/telegram/unit/test_config.py
tests/tools/telegram/unit/test_models.py
tests/tools/telegram/unit/test_api.py
tests/tools/telegram/unit/test_auth.py
tests/tools/telegram/unit/test_db.py
tests/tools/telegram/unit/test_classifier.py
tests/tools/telegram/unit/test_dispatcher.py
tests/tools/telegram/unit/test_handlers.py
tests/tools/telegram/unit/test_stream.py
tests/tools/telegram/unit/test_summary.py
tests/tools/telegram/unit/test_poller.py
tests/tools/telegram/integration/__init__.py
tests/tools/telegram/integration/test_full_pipeline.py
tests/tools/telegram/integration/test_db_persistence.py
tests/tools/telegram/integration/test_task_lifecycle.py
tests/tools/telegram/contract/__init__.py
tests/tools/telegram/contract/test_telegram_contract.py
tests/tools/telegram/contract/test_lmstudio_contract.py
tests/tools/telegram/contract/test_claude_stream_contract.py
```

### Modified Files

```
docker/mcp/requirements.txt               — Add asyncpg, respx (test)
docker/mcp/app.py:36                       — Update import path
docker-compose.yml:~196                    — Add POSTGRES_PASSWORD to aichat-mcp env
```

### Deleted Files

```
docker/mcp/tools/telegram_bot.py           — Replaced by telegram/ package
tests/tools/test_telegram_bot.py           — Replaced by tests/tools/telegram/
```

---

## Task 1: Foundation — config.py + models.py

**Files:**
- Create: `docker/mcp/tools/telegram/__init__.py`
- Create: `docker/mcp/tools/telegram/config.py`
- Create: `docker/mcp/tools/telegram/models.py`
- Create: `tests/tools/telegram/__init__.py`
- Create: `tests/tools/telegram/unit/__init__.py`
- Create: `tests/tools/telegram/unit/test_config.py`
- Create: `tests/tools/telegram/unit/test_models.py`

- [ ] **Step 1: Create package skeleton**

```python
# docker/mcp/tools/telegram/__init__.py
"""Telegram bot package — modular command handler with hybrid classification."""
```

```python
# tests/tools/telegram/__init__.py
```

```python
# tests/tools/telegram/unit/__init__.py
```

- [ ] **Step 2: Write config tests**

```python
# tests/tools/telegram/unit/test_config.py
"""Tests for telegram.config — env loading, defaults, validation."""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest


class TestConfig:
    """Config loading and validation."""

    def test_loads_all_env_vars(self):
        env = {
            "TELEGRAM_BOT_TOKEN": "tok-123",
            "TELEGRAM_CHAT_ID": "999",
            "IMAGE_GEN_BASE_URL": "http://lm:1234",
            "TEAM_SSH_HOST": "myhost",
            "TEAM_SSH_PORT": "2222",
            "TEAM_SSH_USER": "admin",
            "TEAM_SSH_KEY": "/keys/id",
            "POSTGRES_PASSWORD": "secret",
        }
        with patch.dict(os.environ, env, clear=False):
            from tools.telegram import config as cfg
            import importlib
            importlib.reload(cfg)
            assert cfg.BOT_TOKEN == "tok-123"
            assert cfg.CHAT_ID == "999"
            assert cfg.LM_STUDIO_URL == "http://lm:1234"
            assert cfg.SSH_HOST == "myhost"
            assert cfg.SSH_PORT == 2222
            assert cfg.SSH_USER == "admin"
            assert cfg.SSH_KEY == "/keys/id"
            assert "secret" in cfg.DB_DSN

    def test_defaults_without_optional_vars(self):
        env = {
            "TELEGRAM_BOT_TOKEN": "tok",
            "TELEGRAM_CHAT_ID": "1",
            "POSTGRES_PASSWORD": "pw",
        }
        with patch.dict(os.environ, env, clear=False):
            from tools.telegram import config as cfg
            import importlib
            importlib.reload(cfg)
            assert cfg.SSH_HOST == "host.docker.internal"
            assert cfg.SSH_PORT == 1337
            assert cfg.SSH_USER == "jalsarraf"
            assert cfg.LM_STUDIO_URL == "http://192.168.50.2:1234"

    def test_is_configured_true(self):
        env = {"TELEGRAM_BOT_TOKEN": "tok", "TELEGRAM_CHAT_ID": "1", "POSTGRES_PASSWORD": "pw"}
        with patch.dict(os.environ, env, clear=False):
            from tools.telegram import config as cfg
            import importlib
            importlib.reload(cfg)
            assert cfg.is_configured() is True

    def test_is_configured_false_missing_token(self):
        env = {"TELEGRAM_BOT_TOKEN": "", "TELEGRAM_CHAT_ID": "1", "POSTGRES_PASSWORD": "pw"}
        with patch.dict(os.environ, env, clear=False):
            from tools.telegram import config as cfg
            import importlib
            importlib.reload(cfg)
            assert cfg.is_configured() is False

    def test_redacted_summary(self):
        env = {"TELEGRAM_BOT_TOKEN": "secret-token-value", "TELEGRAM_CHAT_ID": "1", "POSTGRES_PASSWORD": "pw"}
        with patch.dict(os.environ, env, clear=False):
            from tools.telegram import config as cfg
            import importlib
            importlib.reload(cfg)
            summary = cfg.summary()
            assert "secret-token-value" not in summary
            assert "***" in summary
            assert "1" in summary
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd ~/git/aichat && python -m pytest tests/tools/telegram/unit/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tools.telegram.config'`

- [ ] **Step 4: Implement config.py**

```python
# docker/mcp/tools/telegram/config.py
"""Centralized configuration — single source of truth for all env vars."""
from __future__ import annotations

import os

# Telegram
BOT_TOKEN: str = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID: str = os.environ.get("TELEGRAM_CHAT_ID", "")

# LM Studio
LM_STUDIO_URL: str = os.environ.get("IMAGE_GEN_BASE_URL", "http://192.168.50.2:1234")
LM_STUDIO_MODEL: str = os.environ.get("TELEGRAM_LM_MODEL", "gemma-4-e2b-it")

# SSH (for Claude Code dispatch)
SSH_HOST: str = os.environ.get("TEAM_SSH_HOST", "host.docker.internal")
SSH_PORT: int = int(os.environ.get("TEAM_SSH_PORT", "1337"))
SSH_USER: str = os.environ.get("TEAM_SSH_USER", "jalsarraf")
SSH_KEY: str = os.environ.get("TEAM_SSH_KEY", "/app/.ssh/team_key")

# Postgres
_pg_password: str = os.environ.get("POSTGRES_PASSWORD", "")
DB_DSN: str = os.environ.get(
    "TELEGRAM_DB_DSN",
    f"postgresql://aichat:{_pg_password}@aichat-db:5432/aichat",
)

# Limits
MAX_MSG_LEN: int = 4096
CLAUDE_TIMEOUT: int = 600  # 10 minutes
HEARTBEAT_INTERVAL: int = 90  # seconds
LM_CLASSIFY_TIMEOUT: int = 15
LM_QA_TIMEOUT: int = 20


def is_configured() -> bool:
    """Return True if required env vars are set."""
    return bool(BOT_TOKEN) and bool(CHAT_ID)


def summary() -> str:
    """Return a log-safe config summary with token redacted."""
    redacted = "***" if BOT_TOKEN else "(empty)"
    return (
        f"bot_token={redacted} chat_id={CHAT_ID} "
        f"lm_studio={LM_STUDIO_URL} model={LM_STUDIO_MODEL} "
        f"ssh={SSH_USER}@{SSH_HOST}:{SSH_PORT}"
    )
```

- [ ] **Step 5: Run config tests**

Run: `cd ~/git/aichat && python -m pytest tests/tools/telegram/unit/test_config.py -v`
Expected: PASS (5 tests)

- [ ] **Step 6: Write models tests**

```python
# tests/tools/telegram/unit/test_models.py
"""Tests for telegram.models — dataclass construction and defaults."""
from __future__ import annotations

from tools.telegram.models import ConversationMessage, Intent, TaskState


class TestIntent:
    def test_defaults(self):
        i = Intent(type="question")
        assert i.type == "question"
        assert i.tool == ""
        assert i.repo is None
        assert i.args == {}

    def test_tool_intent(self):
        i = Intent(type="tool", tool="monitor", action="thermals", args={"host": "amarillo"})
        assert i.tool == "monitor"
        assert i.action == "thermals"
        assert i.args == {"host": "amarillo"}


class TestTaskState:
    def test_defaults(self):
        ts = TaskState(task_id="abc12345", description="fix bug")
        assert ts.status == "running"
        assert ts.repo is None
        assert ts.asyncio_task is None
        assert ts.process is None
        assert ts.started_at > 0


class TestConversationMessage:
    def test_construction(self):
        msg = ConversationMessage(chat_id=123, role="user", content="hello")
        assert msg.chat_id == 123
        assert msg.message_id is None
```

- [ ] **Step 7: Run models tests to verify they fail**

Run: `cd ~/git/aichat && python -m pytest tests/tools/telegram/unit/test_models.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 8: Implement models.py**

```python
# docker/mcp/tools/telegram/models.py
"""Data models — pure dataclasses, no logic."""
from __future__ import annotations

import dataclasses
import time
from typing import Any


@dataclasses.dataclass
class Intent:
    """Classified user intent."""
    type: str                    # "tool" | "code" | "create" | "question"
    tool: str = ""
    action: str = ""
    args: dict[str, Any] = dataclasses.field(default_factory=dict)
    repo: str | None = None
    task: str = ""
    name: str = ""
    description: str = ""
    language: str = ""
    text: str = ""


@dataclasses.dataclass
class TaskState:
    """Tracks a running code/create task."""
    task_id: str
    description: str
    repo: str | None = None
    status: str = "running"      # running | done | failed | cancelled
    started_at: float = dataclasses.field(default_factory=time.monotonic)
    asyncio_task: Any = None     # asyncio.Task (not typed to avoid import)
    process: Any = None          # asyncio.subprocess.Process


@dataclasses.dataclass
class ConversationMessage:
    """A single message in conversation history."""
    chat_id: int
    role: str                    # "user" | "assistant"
    content: str
    message_id: int | None = None
```

- [ ] **Step 9: Run models tests**

Run: `cd ~/git/aichat && python -m pytest tests/tools/telegram/unit/test_models.py -v`
Expected: PASS (4 tests)

- [ ] **Step 10: Commit**

```bash
cd ~/git/aichat
git add docker/mcp/tools/telegram/__init__.py docker/mcp/tools/telegram/config.py docker/mcp/tools/telegram/models.py tests/tools/telegram/
git commit -m "feat(telegram): add config and models modules"
```

---

## Task 2: api.py — Telegram API Client

**Files:**
- Create: `docker/mcp/tools/telegram/api.py`
- Create: `tests/tools/telegram/unit/test_api.py`

- [ ] **Step 1: Write API tests**

```python
# tests/tools/telegram/unit/test_api.py
"""Tests for telegram.api — send_message, get_updates, rate limiting."""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

ENV = {
    "TELEGRAM_BOT_TOKEN": "test-token",
    "TELEGRAM_CHAT_ID": "123",
    "POSTGRES_PASSWORD": "pw",
}


def _mock_response(status: int = 200, json_data: dict | None = None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = json_data or {"ok": True, "result": {"message_id": 7}}
    resp.headers = {}
    return resp


def _mock_429_response(retry_after: int = 5) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 429
    resp.json.return_value = {"ok": False, "parameters": {"retry_after": retry_after}}
    resp.headers = {"Retry-After": str(retry_after)}
    return resp


@pytest.mark.asyncio
class TestSendMessage:
    async def test_sends_with_markdown(self):
        client = AsyncMock()
        client.post = AsyncMock(return_value=_mock_response())
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)

        with patch.dict(os.environ, ENV, clear=False):
            from tools.telegram import config
            import importlib
            importlib.reload(config)
            from tools.telegram.api import send_message
            with patch("tools.telegram.api.httpx.AsyncClient", return_value=client):
                await send_message("hello world")

        call_args = client.post.call_args
        assert "sendMessage" in call_args[0][0]
        assert call_args[1]["json"]["parse_mode"] == "Markdown"

    async def test_truncates_long_messages(self):
        client = AsyncMock()
        client.post = AsyncMock(return_value=_mock_response())
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)

        with patch.dict(os.environ, ENV, clear=False):
            from tools.telegram import config
            import importlib
            importlib.reload(config)
            from tools.telegram.api import send_message
            with patch("tools.telegram.api.httpx.AsyncClient", return_value=client):
                await send_message("x" * 5000)

        sent_text = client.post.call_args[1]["json"]["text"]
        assert len(sent_text) <= 4096
        assert sent_text.endswith("(truncated)")

    async def test_retries_without_markdown_on_400(self):
        resp_400 = _mock_response(status=400)
        resp_200 = _mock_response(status=200)
        client = AsyncMock()
        client.post = AsyncMock(side_effect=[resp_400, resp_200])
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)

        with patch.dict(os.environ, ENV, clear=False):
            from tools.telegram import config
            import importlib
            importlib.reload(config)
            from tools.telegram.api import send_message
            with patch("tools.telegram.api.httpx.AsyncClient", return_value=client):
                await send_message("bad *markdown")

        assert client.post.call_count == 2
        second_call = client.post.call_args_list[1]
        assert "parse_mode" not in second_call[1]["json"]

    async def test_handles_429_rate_limit(self):
        resp_429 = _mock_429_response(retry_after=1)
        resp_200 = _mock_response()
        client = AsyncMock()
        client.post = AsyncMock(side_effect=[resp_429, resp_200])
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)

        with patch.dict(os.environ, ENV, clear=False):
            from tools.telegram import config
            import importlib
            importlib.reload(config)
            from tools.telegram.api import send_message
            with patch("tools.telegram.api.httpx.AsyncClient", return_value=client), \
                 patch("tools.telegram.api.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
                await send_message("hello")

        mock_sleep.assert_awaited_once_with(1)
        assert client.post.call_count == 2

    async def test_reply_to_threading(self):
        client = AsyncMock()
        client.post = AsyncMock(return_value=_mock_response())
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)

        with patch.dict(os.environ, ENV, clear=False):
            from tools.telegram import config
            import importlib
            importlib.reload(config)
            from tools.telegram.api import send_message
            with patch("tools.telegram.api.httpx.AsyncClient", return_value=client):
                await send_message("reply", reply_to=42)

        assert client.post.call_args[1]["json"]["reply_to_message_id"] == 42


@pytest.mark.asyncio
class TestGetUpdates:
    async def test_returns_updates(self):
        updates = [{"update_id": 1, "message": {"text": "hi"}}]
        client = AsyncMock()
        client.get = AsyncMock(return_value=_mock_response(json_data={"ok": True, "result": updates}))
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)

        with patch.dict(os.environ, ENV, clear=False):
            from tools.telegram import config
            import importlib
            importlib.reload(config)
            from tools.telegram.api import get_updates
            with patch("tools.telegram.api.httpx.AsyncClient", return_value=client):
                result = await get_updates(0)

        assert result == updates

    async def test_returns_empty_on_error(self):
        client = AsyncMock()
        client.get = AsyncMock(side_effect=Exception("timeout"))
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)

        with patch.dict(os.environ, ENV, clear=False):
            from tools.telegram import config
            import importlib
            importlib.reload(config)
            from tools.telegram.api import get_updates
            with patch("tools.telegram.api.httpx.AsyncClient", return_value=client):
                result = await get_updates(0)

        assert result == []

    async def test_passes_offset_and_timeout(self):
        client = AsyncMock()
        client.get = AsyncMock(return_value=_mock_response(json_data={"ok": True, "result": []}))
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)

        with patch.dict(os.environ, ENV, clear=False):
            from tools.telegram import config
            import importlib
            importlib.reload(config)
            from tools.telegram.api import get_updates
            with patch("tools.telegram.api.httpx.AsyncClient", return_value=client):
                await get_updates(42)

        params = client.get.call_args[1]["params"]
        assert params["offset"] == 42
        assert params["timeout"] == 30
```

- [ ] **Step 2: Run to verify fail**

Run: `cd ~/git/aichat && python -m pytest tests/tools/telegram/unit/test_api.py -v`
Expected: FAIL

- [ ] **Step 3: Implement api.py**

```python
# docker/mcp/tools/telegram/api.py
"""Telegram Bot API client — send messages, poll updates, handle rate limits."""
from __future__ import annotations

import asyncio
import logging

import httpx

from tools.telegram import config

logger = logging.getLogger(__name__)

_API_BASE = "https://api.telegram.org/bot{token}"


async def send_message(
    text: str,
    reply_to: int | None = None,
    chat_id: str | None = None,
) -> None:
    """Send a message to Telegram. Handles truncation, Markdown 400 retry, and 429 rate limit."""
    chat_id = chat_id or config.CHAT_ID
    if not config.BOT_TOKEN:
        return

    if len(text) > config.MAX_MSG_LEN:
        text = text[: config.MAX_MSG_LEN - 12] + "\n(truncated)"

    url = f"{_API_BASE.format(token=config.BOT_TOKEN)}/sendMessage"
    payload: dict = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    if reply_to is not None:
        payload["reply_to_message_id"] = reply_to

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(url, json=payload)

            # Markdown parse error — retry without parse_mode
            if resp.status_code == 400:
                payload.pop("parse_mode", None)
                resp = await client.post(url, json=payload)

            # Rate limit — sleep and retry once
            if resp.status_code == 429:
                retry_after = _extract_retry_after(resp)
                logger.warning("Telegram 429 — sleeping %ds", retry_after)
                await asyncio.sleep(retry_after)
                resp = await client.post(url, json=payload)
                if resp.status_code == 429:
                    logger.error("Telegram 429 after retry — dropping message")

    except Exception:
        logger.exception("send_message failed")


async def get_updates(offset: int) -> list[dict]:
    """Long-poll for new messages. Returns list of update dicts, or [] on error."""
    if not config.BOT_TOKEN:
        return []

    url = f"{_API_BASE.format(token=config.BOT_TOKEN)}/getUpdates"
    params = {"offset": offset, "timeout": 30, "allowed_updates": '["message"]'}

    try:
        async with httpx.AsyncClient(timeout=35) as client:
            resp = await client.get(url, params=params)
            data = resp.json()
            return data.get("result", [])
    except Exception:
        logger.exception("get_updates failed")
        return []


def _extract_retry_after(resp: httpx.Response) -> int:
    """Extract retry_after from 429 response (header or body)."""
    try:
        return int(resp.headers.get("Retry-After", 5))
    except (ValueError, TypeError):
        pass
    try:
        return resp.json().get("parameters", {}).get("retry_after", 5)
    except Exception:
        return 5
```

- [ ] **Step 4: Run tests**

Run: `cd ~/git/aichat && python -m pytest tests/tools/telegram/unit/test_api.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
cd ~/git/aichat
git add docker/mcp/tools/telegram/api.py tests/tools/telegram/unit/test_api.py
git commit -m "feat(telegram): add Telegram API client with rate limit handling"
```

---

## Task 3: auth.py + db.py

**Files:**
- Create: `docker/mcp/tools/telegram/auth.py`
- Create: `docker/mcp/tools/telegram/db.py`
- Create: `tests/tools/telegram/unit/test_auth.py`
- Create: `tests/tools/telegram/unit/test_db.py`

- [ ] **Step 1: Write auth tests**

```python
# tests/tools/telegram/unit/test_auth.py
"""Tests for telegram.auth — authorization gate."""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest


ENV = {"TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_CHAT_ID": "12345", "POSTGRES_PASSWORD": "pw"}


class TestAuth:
    def test_authorized_correct_id(self):
        with patch.dict(os.environ, ENV, clear=False):
            from tools.telegram import config
            import importlib
            importlib.reload(config)
            from tools.telegram.auth import is_authorized
            msg = {"chat": {"id": 12345}}
            assert is_authorized(msg) is True

    def test_unauthorized_wrong_id(self):
        with patch.dict(os.environ, ENV, clear=False):
            from tools.telegram import config
            import importlib
            importlib.reload(config)
            from tools.telegram.auth import is_authorized
            msg = {"chat": {"id": 99999}}
            assert is_authorized(msg) is False

    def test_unauthorized_missing_chat_id_config(self):
        env = {**ENV, "TELEGRAM_CHAT_ID": ""}
        with patch.dict(os.environ, env, clear=False):
            from tools.telegram import config
            import importlib
            importlib.reload(config)
            from tools.telegram.auth import is_authorized
            msg = {"chat": {"id": 12345}}
            assert is_authorized(msg) is False
```

- [ ] **Step 2: Implement auth.py**

```python
# docker/mcp/tools/telegram/auth.py
"""Authorization gate — checks chat ID against config."""
from __future__ import annotations

from tools.telegram import config


def is_authorized(message: dict) -> bool:
    """Return True if message is from the authorized chat."""
    if not config.CHAT_ID:
        return False
    return str(message.get("chat", {}).get("id", "")) == config.CHAT_ID
```

- [ ] **Step 3: Run auth tests**

Run: `cd ~/git/aichat && python -m pytest tests/tools/telegram/unit/test_auth.py -v`
Expected: PASS (3 tests)

- [ ] **Step 4: Write db tests**

```python
# tests/tools/telegram/unit/test_db.py
"""Tests for telegram.db — Postgres operations (mocked asyncpg)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
class TestDb:
    async def test_save_message(self):
        mock_pool = AsyncMock()
        mock_pool.execute = AsyncMock()

        with patch("tools.telegram.db._pool", mock_pool):
            from tools.telegram.db import save_message
            await save_message(chat_id=123, role="user", content="hello", message_id=7)

        mock_pool.execute.assert_awaited_once()
        args = mock_pool.execute.call_args[0]
        assert "INSERT INTO telegram_messages" in args[0]
        assert args[1] == 123
        assert args[2] == "user"
        assert args[3] == "hello"
        assert args[4] == 7

    async def test_get_history(self):
        rows = [
            {"role": "user", "content": "hi", "created_at": "2026-04-10T00:00:00"},
            {"role": "assistant", "content": "hello", "created_at": "2026-04-10T00:00:01"},
        ]
        mock_pool = AsyncMock()
        mock_pool.fetch = AsyncMock(return_value=rows)

        with patch("tools.telegram.db._pool", mock_pool):
            from tools.telegram.db import get_history
            result = await get_history(chat_id=123, limit=10)

        assert len(result) == 2
        assert result[0]["role"] == "user"
        mock_pool.fetch.assert_awaited_once()

    async def test_save_task(self):
        mock_pool = AsyncMock()
        mock_pool.execute = AsyncMock()

        with patch("tools.telegram.db._pool", mock_pool):
            from tools.telegram.db import save_task
            await save_task(
                task_id="abc12345", chat_id=123, intent_type="code",
                repo="aichat", description="fix bug",
            )

        mock_pool.execute.assert_awaited_once()
        args = mock_pool.execute.call_args[0]
        assert "INSERT INTO telegram_tasks" in args[0]

    async def test_update_task(self):
        mock_pool = AsyncMock()
        mock_pool.execute = AsyncMock()

        with patch("tools.telegram.db._pool", mock_pool):
            from tools.telegram.db import update_task
            await update_task(
                task_id="abc12345", status="done",
                files=["main.py"], commit_sha="abc1234", exit_code=0,
                summary="Done — aichat",
            )

        mock_pool.execute.assert_awaited_once()
        args = mock_pool.execute.call_args[0]
        assert "UPDATE telegram_tasks" in args[0]
        assert args[1] == "done"

    async def test_recover_stale_tasks(self):
        mock_pool = AsyncMock()
        mock_pool.execute = AsyncMock()

        with patch("tools.telegram.db._pool", mock_pool):
            from tools.telegram.db import recover_stale_tasks
            await recover_stale_tasks()

        args = mock_pool.execute.call_args[0]
        assert "status = 'running'" in args[0] or "status='running'" in args[0].replace(" ", "")

    async def test_graceful_degradation_on_failure(self):
        mock_pool = AsyncMock()
        mock_pool.execute = AsyncMock(side_effect=Exception("connection refused"))

        with patch("tools.telegram.db._pool", mock_pool):
            from tools.telegram.db import save_message
            # Should not raise — logs and continues
            await save_message(chat_id=123, role="user", content="hello")

    async def test_no_pool_graceful(self):
        with patch("tools.telegram.db._pool", None):
            from tools.telegram.db import save_message
            # Should not raise when pool is None
            await save_message(chat_id=123, role="user", content="hello")

    async def test_init_creates_pool_and_migrates(self):
        mock_pool = AsyncMock()
        mock_pool.execute = AsyncMock()

        with patch("tools.telegram.db.asyncpg.create_pool", new_callable=AsyncMock, return_value=mock_pool) as mock_create:
            from tools.telegram.db import init
            await init(dsn="postgresql://test:test@localhost/test")

        mock_create.assert_awaited_once()
        # Migration should have run CREATE TABLE statements
        assert mock_pool.execute.await_count >= 2  # at least messages + tasks tables
```

- [ ] **Step 5: Implement db.py**

```python
# docker/mcp/tools/telegram/db.py
"""Postgres persistence — conversation history and task audit trail via asyncpg."""
from __future__ import annotations

import logging
from typing import Any

import asyncpg

from tools.telegram import config

logger = logging.getLogger(__name__)

_pool: asyncpg.Pool | None = None

_SCHEMA_MESSAGES = """
CREATE TABLE IF NOT EXISTS telegram_messages (
    id          SERIAL PRIMARY KEY,
    chat_id     BIGINT NOT NULL,
    role        TEXT NOT NULL,
    content     TEXT NOT NULL,
    message_id  INT,
    created_at  TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_tg_messages_chat
    ON telegram_messages(chat_id, created_at DESC);
"""

_SCHEMA_TASKS = """
CREATE TABLE IF NOT EXISTS telegram_tasks (
    task_id     TEXT PRIMARY KEY,
    chat_id     BIGINT NOT NULL,
    intent_type TEXT NOT NULL,
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
"""


async def init(dsn: str | None = None) -> None:
    """Create connection pool and run schema migrations."""
    global _pool
    dsn = dsn or config.DB_DSN
    try:
        _pool = await asyncpg.create_pool(dsn, min_size=1, max_size=3)
        await _pool.execute(_SCHEMA_MESSAGES)
        await _pool.execute(_SCHEMA_TASKS)
        logger.info("Telegram DB initialized")
    except Exception:
        logger.exception("Failed to initialize Telegram DB — continuing without persistence")
        _pool = None


async def close() -> None:
    """Close the connection pool."""
    global _pool
    if _pool:
        await _pool.close()
        _pool = None


async def save_message(
    chat_id: int, role: str, content: str, message_id: int | None = None,
) -> None:
    """Store a conversation message. Silently fails if DB is unavailable."""
    if _pool is None:
        return
    try:
        await _pool.execute(
            "INSERT INTO telegram_messages (chat_id, role, content, message_id) VALUES ($1, $2, $3, $4)",
            chat_id, role, content, message_id,
        )
    except Exception:
        logger.warning("Failed to save message — DB may be down")


async def get_history(chat_id: int, limit: int = 10) -> list[dict[str, Any]]:
    """Fetch recent conversation messages, oldest first."""
    if _pool is None:
        return []
    try:
        rows = await _pool.fetch(
            "SELECT role, content, created_at FROM telegram_messages "
            "WHERE chat_id = $1 ORDER BY created_at DESC LIMIT $2",
            chat_id, limit,
        )
        return [dict(r) for r in reversed(rows)]
    except Exception:
        logger.warning("Failed to fetch history — DB may be down")
        return []


async def save_task(
    task_id: str, chat_id: int, intent_type: str,
    description: str, repo: str | None = None,
) -> None:
    """Insert a new task record."""
    if _pool is None:
        return
    try:
        await _pool.execute(
            "INSERT INTO telegram_tasks (task_id, chat_id, intent_type, repo, description) "
            "VALUES ($1, $2, $3, $4, $5)",
            task_id, chat_id, intent_type, repo, description,
        )
    except Exception:
        logger.warning("Failed to save task %s", task_id)


async def update_task(
    task_id: str, status: str,
    files: list[str] | None = None,
    commit_sha: str | None = None,
    exit_code: int | None = None,
    summary: str | None = None,
) -> None:
    """Update task status and results."""
    if _pool is None:
        return
    try:
        await _pool.execute(
            "UPDATE telegram_tasks SET status=$1, files=$2, commit_sha=$3, "
            "exit_code=$4, summary=$5, finished_at=now() WHERE task_id=$6",
            status, files, commit_sha, exit_code, summary, task_id,
        )
    except Exception:
        logger.warning("Failed to update task %s", task_id)


async def recover_stale_tasks() -> None:
    """Mark any tasks still 'running' as failed (container restart recovery)."""
    if _pool is None:
        return
    try:
        await _pool.execute(
            "UPDATE telegram_tasks SET status='failed', "
            "summary='Container restarted', finished_at=now() "
            "WHERE status = 'running'"
        )
    except Exception:
        logger.warning("Failed to recover stale tasks")
```

- [ ] **Step 6: Add asyncpg to requirements.txt**

Add `asyncpg>=0.29.0` to `docker/mcp/requirements.txt`.

- [ ] **Step 7: Run db and auth tests**

Run: `cd ~/git/aichat && python -m pytest tests/tools/telegram/unit/test_auth.py tests/tools/telegram/unit/test_db.py -v`
Expected: PASS (11 tests)

- [ ] **Step 8: Commit**

```bash
cd ~/git/aichat
git add docker/mcp/tools/telegram/auth.py docker/mcp/tools/telegram/db.py \
        tests/tools/telegram/unit/test_auth.py tests/tools/telegram/unit/test_db.py \
        docker/mcp/requirements.txt
git commit -m "feat(telegram): add auth gate and Postgres persistence layer"
```

---

## Task 4: classifier.py — Hybrid Regex + LLM

**Files:**
- Create: `docker/mcp/tools/telegram/classifier.py`
- Create: `tests/tools/telegram/unit/test_classifier.py`

- [ ] **Step 1: Write classifier tests**

```python
# tests/tools/telegram/unit/test_classifier.py
"""Tests for telegram.classifier — regex patterns and LLM fallback."""
from __future__ import annotations

import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

ENV = {"TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_CHAT_ID": "1", "POSTGRES_PASSWORD": "pw"}


def _lm_response(content: str) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "choices": [{"message": {"content": content}}]
    }
    return resp


@pytest.mark.asyncio
class TestRegexPatterns:
    """Fast-path regex classification."""

    async def test_status_command(self):
        with patch.dict(os.environ, ENV, clear=False):
            from tools.telegram import config
            import importlib
            importlib.reload(config)
            from tools.telegram.classifier import classify
            intent = await classify("status")
        assert intent.type == "status"

    async def test_status_slash_command(self):
        with patch.dict(os.environ, ENV, clear=False):
            from tools.telegram import config
            import importlib
            importlib.reload(config)
            from tools.telegram.classifier import classify
            intent = await classify("/status")
        assert intent.type == "status"

    async def test_cancel_command(self):
        with patch.dict(os.environ, ENV, clear=False):
            from tools.telegram import config
            import importlib
            importlib.reload(config)
            from tools.telegram.classifier import classify
            intent = await classify("cancel")
        assert intent.type == "cancel"

    async def test_check_thermals(self):
        with patch.dict(os.environ, ENV, clear=False):
            from tools.telegram import config
            import importlib
            importlib.reload(config)
            from tools.telegram.classifier import classify
            intent = await classify("check thermals")
        assert intent.type == "tool"
        assert intent.tool == "monitor"
        assert intent.action == "thermals"

    async def test_show_containers(self):
        with patch.dict(os.environ, ENV, clear=False):
            from tools.telegram import config
            import importlib
            importlib.reload(config)
            from tools.telegram.classifier import classify
            intent = await classify("show containers")
        assert intent.type == "tool"
        assert intent.tool == "monitor"
        assert intent.action == "containers"

    async def test_fleet_overview(self):
        with patch.dict(os.environ, ENV, clear=False):
            from tools.telegram import config
            import importlib
            importlib.reload(config)
            from tools.telegram.classifier import classify
            intent = await classify("how's the fleet")
        assert intent.type == "tool"
        assert intent.tool == "monitor"
        assert intent.action == "overview"

    async def test_tail_logs(self):
        with patch.dict(os.environ, ENV, clear=False):
            from tools.telegram import config
            import importlib
            importlib.reload(config)
            from tools.telegram.classifier import classify
            intent = await classify("tail logs for mcp")
        assert intent.type == "tool"
        assert intent.tool == "log"
        assert intent.action == "tail"
        assert intent.args.get("service") == "mcp"

    async def test_git_status(self):
        with patch.dict(os.environ, ENV, clear=False):
            from tools.telegram import config
            import importlib
            importlib.reload(config)
            from tools.telegram.classifier import classify
            intent = await classify("git status in aichat")
        assert intent.type == "tool"
        assert intent.tool == "git"
        assert intent.action == "status"
        assert intent.args.get("repo") == "aichat"

    async def test_list_devices(self):
        with patch.dict(os.environ, ENV, clear=False):
            from tools.telegram import config
            import importlib
            importlib.reload(config)
            from tools.telegram.classifier import classify
            intent = await classify("list devices")
        assert intent.type == "tool"
        assert intent.tool == "iot"
        assert intent.action == "list_devices"

    async def test_case_insensitive(self):
        with patch.dict(os.environ, ENV, clear=False):
            from tools.telegram import config
            import importlib
            importlib.reload(config)
            from tools.telegram.classifier import classify
            intent = await classify("CHECK THERMALS")
        assert intent.type == "tool"
        assert intent.tool == "monitor"


@pytest.mark.asyncio
class TestLLMFallback:
    """Slow-path LLM classification."""

    async def test_ambiguous_routes_to_llm(self):
        lm_resp = _lm_response(json.dumps({"type": "tool", "tool": "monitor", "action": "overview"}))
        client = AsyncMock()
        client.post = AsyncMock(return_value=lm_resp)
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)

        with patch.dict(os.environ, ENV, clear=False):
            from tools.telegram import config
            import importlib
            importlib.reload(config)
            from tools.telegram.classifier import classify
            with patch("tools.telegram.classifier.httpx.AsyncClient", return_value=client):
                intent = await classify("something seems off with the servers")

        assert intent.type == "tool"
        client.post.assert_awaited_once()

    async def test_llm_returns_code_intent(self):
        lm_resp = _lm_response(json.dumps({"type": "code", "repo": "aichat", "task": "fix the bug"}))
        client = AsyncMock()
        client.post = AsyncMock(return_value=lm_resp)
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)

        with patch.dict(os.environ, ENV, clear=False):
            from tools.telegram import config
            import importlib
            importlib.reload(config)
            from tools.telegram.classifier import classify
            with patch("tools.telegram.classifier.httpx.AsyncClient", return_value=client):
                intent = await classify("fix the authentication bug in aichat")

        assert intent.type == "code"
        assert intent.repo == "aichat"

    async def test_malformed_json_falls_back_to_question(self):
        lm_resp = _lm_response("not valid json {{{")
        client = AsyncMock()
        client.post = AsyncMock(return_value=lm_resp)
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)

        with patch.dict(os.environ, ENV, clear=False):
            from tools.telegram import config
            import importlib
            importlib.reload(config)
            from tools.telegram.classifier import classify
            with patch("tools.telegram.classifier.httpx.AsyncClient", return_value=client):
                intent = await classify("blah blah blah")

        assert intent.type == "question"

    async def test_network_error_falls_back_to_question(self):
        client = AsyncMock()
        client.post = AsyncMock(side_effect=Exception("connection refused"))
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)

        with patch.dict(os.environ, ENV, clear=False):
            from tools.telegram import config
            import importlib
            importlib.reload(config)
            from tools.telegram.classifier import classify
            with patch("tools.telegram.classifier.httpx.AsyncClient", return_value=client):
                intent = await classify("something weird")

        assert intent.type == "question"

    async def test_create_intent(self):
        lm_resp = _lm_response(json.dumps({
            "type": "create", "name": "myapp", "language": "python",
            "description": "a web app"
        }))
        client = AsyncMock()
        client.post = AsyncMock(return_value=lm_resp)
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)

        with patch.dict(os.environ, ENV, clear=False):
            from tools.telegram import config
            import importlib
            importlib.reload(config)
            from tools.telegram.classifier import classify
            with patch("tools.telegram.classifier.httpx.AsyncClient", return_value=client):
                intent = await classify("create a new python web app called myapp")

        assert intent.type == "create"
        assert intent.name == "myapp"
        assert intent.language == "python"
```

- [ ] **Step 2: Run to verify fail**

Run: `cd ~/git/aichat && python -m pytest tests/tools/telegram/unit/test_classifier.py -v`
Expected: FAIL

- [ ] **Step 3: Implement classifier.py**

```python
# docker/mcp/tools/telegram/classifier.py
"""Hybrid classifier — regex fast path + Gemma LLM fallback."""
from __future__ import annotations

import json
import logging
import re

import httpx

from tools.telegram import config
from tools.telegram.models import Intent

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Regex patterns — compiled once, checked top-to-bottom
# ---------------------------------------------------------------------------

_Pattern = tuple[re.Pattern, callable]

def _build_patterns() -> list[_Pattern]:
    """Build the ordered list of (compiled_regex, intent_factory) pairs."""
    patterns: list[_Pattern] = []

    def _add(pattern: str, factory):
        patterns.append((re.compile(pattern, re.IGNORECASE), factory))

    # Direct commands
    _add(r"^/?status$", lambda m: Intent(type="status"))
    _add(r"^/?cancel$", lambda m: Intent(type="cancel"))

    # Monitor — specific metrics
    _add(
        r"(?:check|show|get)\s+(thermals?|containers?|disk|gpu|services?|tailscale)",
        lambda m: Intent(type="tool", tool="monitor", action=re.sub(r"s$", "", m.group(1).lower())),
    )

    # Monitor — overview
    _add(
        r"(?:monitor|overview|fleet|how.*fleet)",
        lambda m: Intent(type="tool", tool="monitor", action="overview"),
    )

    # Logs
    _add(
        r"(?:tail|read|show)\s+logs?\s*(?:for|of|from)?\s*(?P<svc>\S+)?",
        lambda m: Intent(
            type="tool", tool="log", action="tail",
            args={"service": m.group("svc")} if m.group("svc") else {},
        ),
    )

    # Git
    _add(
        r"git\s+(?P<action>status|log|diff|ci|issues?|scorecard|push)\s*(?:in|for|of)?\s*(?P<repo>\S+)?",
        lambda m: Intent(
            type="tool", tool="git",
            action=re.sub(r"s$", "", m.group("action").lower()),
            args={"repo": m.group("repo")} if m.group("repo") else {},
        ),
    )

    # IoT
    _add(
        r"(?:list|show)\s+(?:devices?|sensors?|switches?)",
        lambda m: Intent(type="tool", tool="iot", action="list_devices"),
    )

    # SSH
    _add(
        r"(?:ssh|run)\s+(?:on\s+)?(?P<host>\S+)\s+(?P<cmd>.+)",
        lambda m: Intent(
            type="tool", tool="ssh", action="exec",
            args={"host": m.group("host"), "command": m.group("cmd")},
        ),
    )

    # Notify
    _add(
        r"(?:send|notify|alert)\s+(?P<msg>.+)",
        lambda m: Intent(type="tool", tool="notify", action="send", args={"message": m.group("msg")}),
    )

    return patterns


_PATTERNS = _build_patterns()

# ---------------------------------------------------------------------------
# LLM classifier prompt (same as original, for ambiguous messages)
# ---------------------------------------------------------------------------

_CLASSIFIER_SYSTEM_PROMPT = """\
You are an intent classifier for a home-lab infrastructure bot.
Classify the user message into ONE of these types and return raw JSON only (no markdown).

Types:
1. "tool" — invoke an MCP tool. Fields: type, tool, action, args (object)
   Tools: monitor (overview|containers|thermals|disk|gpu|services|tailscale),
          git (status|log|diff|ci|scorecard|create_pr|merge|push|trigger_ci|issues),
          notify (send|send_alert|send_photo|send_document),
          ssh (exec), log (read|tail|list),
          iot (list_devices|read_sensor|toggle_switch)
2. "code" — modify an existing repo. Fields: type, repo, task
3. "create" — new project from scratch. Fields: type, name, language, description
4. "question" — open-ended query. Fields: type, text

If unsure, use type "question" with the original text.
Return ONLY valid JSON, no wrapping.\
"""


async def classify(text: str) -> Intent:
    """Classify a message. Tries regex first, falls back to LLM."""
    # Fast path — regex
    for pattern, factory in _PATTERNS:
        match = pattern.search(text)
        if match:
            return factory(match)

    # Slow path — LLM
    return await _classify_llm(text)


async def _classify_llm(text: str) -> Intent:
    """Send to Gemma for classification. Returns question Intent on any failure."""
    try:
        async with httpx.AsyncClient(timeout=config.LM_CLASSIFY_TIMEOUT) as client:
            resp = await client.post(
                f"{config.LM_STUDIO_URL}/v1/chat/completions",
                json={
                    "model": config.LM_STUDIO_MODEL,
                    "messages": [
                        {"role": "system", "content": _CLASSIFIER_SYSTEM_PROMPT},
                        {"role": "user", "content": text},
                    ],
                    "temperature": 0.1,
                    "max_tokens": 256,
                },
            )
        raw = resp.json()["choices"][0]["message"]["content"]

        # Strip markdown wrappers if present
        raw = re.sub(r"^```(?:json)?\s*", "", raw.strip())
        raw = re.sub(r"\s*```$", "", raw)

        data = json.loads(raw)
        return _parse_llm_response(data, text)

    except Exception:
        logger.warning("LLM classification failed — falling back to question", exc_info=True)
        return Intent(type="question", text=text)


def _parse_llm_response(data: dict, original_text: str) -> Intent:
    """Map LLM JSON response to Intent."""
    intent_type = data.get("type", "question")

    if intent_type == "tool":
        return Intent(
            type="tool",
            tool=data.get("tool", ""),
            action=data.get("action", ""),
            args=data.get("args", {}),
        )
    elif intent_type == "code":
        return Intent(
            type="code",
            repo=data.get("repo"),
            task=data.get("task", original_text),
        )
    elif intent_type == "create":
        return Intent(
            type="create",
            name=data.get("name", ""),
            language=data.get("language", ""),
            description=data.get("description", ""),
        )
    else:
        return Intent(type="question", text=data.get("text", original_text))
```

- [ ] **Step 4: Run tests**

Run: `cd ~/git/aichat && python -m pytest tests/tools/telegram/unit/test_classifier.py -v`
Expected: PASS (15 tests)

- [ ] **Step 5: Commit**

```bash
cd ~/git/aichat
git add docker/mcp/tools/telegram/classifier.py tests/tools/telegram/unit/test_classifier.py
git commit -m "feat(telegram): add hybrid regex + LLM classifier"
```

---

## Task 5: summary.py + stream.py

**Files:**
- Create: `docker/mcp/tools/telegram/summary.py`
- Create: `docker/mcp/tools/telegram/stream.py`
- Create: `tests/tools/telegram/unit/test_summary.py`
- Create: `tests/tools/telegram/unit/test_stream.py`

- [ ] **Step 1: Write summary tests**

```python
# tests/tools/telegram/unit/test_summary.py
"""Tests for telegram.summary — summary builder."""
from __future__ import annotations

from tools.telegram.models import TaskState
from tools.telegram.summary import build_summary


class TestBuildSummary:
    def test_full_summary(self):
        ts = TaskState(task_id="abc12345", description="fix logging", repo="aichat")
        ts.started_at = 0  # will compute elapsed from current time
        result = build_summary(
            task_state=ts,
            final_text="Fixed the logging issue in main.py",
            edited_files=["/home/user/git/aichat/main.py", "/home/user/git/aichat/utils.py"],
            last_bash_output="[main abc1234] fix: logging\n 2 files changed",
            returncode=0,
        )
        assert "Done" in result
        assert "main.py" in result
        assert "utils.py" in result
        assert "abc1234" in result

    def test_failed_summary(self):
        ts = TaskState(task_id="abc12345", description="break things", repo="aichat")
        result = build_summary(
            task_state=ts, final_text="error occurred",
            edited_files=[], last_bash_output="", returncode=1,
        )
        assert "Failed (exit 1)" in result

    def test_no_files_no_commit(self):
        ts = TaskState(task_id="abc12345", description="read-only check")
        result = build_summary(
            task_state=ts, final_text="All looks good",
            edited_files=[], last_bash_output="", returncode=0,
        )
        assert "Done" in result
        assert "Files:" not in result
        assert "Commit:" not in result
```

- [ ] **Step 2: Implement summary.py**

```python
# docker/mcp/tools/telegram/summary.py
"""Build human-readable task summaries for Telegram."""
from __future__ import annotations

import os
import re
import time

from tools.telegram.models import TaskState


def build_summary(
    task_state: TaskState,
    final_text: str,
    edited_files: list[str],
    last_bash_output: str,
    returncode: int | None,
) -> str:
    """Build a summary string from task results."""
    elapsed = int(time.monotonic() - task_state.started_at)
    status = "Done" if returncode == 0 else f"Failed (exit {returncode})"
    repo = task_state.repo or "unknown"

    lines = [f"*{status}* — {repo} ({elapsed}s)"]

    # Description or final text (truncated)
    desc = final_text or task_state.description
    if len(desc) > 300:
        desc = desc[:297] + "..."
    lines.append(f"\n{desc}")

    # Edited files (unique basenames)
    if edited_files:
        basenames = sorted(set(os.path.basename(f) for f in edited_files))
        lines.append(f"\nFiles: {', '.join(basenames)}")

    # Commit SHA
    sha_match = re.search(r"\[.+?\s+([0-9a-f]{7,})\]", last_bash_output)
    if sha_match:
        lines.append(f"Commit: `{sha_match.group(1)}`")

    return "\n".join(lines)
```

- [ ] **Step 3: Run summary tests**

Run: `cd ~/git/aichat && python -m pytest tests/tools/telegram/unit/test_summary.py -v`
Expected: PASS (3 tests)

- [ ] **Step 4: Write stream tests**

```python
# tests/tools/telegram/unit/test_stream.py
"""Tests for telegram.stream — Claude streaming, milestones, heartbeat."""
from __future__ import annotations

import asyncio
import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tools.telegram.models import TaskState

ENV = {"TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_CHAT_ID": "1", "POSTGRES_PASSWORD": "pw"}


def _stream_line(event: dict) -> bytes:
    return json.dumps(event).encode() + b"\n"


@pytest.mark.asyncio
class TestStreamClaude:
    async def test_detects_read_milestone(self):
        lines = [
            _stream_line({"type": "tool_use", "tool": "Read", "tool_input": {"file_path": "/app/main.py"}}),
            _stream_line({"type": "result", "result": "done"}),
        ]

        mock_proc = AsyncMock()
        mock_proc.stdout = AsyncMock()
        mock_proc.stdout.readline = AsyncMock(side_effect=lines + [b""])
        mock_proc.wait = AsyncMock(return_value=0)
        mock_proc.returncode = 0

        ts = TaskState(task_id="t1", description="test", repo="test")
        ts.process = mock_proc

        with patch.dict(os.environ, ENV, clear=False):
            from tools.telegram import config
            import importlib
            importlib.reload(config)
            from tools.telegram.stream import stream_claude
            with patch("tools.telegram.stream.api.send_message", new_callable=AsyncMock) as mock_send, \
                 patch("tools.telegram.stream.asyncio.create_subprocess_shell", return_value=mock_proc):
                result = await stream_claude("echo test", reply_to=1, task_state=ts)

        # Should have sent "Reading codebase..." milestone
        calls = [c[0][0] for c in mock_send.call_args_list]
        assert any("Reading" in c for c in calls)

    async def test_detects_write_milestone(self):
        lines = [
            _stream_line({"type": "tool_use", "tool": "Edit", "tool_input": {"file_path": "/app/main.py"}}),
            _stream_line({"type": "result", "result": "done"}),
        ]

        mock_proc = AsyncMock()
        mock_proc.stdout = AsyncMock()
        mock_proc.stdout.readline = AsyncMock(side_effect=lines + [b""])
        mock_proc.wait = AsyncMock(return_value=0)
        mock_proc.returncode = 0

        ts = TaskState(task_id="t1", description="test", repo="test")
        ts.process = mock_proc

        with patch.dict(os.environ, ENV, clear=False):
            from tools.telegram import config
            import importlib
            importlib.reload(config)
            from tools.telegram.stream import stream_claude
            with patch("tools.telegram.stream.api.send_message", new_callable=AsyncMock) as mock_send, \
                 patch("tools.telegram.stream.asyncio.create_subprocess_shell", return_value=mock_proc):
                result = await stream_claude("echo test", reply_to=1, task_state=ts)

        calls = [c[0][0] for c in mock_send.call_args_list]
        assert any("Writing" in c for c in calls)

    async def test_extracts_edited_files(self):
        lines = [
            _stream_line({"type": "tool_use", "tool": "Edit", "tool_input": {"file_path": "/app/a.py"}}),
            _stream_line({"type": "tool_use", "tool": "Write", "tool_input": {"file_path": "/app/b.py"}}),
            _stream_line({"type": "result", "result": "done"}),
        ]

        mock_proc = AsyncMock()
        mock_proc.stdout = AsyncMock()
        mock_proc.stdout.readline = AsyncMock(side_effect=lines + [b""])
        mock_proc.wait = AsyncMock(return_value=0)
        mock_proc.returncode = 0

        ts = TaskState(task_id="t1", description="test", repo="test")
        ts.process = mock_proc

        with patch.dict(os.environ, ENV, clear=False):
            from tools.telegram import config
            import importlib
            importlib.reload(config)
            from tools.telegram.stream import stream_claude
            with patch("tools.telegram.stream.api.send_message", new_callable=AsyncMock), \
                 patch("tools.telegram.stream.asyncio.create_subprocess_shell", return_value=mock_proc):
                result = await stream_claude("echo test", reply_to=1, task_state=ts)

        assert "a.py" in result
        assert "b.py" in result

    async def test_timeout_kills_process(self):
        async def slow_readline():
            await asyncio.sleep(100)
            return b""

        mock_proc = AsyncMock()
        mock_proc.stdout = AsyncMock()
        mock_proc.stdout.readline = slow_readline
        mock_proc.kill = MagicMock()
        mock_proc.wait = AsyncMock(return_value=-9)
        mock_proc.returncode = -9

        ts = TaskState(task_id="t1", description="test", repo="test")
        ts.process = mock_proc

        with patch.dict(os.environ, {**ENV}, clear=False):
            from tools.telegram import config
            import importlib
            importlib.reload(config)
            # Override timeout to 0.1s for test speed
            with patch("tools.telegram.stream.config.CLAUDE_TIMEOUT", 0.1):
                from tools.telegram.stream import stream_claude
                with patch("tools.telegram.stream.api.send_message", new_callable=AsyncMock), \
                     patch("tools.telegram.stream.asyncio.create_subprocess_shell", return_value=mock_proc):
                    result = await stream_claude("echo test", reply_to=1, task_state=ts)

        assert "timed out" in result.lower() or "Timed out" in result

    async def test_commit_sha_extracted(self):
        lines = [
            _stream_line({"type": "tool_use", "tool": "Bash", "tool_input": {"command": "git commit -m 'fix'"}}),
            _stream_line({"type": "tool_result", "content": [{"type": "text", "text": "[main abc1234] fix: stuff\n 1 file changed"}]}),
            _stream_line({"type": "result", "result": "done"}),
        ]

        mock_proc = AsyncMock()
        mock_proc.stdout = AsyncMock()
        mock_proc.stdout.readline = AsyncMock(side_effect=lines + [b""])
        mock_proc.wait = AsyncMock(return_value=0)
        mock_proc.returncode = 0

        ts = TaskState(task_id="t1", description="test", repo="test")
        ts.process = mock_proc

        with patch.dict(os.environ, ENV, clear=False):
            from tools.telegram import config
            import importlib
            importlib.reload(config)
            from tools.telegram.stream import stream_claude
            with patch("tools.telegram.stream.api.send_message", new_callable=AsyncMock), \
                 patch("tools.telegram.stream.asyncio.create_subprocess_shell", return_value=mock_proc):
                result = await stream_claude("echo test", reply_to=1, task_state=ts)

        assert "abc1234" in result

    async def test_deduplicates_edited_files(self):
        lines = [
            _stream_line({"type": "tool_use", "tool": "Edit", "tool_input": {"file_path": "/app/main.py"}}),
            _stream_line({"type": "tool_use", "tool": "Edit", "tool_input": {"file_path": "/app/main.py"}}),
            _stream_line({"type": "result", "result": "done"}),
        ]

        mock_proc = AsyncMock()
        mock_proc.stdout = AsyncMock()
        mock_proc.stdout.readline = AsyncMock(side_effect=lines + [b""])
        mock_proc.wait = AsyncMock(return_value=0)
        mock_proc.returncode = 0

        ts = TaskState(task_id="t1", description="test", repo="test")
        ts.process = mock_proc

        with patch.dict(os.environ, ENV, clear=False):
            from tools.telegram import config
            import importlib
            importlib.reload(config)
            from tools.telegram.stream import stream_claude
            with patch("tools.telegram.stream.api.send_message", new_callable=AsyncMock), \
                 patch("tools.telegram.stream.asyncio.create_subprocess_shell", return_value=mock_proc):
                result = await stream_claude("echo test", reply_to=1, task_state=ts)

        # main.py should appear only once
        assert result.count("main.py") == 1
```

- [ ] **Step 5: Implement stream.py**

```python
# docker/mcp/tools/telegram/stream.py
"""Claude Code stream-json parser with milestone detection and heartbeat."""
from __future__ import annotations

import asyncio
import json
import logging
import re

from tools.telegram import api, config
from tools.telegram.models import TaskState
from tools.telegram.summary import build_summary

logger = logging.getLogger(__name__)

# Milestones — each fires at most once
_READ_TOOLS = {"Read", "Glob", "Grep"}
_WRITE_TOOLS = {"Edit", "Write"}
_TEST_PATTERNS = re.compile(r"pytest|jest|cargo test|go test|npm test|make test", re.IGNORECASE)


async def stream_claude(
    ssh_command: str,
    reply_to: int | None,
    task_state: TaskState,
) -> str:
    """Run an SSH command, parse Claude stream-json, send milestones, return summary."""

    ssh_full = (
        f"ssh -i {config.SSH_KEY} "
        f"-o StrictHostKeyChecking=accept-new "
        f"-o ConnectTimeout=10 "
        f"-o BatchMode=yes "
        f"-o ServerAliveInterval=30 "
        f"-o ServerAliveCountMax=3 "
        f"-p {config.SSH_PORT} "
        f"{config.SSH_USER}@{config.SSH_HOST} "
        f"{ssh_command}"
    )

    proc = await asyncio.create_subprocess_shell(
        ssh_full,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    task_state.process = proc

    edited_files: list[str] = []
    last_bash_output = ""
    final_text = ""
    sent_milestones: set[str] = set()

    # Heartbeat — sends "Still working..." every HEARTBEAT_INTERVAL seconds
    last_activity = asyncio.get_event_loop().time()

    async def heartbeat():
        nonlocal last_activity
        while True:
            await asyncio.sleep(config.HEARTBEAT_INTERVAL)
            elapsed = asyncio.get_event_loop().time() - last_activity
            if elapsed >= config.HEARTBEAT_INTERVAL:
                await api.send_message(
                    f"Still working on: {task_state.description}...", reply_to=reply_to,
                )
                last_activity = asyncio.get_event_loop().time()

    heartbeat_task = asyncio.create_task(heartbeat())

    try:
        while True:
            try:
                line = await asyncio.wait_for(
                    proc.stdout.readline(), timeout=config.CLAUDE_TIMEOUT,
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                heartbeat_task.cancel()
                return f"Timed out after {config.CLAUDE_TIMEOUT}s"

            if not line:
                break

            last_activity = asyncio.get_event_loop().time()

            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            # Track milestones
            tool = event.get("tool", "")
            tool_input = event.get("tool_input", {})

            if tool in _READ_TOOLS and "reading" not in sent_milestones:
                sent_milestones.add("reading")
                await api.send_message("Reading codebase...", reply_to=reply_to)

            if tool in _WRITE_TOOLS and "writing" not in sent_milestones:
                sent_milestones.add("writing")
                await api.send_message("Writing code...", reply_to=reply_to)

            if tool in _WRITE_TOOLS:
                fp = tool_input.get("file_path", "")
                if fp:
                    edited_files.append(fp)

            if tool == "Bash":
                cmd = tool_input.get("command", "")
                if _TEST_PATTERNS.search(cmd) and "testing" not in sent_milestones:
                    sent_milestones.add("testing")
                    await api.send_message("Running tests...", reply_to=reply_to)
                if "git commit" in cmd and "committing" not in sent_milestones:
                    sent_milestones.add("committing")
                    await api.send_message("Committing...", reply_to=reply_to)

            # Track tool_result content (for bash output / commit SHA)
            if event.get("type") == "tool_result":
                content = event.get("content", [])
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            last_bash_output = block["text"]

            # Track final text
            if event.get("type") in ("assistant", "result"):
                text = event.get("result", "") or event.get("content", "")
                if isinstance(text, str) and text:
                    final_text = text

    finally:
        heartbeat_task.cancel()
        try:
            await heartbeat_task
        except asyncio.CancelledError:
            pass

    await proc.wait()
    returncode = proc.returncode

    return build_summary(task_state, final_text, edited_files, last_bash_output, returncode)
```

- [ ] **Step 6: Run tests**

Run: `cd ~/git/aichat && python -m pytest tests/tools/telegram/unit/test_summary.py tests/tools/telegram/unit/test_stream.py -v`
Expected: PASS (9 tests)

- [ ] **Step 7: Commit**

```bash
cd ~/git/aichat
git add docker/mcp/tools/telegram/summary.py docker/mcp/tools/telegram/stream.py \
        tests/tools/telegram/unit/test_summary.py tests/tools/telegram/unit/test_stream.py
git commit -m "feat(telegram): add stream parser and summary builder"
```

---

## Task 6: Handlers

**Files:**
- Create: `docker/mcp/tools/telegram/handlers/__init__.py`
- Create: `docker/mcp/tools/telegram/handlers/tool.py`
- Create: `docker/mcp/tools/telegram/handlers/code.py`
- Create: `docker/mcp/tools/telegram/handlers/create.py`
- Create: `docker/mcp/tools/telegram/handlers/question.py`
- Create: `docker/mcp/tools/telegram/handlers/status.py`
- Create: `docker/mcp/tools/telegram/handlers/cancel.py`
- Create: `tests/tools/telegram/unit/test_handlers.py`

- [ ] **Step 1: Write handler tests**

```python
# tests/tools/telegram/unit/test_handlers.py
"""Tests for telegram.handlers — all dispatchers."""
from __future__ import annotations

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tools.telegram.models import Intent, TaskState

ENV = {"TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_CHAT_ID": "1", "POSTGRES_PASSWORD": "pw",
       "IMAGE_GEN_BASE_URL": "http://lm:1234"}


def _lm_response(content: str) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"choices": [{"message": {"content": content}}]}
    return resp


@pytest.mark.asyncio
class TestToolHandler:
    async def test_calls_handler_and_replies(self):
        handler_fn = AsyncMock(return_value=[{"type": "text", "text": "CPU: 45C"}])

        with patch.dict(os.environ, ENV, clear=False):
            from tools.telegram import config
            import importlib
            importlib.reload(config)
            from tools.telegram.handlers.tool import handle_tool
            with patch("tools.telegram.handlers.tool.TOOL_HANDLERS", {"monitor": handler_fn}), \
                 patch("tools.telegram.handlers.tool.api.send_message", new_callable=AsyncMock) as mock_send:
                intent = Intent(type="tool", tool="monitor", action="thermals")
                await handle_tool(intent, reply_to=1)

        handler_fn.assert_awaited_once()
        calls = [c[0][0] for c in mock_send.call_args_list]
        assert any("CPU: 45C" in c for c in calls)

    async def test_unknown_tool_sends_error(self):
        with patch.dict(os.environ, ENV, clear=False):
            from tools.telegram import config
            import importlib
            importlib.reload(config)
            from tools.telegram.handlers.tool import handle_tool
            with patch("tools.telegram.handlers.tool.TOOL_HANDLERS", {}), \
                 patch("tools.telegram.handlers.tool.api.send_message", new_callable=AsyncMock) as mock_send:
                intent = Intent(type="tool", tool="nonexistent", action="x")
                await handle_tool(intent, reply_to=1)

        assert any("Unknown tool" in c[0][0] for c in mock_send.call_args_list)


@pytest.mark.asyncio
class TestQuestionHandler:
    async def test_sends_answer(self):
        client = AsyncMock()
        client.post = AsyncMock(return_value=_lm_response("The fleet is fine."))
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)

        with patch.dict(os.environ, ENV, clear=False):
            from tools.telegram import config
            import importlib
            importlib.reload(config)
            from tools.telegram.handlers.question import handle_question
            with patch("tools.telegram.handlers.question.httpx.AsyncClient", return_value=client), \
                 patch("tools.telegram.handlers.question.api.send_message", new_callable=AsyncMock) as mock_send, \
                 patch("tools.telegram.handlers.question.db.get_history", new_callable=AsyncMock, return_value=[]), \
                 patch("tools.telegram.handlers.question.db.save_message", new_callable=AsyncMock):
                intent = Intent(type="question", text="how's the fleet?")
                await handle_question(intent, chat_id=123, reply_to=1)

        assert any("fleet is fine" in c[0][0] for c in mock_send.call_args_list)


@pytest.mark.asyncio
class TestStatusHandler:
    async def test_no_active_tasks(self):
        with patch.dict(os.environ, ENV, clear=False):
            from tools.telegram import config
            import importlib
            importlib.reload(config)
            from tools.telegram.handlers.status import handle_status
            with patch("tools.telegram.handlers.status.api.send_message", new_callable=AsyncMock) as mock_send, \
                 patch("tools.telegram.handlers.status._active_tasks", {}):
                await handle_status(reply_to=1)

        assert any("No active tasks" in c[0][0] for c in mock_send.call_args_list)

    async def test_lists_active_tasks(self):
        ts = TaskState(task_id="abc12345", description="fix bug", repo="aichat")
        with patch.dict(os.environ, ENV, clear=False):
            from tools.telegram import config
            import importlib
            importlib.reload(config)
            from tools.telegram.handlers.status import handle_status
            with patch("tools.telegram.handlers.status.api.send_message", new_callable=AsyncMock) as mock_send, \
                 patch("tools.telegram.handlers.status._active_tasks", {"abc12345": ts}):
                await handle_status(reply_to=1)

        calls = [c[0][0] for c in mock_send.call_args_list]
        assert any("abc12345" in c for c in calls)


@pytest.mark.asyncio
class TestCancelHandler:
    async def test_nothing_to_cancel(self):
        with patch.dict(os.environ, ENV, clear=False):
            from tools.telegram import config
            import importlib
            importlib.reload(config)
            from tools.telegram.handlers.cancel import handle_cancel
            with patch("tools.telegram.handlers.cancel.api.send_message", new_callable=AsyncMock) as mock_send, \
                 patch("tools.telegram.handlers.cancel._active_tasks", {}):
                await handle_cancel(reply_to=1)

        assert any("Nothing to cancel" in c[0][0] for c in mock_send.call_args_list)

    async def test_cancels_most_recent(self):
        ts = TaskState(task_id="abc12345", description="fix bug")
        ts.asyncio_task = AsyncMock()
        ts.asyncio_task.cancel = MagicMock()
        ts.process = MagicMock()
        ts.process.kill = MagicMock()

        tasks = {"abc12345": ts}
        with patch.dict(os.environ, ENV, clear=False):
            from tools.telegram import config
            import importlib
            importlib.reload(config)
            from tools.telegram.handlers.cancel import handle_cancel
            with patch("tools.telegram.handlers.cancel.api.send_message", new_callable=AsyncMock) as mock_send, \
                 patch("tools.telegram.handlers.cancel._active_tasks", tasks), \
                 patch("tools.telegram.handlers.cancel.db.update_task", new_callable=AsyncMock):
                await handle_cancel(reply_to=1)

        ts.process.kill.assert_called_once()
        ts.asyncio_task.cancel.assert_called_once()
        assert any("Cancelled" in c[0][0] for c in mock_send.call_args_list)


@pytest.mark.asyncio
class TestCodeHandler:
    async def test_rejects_invalid_repo_name(self):
        with patch.dict(os.environ, ENV, clear=False):
            from tools.telegram import config
            import importlib
            importlib.reload(config)
            from tools.telegram.handlers.code import handle_code
            with patch("tools.telegram.handlers.code.api.send_message", new_callable=AsyncMock) as mock_send:
                intent = Intent(type="code", repo="../escape", task="hack")
                await handle_code(intent, chat_id=123, reply_to=1)

        assert any("Invalid repo" in c[0][0] for c in mock_send.call_args_list)
```

- [ ] **Step 2: Run to verify fail**

Run: `cd ~/git/aichat && python -m pytest tests/tools/telegram/unit/test_handlers.py -v`
Expected: FAIL

- [ ] **Step 3: Create handlers package**

```python
# docker/mcp/tools/telegram/handlers/__init__.py
"""Handler modules for each intent type."""
```

- [ ] **Step 4: Implement handlers/tool.py**

```python
# docker/mcp/tools/telegram/handlers/tool.py
"""Tool dispatch — invoke MCP TOOL_HANDLERS."""
from __future__ import annotations

import logging

from tools import TOOL_HANDLERS
from tools.telegram import api
from tools.telegram.models import Intent

logger = logging.getLogger(__name__)


async def handle_tool(intent: Intent, reply_to: int | None = None) -> None:
    """Look up and call a registered MCP tool handler."""
    handler = TOOL_HANDLERS.get(intent.tool)
    if handler is None:
        await api.send_message(f"Unknown tool: `{intent.tool}`.", reply_to=reply_to)
        return

    await api.send_message(f"Running: {intent.tool} {intent.action}", reply_to=reply_to)
    try:
        result = await handler({"action": intent.action, **intent.args})
        if isinstance(result, list):
            texts = [b["text"] for b in result if isinstance(b, dict) and b.get("type") == "text"]
            reply_text = "\n".join(texts) if texts else str(result)
        else:
            reply_text = str(result)
        await api.send_message(reply_text, reply_to=reply_to)
    except Exception as exc:
        logger.error("Tool %s failed: %s", intent.tool, exc)
        await api.send_message(f"Error running {intent.tool}: {exc}", reply_to=reply_to)
```

- [ ] **Step 5: Implement handlers/question.py**

```python
# docker/mcp/tools/telegram/handlers/question.py
"""Q&A handler — conversational answers via Gemma with history context."""
from __future__ import annotations

import logging

import httpx

from tools.telegram import api, config, db
from tools.telegram.models import Intent

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are a helpful infrastructure assistant for a home lab running Fedora 43.
You have access to monitoring, git, SSH, IoT, and log tools.
Answer questions concisely.
If the user seems to want an action, suggest the right phrasing.\
"""


async def handle_question(
    intent: Intent,
    chat_id: int,
    reply_to: int | None = None,
) -> None:
    """Answer a question using Gemma with conversation context."""
    # Build message history
    messages = [{"role": "system", "content": _SYSTEM_PROMPT}]

    history = await db.get_history(chat_id, limit=10)
    for msg in history:
        messages.append({"role": msg["role"], "content": msg["content"]})

    messages.append({"role": "user", "content": intent.text})

    try:
        async with httpx.AsyncClient(timeout=config.LM_QA_TIMEOUT) as client:
            resp = await client.post(
                f"{config.LM_STUDIO_URL}/v1/chat/completions",
                json={
                    "model": config.LM_STUDIO_MODEL,
                    "messages": messages,
                    "temperature": 0.7,
                    "max_tokens": 512,
                },
            )
        answer = resp.json()["choices"][0]["message"]["content"]
        await api.send_message(answer, reply_to=reply_to)
        await db.save_message(chat_id, "assistant", answer)
    except Exception as exc:
        logger.error("Question handler failed: %s", exc)
        await api.send_message(f"Error: {exc}", reply_to=reply_to)
```

- [ ] **Step 6: Implement handlers/status.py and handlers/cancel.py**

```python
# docker/mcp/tools/telegram/handlers/status.py
"""Task status listing."""
from __future__ import annotations

import time

from tools.telegram import api, db
from tools.telegram.models import TaskState

# Shared mutable state — imported by poller and code/create handlers
_active_tasks: dict[str, TaskState] = {}


async def handle_status(reply_to: int | None = None) -> None:
    """List all active tasks."""
    if not _active_tasks:
        await api.send_message("No active tasks.", reply_to=reply_to)
        return

    lines = ["**Active tasks:**"]
    for tid, ts in _active_tasks.items():
        elapsed = int(time.monotonic() - ts.started_at)
        lines.append(f"  `{tid}` [{ts.status}] {elapsed}s — {ts.description}")
    await api.send_message("\n".join(lines), reply_to=reply_to)
```

```python
# docker/mcp/tools/telegram/handlers/cancel.py
"""Task cancellation."""
from __future__ import annotations

import logging

from tools.telegram import api, db
from tools.telegram.handlers.status import _active_tasks

logger = logging.getLogger(__name__)


async def handle_cancel(reply_to: int | None = None) -> None:
    """Cancel the most recently started task."""
    if not _active_tasks:
        await api.send_message("Nothing to cancel.", reply_to=reply_to)
        return

    # Find most recent by started_at
    tid = max(_active_tasks, key=lambda k: _active_tasks[k].started_at)
    ts = _active_tasks.pop(tid)

    if ts.process:
        try:
            ts.process.kill()
        except Exception:
            logger.warning("Failed to kill process for task %s", tid)

    if ts.asyncio_task:
        ts.asyncio_task.cancel()

    await db.update_task(tid, status="cancelled", summary="Cancelled by user")
    await api.send_message(f"Cancelled task `{tid}`: {ts.description}", reply_to=reply_to)
```

- [ ] **Step 7: Implement handlers/code.py and handlers/create.py**

```python
# docker/mcp/tools/telegram/handlers/code.py
"""Code modification handler — SSH → Claude Code."""
from __future__ import annotations

import asyncio
import logging
import re
import shlex
import uuid

from tools.telegram import api, config, db
from tools.telegram.handlers.status import _active_tasks
from tools.telegram.models import Intent, TaskState
from tools.telegram.stream import stream_claude

logger = logging.getLogger(__name__)

_VALID_NAME = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$")


async def handle_code(
    intent: Intent,
    chat_id: int,
    reply_to: int | None = None,
) -> None:
    """Spawn a background Claude Code task to modify a repo."""
    repo = intent.repo
    if not repo or not _VALID_NAME.match(repo):
        await api.send_message(f"Invalid repo name: `{repo}`", reply_to=reply_to)
        return

    task_id = uuid.uuid4().hex[:8]
    ts = TaskState(task_id=task_id, description=intent.task, repo=repo)
    _active_tasks[task_id] = ts

    await db.save_task(task_id, chat_id, "code", intent.task, repo)
    await api.send_message(
        f"Starting task `{task_id}`: {intent.task} (repo: {repo})", reply_to=reply_to,
    )

    escaped = shlex.quote(intent.task)
    ssh_cmd = (
        f'cd "$HOME/git/{repo}" && '
        f"claude --output-format stream-json --dangerously-skip-permissions -p {escaped}"
    )

    async def _run():
        try:
            summary = await stream_claude(ssh_cmd, reply_to, ts)
            ts.status = "done"
            await api.send_message(summary, reply_to=reply_to)
            await db.update_task(task_id, "done", summary=summary)
        except asyncio.CancelledError:
            ts.status = "cancelled"
            await api.send_message(f"Task `{task_id}` cancelled.", reply_to=reply_to)
            await db.update_task(task_id, "cancelled", summary="Cancelled")
        except Exception as exc:
            ts.status = "failed"
            logger.error("Code task %s failed: %s", task_id, exc)
            await api.send_message(f"Task `{task_id}` failed: {exc}", reply_to=reply_to)
            await db.update_task(task_id, "failed", summary=str(exc))
        finally:
            _active_tasks.pop(task_id, None)

    task = asyncio.create_task(_run())
    ts.asyncio_task = task
```

```python
# docker/mcp/tools/telegram/handlers/create.py
"""Project scaffolding handler — SSH → Claude Code."""
from __future__ import annotations

import asyncio
import logging
import re
import shlex
import uuid

from tools.telegram import api, config, db
from tools.telegram.handlers.status import _active_tasks
from tools.telegram.models import Intent, TaskState
from tools.telegram.stream import stream_claude

logger = logging.getLogger(__name__)

_VALID_NAME = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$")


async def handle_create(
    intent: Intent,
    chat_id: int,
    reply_to: int | None = None,
) -> None:
    """Spawn a background Claude Code task to scaffold a new project."""
    name = intent.name
    if not name or not _VALID_NAME.match(name):
        await api.send_message(f"Invalid project name: `{name}`", reply_to=reply_to)
        return

    task_id = uuid.uuid4().hex[:8]
    desc = f"Create {intent.language} project: {intent.description}"
    ts = TaskState(task_id=task_id, description=desc, repo=name)
    _active_tasks[task_id] = ts

    await db.save_task(task_id, chat_id, "create", desc, name)
    await api.send_message(
        f"Creating project: {name} ({intent.language})", reply_to=reply_to,
    )

    prompt = (
        f"Create a new {intent.language} project: {intent.description}. "
        f"Set up project structure, CLAUDE.md (inheriting from ~/.claude/CLAUDE.md conventions), "
        f"CI workflow for GitHub Actions (self-hosted runners, no attest-build-provenance, no macOS targets), "
        f"README, and initial source files. "
        f"Initialize git and make the first commit."
    )
    escaped = shlex.quote(prompt)
    ssh_cmd = (
        f'mkdir -p "$HOME/git/{name}" && cd "$HOME/git/{name}" && git init 2>/dev/null; '
        f"claude --output-format stream-json --dangerously-skip-permissions -p {escaped}"
    )

    async def _run():
        try:
            summary = await stream_claude(ssh_cmd, reply_to, ts)
            ts.status = "done"
            await api.send_message(summary, reply_to=reply_to)
            await db.update_task(task_id, "done", summary=summary)
        except asyncio.CancelledError:
            ts.status = "cancelled"
            await api.send_message(f"Task `{task_id}` cancelled.", reply_to=reply_to)
            await db.update_task(task_id, "cancelled", summary="Cancelled")
        except Exception as exc:
            ts.status = "failed"
            logger.error("Create task %s failed: %s", task_id, exc)
            await api.send_message(f"Task `{task_id}` failed: {exc}", reply_to=reply_to)
            await db.update_task(task_id, "failed", summary=str(exc))
        finally:
            _active_tasks.pop(task_id, None)

    task = asyncio.create_task(_run())
    ts.asyncio_task = task
```

- [ ] **Step 8: Run handler tests**

Run: `cd ~/git/aichat && python -m pytest tests/tools/telegram/unit/test_handlers.py -v`
Expected: PASS (8 tests)

- [ ] **Step 9: Commit**

```bash
cd ~/git/aichat
git add docker/mcp/tools/telegram/handlers/ tests/tools/telegram/unit/test_handlers.py
git commit -m "feat(telegram): add all intent handlers"
```

---

## Task 7: dispatcher.py + poller.py — Wiring It All Together

**Files:**
- Create: `docker/mcp/tools/telegram/dispatcher.py`
- Create: `docker/mcp/tools/telegram/poller.py`
- Create: `tests/tools/telegram/unit/test_dispatcher.py`
- Create: `tests/tools/telegram/unit/test_poller.py`

- [ ] **Step 1: Write dispatcher tests**

```python
# tests/tools/telegram/unit/test_dispatcher.py
"""Tests for telegram.dispatcher — intent routing."""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch

import pytest

from tools.telegram.models import Intent

ENV = {"TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_CHAT_ID": "1", "POSTGRES_PASSWORD": "pw"}


@pytest.mark.asyncio
class TestDispatcher:
    async def test_routes_tool_intent(self):
        with patch.dict(os.environ, ENV, clear=False):
            from tools.telegram import config
            import importlib
            importlib.reload(config)
            from tools.telegram.dispatcher import dispatch
            with patch("tools.telegram.dispatcher.handle_tool", new_callable=AsyncMock) as mock:
                await dispatch(Intent(type="tool", tool="monitor", action="thermals"), chat_id=1, reply_to=1)
        mock.assert_awaited_once()

    async def test_routes_question_intent(self):
        with patch.dict(os.environ, ENV, clear=False):
            from tools.telegram import config
            import importlib
            importlib.reload(config)
            from tools.telegram.dispatcher import dispatch
            with patch("tools.telegram.dispatcher.handle_question", new_callable=AsyncMock) as mock:
                await dispatch(Intent(type="question", text="hi"), chat_id=1, reply_to=1)
        mock.assert_awaited_once()

    async def test_routes_status(self):
        with patch.dict(os.environ, ENV, clear=False):
            from tools.telegram import config
            import importlib
            importlib.reload(config)
            from tools.telegram.dispatcher import dispatch
            with patch("tools.telegram.dispatcher.handle_status", new_callable=AsyncMock) as mock:
                await dispatch(Intent(type="status"), chat_id=1, reply_to=1)
        mock.assert_awaited_once()

    async def test_routes_cancel(self):
        with patch.dict(os.environ, ENV, clear=False):
            from tools.telegram import config
            import importlib
            importlib.reload(config)
            from tools.telegram.dispatcher import dispatch
            with patch("tools.telegram.dispatcher.handle_cancel", new_callable=AsyncMock) as mock:
                await dispatch(Intent(type="cancel"), chat_id=1, reply_to=1)
        mock.assert_awaited_once()

    async def test_routes_code_intent(self):
        with patch.dict(os.environ, ENV, clear=False):
            from tools.telegram import config
            import importlib
            importlib.reload(config)
            from tools.telegram.dispatcher import dispatch
            with patch("tools.telegram.dispatcher.handle_code", new_callable=AsyncMock) as mock:
                await dispatch(Intent(type="code", repo="aichat", task="fix"), chat_id=1, reply_to=1)
        mock.assert_awaited_once()
```

- [ ] **Step 2: Implement dispatcher.py**

```python
# docker/mcp/tools/telegram/dispatcher.py
"""Intent → handler routing."""
from __future__ import annotations

import logging

from tools.telegram import api
from tools.telegram.handlers.cancel import handle_cancel
from tools.telegram.handlers.code import handle_code
from tools.telegram.handlers.create import handle_create
from tools.telegram.handlers.question import handle_question
from tools.telegram.handlers.status import handle_status
from tools.telegram.handlers.tool import handle_tool
from tools.telegram.models import Intent

logger = logging.getLogger(__name__)


async def dispatch(
    intent: Intent,
    chat_id: int,
    reply_to: int | None = None,
) -> None:
    """Route an intent to its handler."""
    match intent.type:
        case "status":
            await handle_status(reply_to=reply_to)
        case "cancel":
            await handle_cancel(reply_to=reply_to)
        case "tool":
            await handle_tool(intent, reply_to=reply_to)
        case "code":
            await handle_code(intent, chat_id=chat_id, reply_to=reply_to)
        case "create":
            await handle_create(intent, chat_id=chat_id, reply_to=reply_to)
        case "question":
            await handle_question(intent, chat_id=chat_id, reply_to=reply_to)
        case _:
            logger.warning("Unknown intent type: %s", intent.type)
            await api.send_message(f"Unknown intent: {intent.type}", reply_to=reply_to)
```

- [ ] **Step 3: Write poller tests**

```python
# tests/tools/telegram/unit/test_poller.py
"""Tests for telegram.poller — poll loop, auth, message dispatch."""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch

import pytest

ENV = {"TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_CHAT_ID": "123", "POSTGRES_PASSWORD": "pw"}


@pytest.mark.asyncio
class TestPoller:
    async def test_auth_gate_blocks_unauthorized(self):
        with patch.dict(os.environ, ENV, clear=False):
            from tools.telegram import config
            import importlib
            importlib.reload(config)
            from tools.telegram.poller import _handle_message
            msg = {"chat": {"id": 99999}, "text": "hello", "message_id": 1}
            with patch("tools.telegram.poller.classifier.classify", new_callable=AsyncMock) as mock_classify:
                await _handle_message(msg)
            mock_classify.assert_not_awaited()

    async def test_dispatches_authorized_message(self):
        with patch.dict(os.environ, ENV, clear=False):
            from tools.telegram import config
            import importlib
            importlib.reload(config)
            from tools.telegram.poller import _handle_message
            from tools.telegram.models import Intent
            msg = {"chat": {"id": 123}, "text": "check thermals", "message_id": 1}
            with patch("tools.telegram.poller.classifier.classify", new_callable=AsyncMock, return_value=Intent(type="tool", tool="monitor", action="thermals")) as mock_classify, \
                 patch("tools.telegram.poller.dispatcher.dispatch", new_callable=AsyncMock) as mock_dispatch, \
                 patch("tools.telegram.poller.api.send_message", new_callable=AsyncMock), \
                 patch("tools.telegram.poller.db.save_message", new_callable=AsyncMock):
                await _handle_message(msg)
            mock_dispatch.assert_awaited_once()

    async def test_saves_user_message_to_db(self):
        with patch.dict(os.environ, ENV, clear=False):
            from tools.telegram import config
            import importlib
            importlib.reload(config)
            from tools.telegram.poller import _handle_message
            from tools.telegram.models import Intent
            msg = {"chat": {"id": 123}, "text": "hello", "message_id": 5}
            with patch("tools.telegram.poller.classifier.classify", new_callable=AsyncMock, return_value=Intent(type="question", text="hello")), \
                 patch("tools.telegram.poller.dispatcher.dispatch", new_callable=AsyncMock), \
                 patch("tools.telegram.poller.api.send_message", new_callable=AsyncMock), \
                 patch("tools.telegram.poller.db.save_message", new_callable=AsyncMock) as mock_save:
                await _handle_message(msg)
            mock_save.assert_awaited()
            args = mock_save.call_args[1] if mock_save.call_args[1] else dict(zip(["chat_id", "role", "content", "message_id"], mock_save.call_args[0]))
            assert args["chat_id"] == 123

    async def test_pending_code_repo_followup(self):
        with patch.dict(os.environ, ENV, clear=False):
            from tools.telegram import config
            import importlib
            importlib.reload(config)
            from tools.telegram.poller import _handle_message, _pending_code
            from tools.telegram.models import Intent
            # Set up a pending code intent missing repo
            _pending_code[123] = Intent(type="code", task="fix the bug")
            msg = {"chat": {"id": 123}, "text": "aichat", "message_id": 2}
            with patch("tools.telegram.poller.dispatcher.dispatch", new_callable=AsyncMock) as mock_dispatch, \
                 patch("tools.telegram.poller.api.send_message", new_callable=AsyncMock), \
                 patch("tools.telegram.poller.db.save_message", new_callable=AsyncMock):
                await _handle_message(msg)
            # Should have dispatched with repo filled in
            dispatched_intent = mock_dispatch.call_args[0][0]
            assert dispatched_intent.repo == "aichat"
            assert 123 not in _pending_code
```

- [ ] **Step 4: Implement poller.py**

```python
# docker/mcp/tools/telegram/poller.py
"""Poll loop — long-poll for messages, classify, dispatch."""
from __future__ import annotations

import asyncio
import logging

from tools.telegram import api, classifier, config, db, dispatcher
from tools.telegram.auth import is_authorized
from tools.telegram.models import Intent

logger = logging.getLogger(__name__)

_pending_code: dict[int, Intent] = {}
_background_tasks: set[asyncio.Task] = set()


async def _handle_message(message: dict) -> None:
    """Process a single inbound Telegram message."""
    if not is_authorized(message):
        return

    text = message.get("text", "")
    if not text:
        return

    chat_id = message["chat"]["id"]
    msg_id = message.get("message_id")

    # Save user message to conversation history
    await db.save_message(chat_id, "user", text, msg_id)

    # Check for pending "which repo?" follow-up
    if chat_id in _pending_code:
        intent = _pending_code.pop(chat_id)
        intent.repo = text.strip()
        await dispatcher.dispatch(intent, chat_id=chat_id, reply_to=msg_id)
        return

    # Classify
    intent = await classifier.classify(text)

    # Code intent without repo — ask for it
    if intent.type == "code" and not intent.repo:
        _pending_code[chat_id] = intent
        await api.send_message("Which repo?", reply_to=msg_id)
        return

    # For non-direct commands, send ack
    if intent.type not in ("status", "cancel"):
        await api.send_message("Got it — classifying...", reply_to=msg_id)

    await dispatcher.dispatch(intent, chat_id=chat_id, reply_to=msg_id)


async def poll_loop() -> None:
    """Main poll loop — runs as a background task from app.py lifespan."""
    if not config.is_configured():
        logger.warning("Telegram not configured (missing token or chat_id) — not starting")
        return

    # Init DB
    await db.init()
    await db.recover_stale_tasks()

    logger.info("Telegram poll_loop starting (%s)", config.summary())

    offset = 0
    try:
        while True:
            try:
                updates = await api.get_updates(offset)
                for update in updates:
                    offset = max(offset, update["update_id"] + 1)
                    msg = update.get("message")
                    if msg:
                        task = asyncio.create_task(_handle_message(msg))
                        _background_tasks.add(task)
                        task.add_done_callback(_background_tasks.discard)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("poll_loop error — retrying in 5s")
                await asyncio.sleep(5)
    except asyncio.CancelledError:
        logger.info("poll_loop cancelled — exiting")
    finally:
        await db.close()
```

- [ ] **Step 5: Run tests**

Run: `cd ~/git/aichat && python -m pytest tests/tools/telegram/unit/test_dispatcher.py tests/tools/telegram/unit/test_poller.py -v`
Expected: PASS (9 tests)

- [ ] **Step 6: Commit**

```bash
cd ~/git/aichat
git add docker/mcp/tools/telegram/dispatcher.py docker/mcp/tools/telegram/poller.py \
        tests/tools/telegram/unit/test_dispatcher.py tests/tools/telegram/unit/test_poller.py
git commit -m "feat(telegram): add dispatcher and poll loop"
```

---

## Task 8: Wire Up — __init__.py, app.py, docker-compose.yml, cleanup

**Files:**
- Modify: `docker/mcp/tools/telegram/__init__.py`
- Modify: `docker/mcp/app.py:36`
- Modify: `docker-compose.yml` (add POSTGRES_PASSWORD to aichat-mcp)
- Delete: `docker/mcp/tools/telegram_bot.py`
- Delete: `tests/tools/test_telegram_bot.py`

- [ ] **Step 1: Update telegram/__init__.py to export poll_loop**

```python
# docker/mcp/tools/telegram/__init__.py
"""Telegram bot package — modular command handler with hybrid classification."""
from tools.telegram.poller import poll_loop

__all__ = ["poll_loop"]
```

- [ ] **Step 2: Update app.py import**

In `docker/mcp/app.py`, change line 36 from:
```python
from tools.telegram_bot import poll_loop as _telegram_poll_loop  # noqa: E402
```
to:
```python
from tools.telegram import poll_loop as _telegram_poll_loop  # noqa: E402
```

- [ ] **Step 3: Add POSTGRES_PASSWORD to aichat-mcp in docker-compose.yml**

In the `aichat-mcp` service environment section, add:
```yaml
      POSTGRES_PASSWORD: "${POSTGRES_PASSWORD:?POSTGRES_PASSWORD must be set in .env}"
```

- [ ] **Step 4: Delete old monolith**

```bash
cd ~/git/aichat
rm docker/mcp/tools/telegram_bot.py
rm tests/tools/test_telegram_bot.py
```

- [ ] **Step 5: Run all unit tests**

Run: `cd ~/git/aichat && python -m pytest tests/tools/telegram/unit/ -v`
Expected: PASS (all ~60 tests)

- [ ] **Step 6: Commit**

```bash
cd ~/git/aichat
git add -A
git commit -m "feat(telegram): wire up modular package, remove monolith

BREAKING: tools.telegram_bot replaced by tools.telegram package.
Import path: from tools.telegram import poll_loop"
```

---

## Task 9: Contract Tests

**Files:**
- Create: `tests/tools/telegram/fixtures/telegram_responses.json`
- Create: `tests/tools/telegram/fixtures/lmstudio_responses.json`
- Create: `tests/tools/telegram/fixtures/claude_stream.jsonl`
- Create: `tests/tools/telegram/contract/__init__.py`
- Create: `tests/tools/telegram/contract/test_telegram_contract.py`
- Create: `tests/tools/telegram/contract/test_lmstudio_contract.py`
- Create: `tests/tools/telegram/contract/test_claude_stream_contract.py`

- [ ] **Step 1: Create fixtures**

```json
// tests/tools/telegram/fixtures/telegram_responses.json
{
  "sendMessage_success": {
    "ok": true,
    "result": {
      "message_id": 42,
      "from": {"id": 123456789, "is_bot": true, "first_name": "TestBot"},
      "chat": {"id": 7274294368, "type": "private"},
      "date": 1712700000,
      "text": "Hello"
    }
  },
  "getUpdates_success": {
    "ok": true,
    "result": [
      {
        "update_id": 100,
        "message": {
          "message_id": 50,
          "from": {"id": 7274294368, "is_bot": false, "first_name": "Jamal"},
          "chat": {"id": 7274294368, "type": "private"},
          "date": 1712700001,
          "text": "check thermals"
        }
      }
    ]
  },
  "rate_limit_429": {
    "ok": false,
    "error_code": 429,
    "description": "Too Many Requests: retry after 5",
    "parameters": {"retry_after": 5}
  }
}
```

```json
// tests/tools/telegram/fixtures/lmstudio_responses.json
{
  "chat_completion_success": {
    "id": "chatcmpl-abc123",
    "object": "chat.completion",
    "created": 1712700000,
    "model": "gemma-4-e2b-it",
    "choices": [
      {
        "index": 0,
        "message": {
          "role": "assistant",
          "content": "{\"type\": \"tool\", \"tool\": \"monitor\", \"action\": \"thermals\"}"
        },
        "finish_reason": "stop"
      }
    ],
    "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}
  },
  "chat_completion_error": {
    "error": {
      "message": "Model not loaded",
      "type": "invalid_request_error",
      "code": "model_not_found"
    }
  }
}
```

```jsonl
{"type":"tool_use","tool":"Read","tool_input":{"file_path":"/app/main.py"}}
{"type":"tool_result","content":[{"type":"text","text":"file contents here"}]}
{"type":"tool_use","tool":"Edit","tool_input":{"file_path":"/app/main.py","old_string":"old","new_string":"new"}}
{"type":"tool_use","tool":"Bash","tool_input":{"command":"pytest tests/ -v"}}
{"type":"tool_result","content":[{"type":"text","text":"3 passed"}]}
{"type":"tool_use","tool":"Bash","tool_input":{"command":"git commit -m 'fix: stuff'"}}
{"type":"tool_result","content":[{"type":"text","text":"[main abc1234] fix: stuff\n 1 file changed"}]}
{"type":"assistant","content":"I've fixed the issue in main.py."}
{"type":"result","result":"Task completed successfully."}
```

- [ ] **Step 2: Write contract tests**

```python
# tests/tools/telegram/contract/__init__.py
```

```python
# tests/tools/telegram/contract/test_telegram_contract.py
"""Contract tests — validate Telegram API response shapes against fixtures."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent.parent / "fixtures" / "telegram_responses.json"


@pytest.fixture
def fixtures():
    return json.loads(FIXTURES.read_text())


class TestTelegramContract:
    def test_send_message_has_required_fields(self, fixtures):
        data = fixtures["sendMessage_success"]
        assert data["ok"] is True
        result = data["result"]
        assert "message_id" in result
        assert "chat" in result
        assert "id" in result["chat"]

    def test_get_updates_has_required_fields(self, fixtures):
        data = fixtures["getUpdates_success"]
        assert data["ok"] is True
        updates = data["result"]
        assert len(updates) > 0
        update = updates[0]
        assert "update_id" in update
        assert "message" in update
        msg = update["message"]
        assert "message_id" in msg
        assert "chat" in msg
        assert "text" in msg

    def test_rate_limit_has_retry_after(self, fixtures):
        data = fixtures["rate_limit_429"]
        assert data["ok"] is False
        assert "parameters" in data
        assert "retry_after" in data["parameters"]
        assert isinstance(data["parameters"]["retry_after"], int)
```

```python
# tests/tools/telegram/contract/test_lmstudio_contract.py
"""Contract tests — validate LM Studio response shapes against fixtures."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent.parent / "fixtures" / "lmstudio_responses.json"


@pytest.fixture
def fixtures():
    return json.loads(FIXTURES.read_text())


class TestLMStudioContract:
    def test_chat_completion_has_choices(self, fixtures):
        data = fixtures["chat_completion_success"]
        assert "choices" in data
        assert len(data["choices"]) > 0
        choice = data["choices"][0]
        assert "message" in choice
        assert "content" in choice["message"]
        assert "role" in choice["message"]

    def test_error_response_has_message(self, fixtures):
        data = fixtures["chat_completion_error"]
        assert "error" in data
        assert "message" in data["error"]
```

```python
# tests/tools/telegram/contract/test_claude_stream_contract.py
"""Contract tests — validate Claude stream-json event shapes against fixtures."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent.parent / "fixtures" / "claude_stream.jsonl"


@pytest.fixture
def events():
    lines = FIXTURES.read_text().strip().split("\n")
    return [json.loads(line) for line in lines]


class TestClaudeStreamContract:
    def test_tool_use_event_shape(self, events):
        tool_uses = [e for e in events if e.get("type") == "tool_use"]
        assert len(tool_uses) > 0
        for event in tool_uses:
            assert "tool" in event
            assert "tool_input" in event
            assert isinstance(event["tool_input"], dict)

    def test_tool_result_event_shape(self, events):
        results = [e for e in events if e.get("type") == "tool_result"]
        assert len(results) > 0
        for event in results:
            assert "content" in event
            assert isinstance(event["content"], list)
            for block in event["content"]:
                assert "type" in block
                assert "text" in block

    def test_result_event_shape(self, events):
        finals = [e for e in events if e.get("type") == "result"]
        assert len(finals) > 0
        for event in finals:
            assert "result" in event
```

- [ ] **Step 3: Run contract tests**

Run: `cd ~/git/aichat && python -m pytest tests/tools/telegram/contract/ -v`
Expected: PASS (8 tests)

- [ ] **Step 4: Commit**

```bash
cd ~/git/aichat
git add tests/tools/telegram/fixtures/ tests/tools/telegram/contract/
git commit -m "test(telegram): add contract tests with recorded API fixtures"
```

---

## Task 10: Integration Tests

**Files:**
- Create: `tests/tools/telegram/integration/__init__.py`
- Create: `tests/tools/telegram/conftest.py`
- Create: `tests/tools/telegram/integration/test_full_pipeline.py`
- Create: `tests/tools/telegram/integration/test_db_persistence.py`
- Create: `tests/tools/telegram/integration/test_task_lifecycle.py`

- [ ] **Step 1: Create shared conftest**

```python
# tests/tools/telegram/conftest.py
"""Shared fixtures for telegram tests — fake servers, DB helpers."""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch

import pytest

TELEGRAM_ENV = {
    "TELEGRAM_BOT_TOKEN": "test-token",
    "TELEGRAM_CHAT_ID": "123",
    "IMAGE_GEN_BASE_URL": "http://localhost:9999",
    "POSTGRES_PASSWORD": "testpw",
    "TEAM_SSH_HOST": "localhost",
    "TEAM_SSH_PORT": "22",
    "TEAM_SSH_USER": "test",
    "TEAM_SSH_KEY": "/dev/null",
}


@pytest.fixture
def telegram_env():
    """Patch environment with test Telegram config."""
    with patch.dict(os.environ, TELEGRAM_ENV, clear=False):
        from tools.telegram import config
        import importlib
        importlib.reload(config)
        yield TELEGRAM_ENV


@pytest.fixture
def mock_send():
    """Mock api.send_message."""
    with patch("tools.telegram.api.send_message", new_callable=AsyncMock) as mock:
        yield mock


@pytest.fixture
def mock_db():
    """Mock all db operations."""
    with patch("tools.telegram.db.save_message", new_callable=AsyncMock) as save_msg, \
         patch("tools.telegram.db.get_history", new_callable=AsyncMock, return_value=[]) as get_hist, \
         patch("tools.telegram.db.save_task", new_callable=AsyncMock) as save_task, \
         patch("tools.telegram.db.update_task", new_callable=AsyncMock) as update_task:
        yield {"save_message": save_msg, "get_history": get_hist,
               "save_task": save_task, "update_task": update_task}
```

```python
# tests/tools/telegram/integration/__init__.py
```

- [ ] **Step 2: Write integration tests**

```python
# tests/tools/telegram/integration/test_full_pipeline.py
"""Integration tests — full message → classify → dispatch → response pipeline."""
from __future__ import annotations

import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

TELEGRAM_ENV = {
    "TELEGRAM_BOT_TOKEN": "test-token",
    "TELEGRAM_CHAT_ID": "123",
    "IMAGE_GEN_BASE_URL": "http://localhost:9999",
    "POSTGRES_PASSWORD": "testpw",
}


@pytest.mark.asyncio
class TestFullPipeline:
    async def test_regex_fast_path_check_thermals(self):
        """'check thermals' → regex → monitor:thermals → tool handler → response."""
        handler_fn = AsyncMock(return_value=[{"type": "text", "text": "CPU: 42C GPU: 38C"}])

        with patch.dict(os.environ, TELEGRAM_ENV, clear=False):
            from tools.telegram import config
            import importlib
            importlib.reload(config)
            from tools.telegram.poller import _handle_message
            msg = {"chat": {"id": 123}, "text": "check thermals", "message_id": 1}
            with patch("tools.telegram.handlers.tool.TOOL_HANDLERS", {"monitor": handler_fn}), \
                 patch("tools.telegram.api.send_message", new_callable=AsyncMock) as mock_send, \
                 patch("tools.telegram.db.save_message", new_callable=AsyncMock):
                await _handle_message(msg)

        handler_fn.assert_awaited_once()
        all_texts = [c[0][0] for c in mock_send.call_args_list]
        assert any("42C" in t for t in all_texts)

    async def test_llm_slow_path_ambiguous(self):
        """Ambiguous message → LLM → question handler → Gemma response."""
        lm_classify = MagicMock()
        lm_classify.status_code = 200
        lm_classify.json.return_value = {
            "choices": [{"message": {"content": '{"type":"question","text":"what is up"}'}}]
        }
        lm_answer = MagicMock()
        lm_answer.status_code = 200
        lm_answer.json.return_value = {
            "choices": [{"message": {"content": "Everything looks good."}}]
        }

        client = AsyncMock()
        client.post = AsyncMock(side_effect=[lm_classify, lm_answer])
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)

        with patch.dict(os.environ, TELEGRAM_ENV, clear=False):
            from tools.telegram import config
            import importlib
            importlib.reload(config)
            from tools.telegram.poller import _handle_message
            msg = {"chat": {"id": 123}, "text": "what is going on around here", "message_id": 2}
            with patch("tools.telegram.classifier.httpx.AsyncClient", return_value=client), \
                 patch("tools.telegram.handlers.question.httpx.AsyncClient", return_value=client), \
                 patch("tools.telegram.api.send_message", new_callable=AsyncMock) as mock_send, \
                 patch("tools.telegram.db.save_message", new_callable=AsyncMock), \
                 patch("tools.telegram.db.get_history", new_callable=AsyncMock, return_value=[]):
                await _handle_message(msg)

        all_texts = [c[0][0] for c in mock_send.call_args_list]
        assert any("looks good" in t.lower() for t in all_texts)

    async def test_status_with_no_tasks(self):
        """'status' → regex → status handler → 'No active tasks.'"""
        with patch.dict(os.environ, TELEGRAM_ENV, clear=False):
            from tools.telegram import config
            import importlib
            importlib.reload(config)
            from tools.telegram.poller import _handle_message
            from tools.telegram.handlers.status import _active_tasks
            _active_tasks.clear()
            msg = {"chat": {"id": 123}, "text": "status", "message_id": 3}
            with patch("tools.telegram.api.send_message", new_callable=AsyncMock) as mock_send, \
                 patch("tools.telegram.db.save_message", new_callable=AsyncMock):
                await _handle_message(msg)

        all_texts = [c[0][0] for c in mock_send.call_args_list]
        assert any("No active tasks" in t for t in all_texts)

    async def test_unauthorized_message_ignored(self):
        """Message from wrong chat_id → silently ignored."""
        with patch.dict(os.environ, TELEGRAM_ENV, clear=False):
            from tools.telegram import config
            import importlib
            importlib.reload(config)
            from tools.telegram.poller import _handle_message
            msg = {"chat": {"id": 99999}, "text": "check thermals", "message_id": 4}
            with patch("tools.telegram.api.send_message", new_callable=AsyncMock) as mock_send, \
                 patch("tools.telegram.db.save_message", new_callable=AsyncMock) as mock_db:
                await _handle_message(msg)

        mock_send.assert_not_awaited()
        mock_db.assert_not_awaited()

    async def test_lm_studio_down_regex_still_works(self):
        """LM Studio unreachable → regex patterns still match."""
        handler_fn = AsyncMock(return_value=[{"type": "text", "text": "overview data"}])

        with patch.dict(os.environ, TELEGRAM_ENV, clear=False):
            from tools.telegram import config
            import importlib
            importlib.reload(config)
            from tools.telegram.poller import _handle_message
            msg = {"chat": {"id": 123}, "text": "show containers", "message_id": 5}
            with patch("tools.telegram.handlers.tool.TOOL_HANDLERS", {"monitor": handler_fn}), \
                 patch("tools.telegram.api.send_message", new_callable=AsyncMock) as mock_send, \
                 patch("tools.telegram.db.save_message", new_callable=AsyncMock):
                # No LM Studio mock needed — regex handles it
                await _handle_message(msg)

        handler_fn.assert_awaited_once()
```

```python
# tests/tools/telegram/integration/test_db_persistence.py
"""Integration tests — DB persistence (mocked asyncpg pool)."""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch

import pytest

TELEGRAM_ENV = {
    "TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_CHAT_ID": "123", "POSTGRES_PASSWORD": "pw",
}


@pytest.mark.asyncio
class TestDbPersistence:
    async def test_conversation_saved_and_retrieved(self):
        """Messages saved via save_message are retrievable via get_history."""
        mock_pool = AsyncMock()
        stored = []

        async def fake_execute(sql, *args):
            if "INSERT INTO telegram_messages" in sql:
                stored.append({"role": args[1], "content": args[2], "created_at": "now"})

        async def fake_fetch(sql, *args):
            return list(reversed(stored[-args[1]:]))

        mock_pool.execute = fake_execute
        mock_pool.fetch = fake_fetch

        with patch("tools.telegram.db._pool", mock_pool):
            from tools.telegram.db import get_history, save_message
            await save_message(123, "user", "hello")
            await save_message(123, "assistant", "hi there")
            history = await get_history(123, limit=10)

        assert len(history) == 2
        assert history[0]["role"] == "user"
        assert history[1]["role"] == "assistant"

    async def test_db_down_graceful_degradation(self):
        """Bot continues working when Postgres is unreachable."""
        mock_pool = AsyncMock()
        mock_pool.execute = AsyncMock(side_effect=Exception("connection refused"))
        mock_pool.fetch = AsyncMock(side_effect=Exception("connection refused"))

        with patch("tools.telegram.db._pool", mock_pool):
            from tools.telegram.db import get_history, save_message
            # Should not raise
            await save_message(123, "user", "hello")
            history = await get_history(123)
            assert history == []
```

```python
# tests/tools/telegram/integration/test_task_lifecycle.py
"""Integration tests — task lifecycle (create → stream → complete/fail)."""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tools.telegram.models import Intent, TaskState

TELEGRAM_ENV = {
    "TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_CHAT_ID": "123", "POSTGRES_PASSWORD": "pw",
}


@pytest.mark.asyncio
class TestTaskLifecycle:
    async def test_code_task_invalid_repo_rejected(self):
        with patch.dict(os.environ, TELEGRAM_ENV, clear=False):
            from tools.telegram import config
            import importlib
            importlib.reload(config)
            from tools.telegram.handlers.code import handle_code
            with patch("tools.telegram.handlers.code.api.send_message", new_callable=AsyncMock) as mock_send, \
                 patch("tools.telegram.handlers.code.db.save_task", new_callable=AsyncMock) as mock_db:
                intent = Intent(type="code", repo="../../etc/passwd", task="hack")
                await handle_code(intent, chat_id=123, reply_to=1)

        assert any("Invalid" in c[0][0] for c in mock_send.call_args_list)
        mock_db.assert_not_awaited()

    async def test_startup_recovery_marks_stale_tasks(self):
        mock_pool = AsyncMock()
        mock_pool.execute = AsyncMock()

        with patch("tools.telegram.db._pool", mock_pool):
            from tools.telegram.db import recover_stale_tasks
            await recover_stale_tasks()

        call_args = mock_pool.execute.call_args[0][0]
        assert "failed" in call_args
        assert "running" in call_args

    async def test_cancel_removes_from_active(self):
        from tools.telegram.handlers.status import _active_tasks

        ts = TaskState(task_id="test123", description="test task")
        ts.process = MagicMock()
        ts.process.kill = MagicMock()
        ts.asyncio_task = MagicMock()
        ts.asyncio_task.cancel = MagicMock()
        _active_tasks["test123"] = ts

        with patch.dict(os.environ, TELEGRAM_ENV, clear=False):
            from tools.telegram import config
            import importlib
            importlib.reload(config)
            from tools.telegram.handlers.cancel import handle_cancel
            with patch("tools.telegram.handlers.cancel.api.send_message", new_callable=AsyncMock), \
                 patch("tools.telegram.handlers.cancel.db.update_task", new_callable=AsyncMock):
                await handle_cancel(reply_to=1)

        assert "test123" not in _active_tasks
        ts.process.kill.assert_called_once()
```

- [ ] **Step 3: Run integration tests**

Run: `cd ~/git/aichat && python -m pytest tests/tools/telegram/integration/ -v`
Expected: PASS (10 tests)

- [ ] **Step 4: Commit**

```bash
cd ~/git/aichat
git add tests/tools/telegram/conftest.py tests/tools/telegram/integration/
git commit -m "test(telegram): add integration tests for full pipeline, DB, and task lifecycle"
```

---

## Task 11: Final Validation

- [ ] **Step 1: Run the complete test suite**

Run: `cd ~/git/aichat && python -m pytest tests/tools/telegram/ -v --tb=short`
Expected: PASS — all ~78 tests (unit + integration + contract)

- [ ] **Step 2: Verify no import errors in the package**

Run: `cd ~/git/aichat && python -c "from tools.telegram import poll_loop; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Verify old monolith is gone**

Run: `cd ~/git/aichat && test ! -f docker/mcp/tools/telegram_bot.py && echo "GONE" || echo "STILL EXISTS"`
Expected: `GONE`

- [ ] **Step 4: Run existing aichat tests to check nothing broke**

Run: `cd ~/git/aichat && python -m pytest tests/ -v --ignore=tests/tools/telegram/ --tb=short -q`
Expected: PASS (no regressions)

- [ ] **Step 5: Build the Docker image**

Run: `cd ~/git/aichat && docker compose build aichat-mcp`
Expected: Build succeeds

- [ ] **Step 6: Restart the service**

Run: `cd ~/git/aichat && docker compose up -d aichat-mcp`
Expected: Container starts, health check passes

- [ ] **Step 7: Verify Telegram poll loop started**

Run: `docker logs aichat-aichat-mcp-1 2>&1 | grep -i "telegram poll_loop"`
Expected: `Telegram poll_loop starting (bot_token=*** chat_id=7274294368 ...)`

- [ ] **Step 8: Final commit**

```bash
cd ~/git/aichat
git add -A
git commit -m "chore(telegram): final validation pass — all tests green, service healthy"
```
