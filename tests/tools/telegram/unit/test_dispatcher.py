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
