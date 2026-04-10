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
            with patch("tools.telegram.poller.classifier.classify", new_callable=AsyncMock, return_value=Intent(type="tool", tool="monitor", action="thermals")), \
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
            args = mock_save.call_args[0]
            assert args[0] == 123

    async def test_pending_code_repo_followup(self):
        with patch.dict(os.environ, ENV, clear=False):
            from tools.telegram import config
            import importlib
            importlib.reload(config)
            from tools.telegram.poller import _handle_message, _pending_code
            from tools.telegram.models import Intent
            _pending_code[123] = Intent(type="code", task="fix the bug")
            msg = {"chat": {"id": 123}, "text": "aichat", "message_id": 2}
            with patch("tools.telegram.poller.dispatcher.dispatch", new_callable=AsyncMock) as mock_dispatch, \
                 patch("tools.telegram.poller.api.send_message", new_callable=AsyncMock), \
                 patch("tools.telegram.poller.db.save_message", new_callable=AsyncMock):
                await _handle_message(msg)
            dispatched_intent = mock_dispatch.call_args[0][0]
            assert dispatched_intent.repo == "aichat"
            assert 123 not in _pending_code
