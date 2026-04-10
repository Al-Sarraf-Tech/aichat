"""Tests for telegram.handlers — all dispatchers."""
from __future__ import annotations

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
