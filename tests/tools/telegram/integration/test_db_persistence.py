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
        mock_pool = AsyncMock()
        mock_pool.execute = AsyncMock(side_effect=Exception("connection refused"))
        mock_pool.fetch = AsyncMock(side_effect=Exception("connection refused"))

        with patch("tools.telegram.db._pool", mock_pool):
            from tools.telegram.db import get_history, save_message
            await save_message(123, "user", "hello")
            history = await get_history(123)
            assert history == []
