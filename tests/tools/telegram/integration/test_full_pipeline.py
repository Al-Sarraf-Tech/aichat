"""Integration tests — full message -> classify -> dispatch -> response pipeline."""
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
                await _handle_message(msg)

        handler_fn.assert_awaited_once()
