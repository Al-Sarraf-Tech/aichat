"""Integration tests — task lifecycle (create -> stream -> complete/fail)."""
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
