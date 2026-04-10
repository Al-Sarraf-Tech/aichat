"""Tests for telegram.db — Postgres operations (mocked asyncpg)."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

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
        # Mock rows in DESC order (newest first) as the DB query returns them;
        # get_history reverses them back to chronological order before returning.
        rows = [
            {"role": "assistant", "content": "hello", "created_at": "2026-04-10T00:00:01"},
            {"role": "user", "content": "hi", "created_at": "2026-04-10T00:00:00"},
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
        assert "running" in args[0]

    async def test_graceful_degradation_on_failure(self):
        mock_pool = AsyncMock()
        mock_pool.execute = AsyncMock(side_effect=Exception("connection refused"))

        with patch("tools.telegram.db._pool", mock_pool):
            from tools.telegram.db import save_message
            await save_message(chat_id=123, role="user", content="hello")

    async def test_no_pool_graceful(self):
        with patch("tools.telegram.db._pool", None):
            from tools.telegram.db import save_message
            await save_message(chat_id=123, role="user", content="hello")

    async def test_init_creates_pool_and_migrates(self):
        mock_pool = AsyncMock()
        mock_pool.execute = AsyncMock()

        with patch("tools.telegram.db.asyncpg.create_pool", new_callable=AsyncMock, return_value=mock_pool) as mock_create:
            from tools.telegram.db import init
            await init(dsn="postgresql://test:test@localhost/test")

        mock_create.assert_awaited_once()
        assert mock_pool.execute.await_count >= 2
