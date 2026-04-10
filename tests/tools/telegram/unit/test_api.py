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
