"""
Unit tests for tools/telegram_bot.py — Telegram command handler.

Test groups:
  - Telegram helpers (4): send success, truncation, getUpdates success, getUpdates error
  - Classifier (6): tool/code/create/question intents, malformed JSON fallback, network error fallback
  - Auth + poll loop (3): authorized correct ID, unauthorized wrong ID, handle_message dispatches tool

Run with:
  cd ~/git/aichat
  python -m pytest tests/tools/test_telegram_bot.py -v
"""
from __future__ import annotations

import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

ENV_VARS = {
    "TELEGRAM_BOT_TOKEN": "fake-token",
    "TELEGRAM_CHAT_ID": "123456",
    "IMAGE_GEN_BASE_URL": "http://192.168.50.2:1234",
}


def _make_mock_client(post_status: int = 200, post_json: dict | None = None,
                      get_status: int = 200, get_json: dict | None = None) -> AsyncMock:
    """Return an AsyncMock httpx.AsyncClient with pre-configured post/get responses."""
    post_resp = MagicMock()
    post_resp.status_code = post_status
    post_resp.json.return_value = post_json if post_json is not None else {"ok": True, "result": {"message_id": 7}}

    get_resp = MagicMock()
    get_resp.status_code = get_status
    get_resp.json.return_value = get_json if get_json is not None else {"ok": True, "result": []}

    client = AsyncMock()
    client.post = AsyncMock(return_value=post_resp)
    client.get = AsyncMock(return_value=get_resp)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client


# ===========================================================================
# Task 1: Telegram helpers — 4 tests
# ===========================================================================


class TestSendTelegram:
    """_send_telegram helper."""

    @pytest.mark.asyncio
    async def test_send_telegram_posts_correct_payload(self):
        """_send_telegram must POST to /sendMessage with chat_id, text, parse_mode."""
        with patch.dict(os.environ, ENV_VARS):
            import importlib
            import tools.telegram_bot as tb
            importlib.reload(tb)

        client = _make_mock_client()
        with patch.dict(os.environ, ENV_VARS), \
             patch("tools.telegram_bot.httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__ = AsyncMock(return_value=client)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            await tb._send_telegram("hello world")

        call_args = client.post.call_args
        assert call_args is not None
        url_arg = call_args[0][0] if call_args[0] else ""
        assert "sendMessage" in url_arg
        json_payload = call_args.kwargs.get("json", {}) or (call_args[0][1] if len(call_args[0]) > 1 else {})
        assert json_payload.get("text") == "hello world"
        assert json_payload.get("parse_mode") == "Markdown"
        assert str(json_payload.get("chat_id")) == "123456"

    @pytest.mark.asyncio
    async def test_send_telegram_truncates_long_text(self):
        """_send_telegram must truncate text exceeding 4096 chars."""
        with patch.dict(os.environ, ENV_VARS):
            import importlib
            import tools.telegram_bot as tb
            importlib.reload(tb)

        long_text = "x" * 5000
        client = _make_mock_client()
        with patch.dict(os.environ, ENV_VARS), \
             patch("tools.telegram_bot.httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__ = AsyncMock(return_value=client)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            await tb._send_telegram(long_text)

        call_args = client.post.call_args
        json_payload = call_args.kwargs.get("json", {})
        sent_text = json_payload.get("text", "")
        assert len(sent_text) <= 4096
        assert "truncated" in sent_text.lower()


class TestGetUpdates:
    """_get_updates helper."""

    @pytest.mark.asyncio
    async def test_get_updates_returns_results(self):
        """_get_updates must return the result list from Telegram on success."""
        with patch.dict(os.environ, ENV_VARS):
            import importlib
            import tools.telegram_bot as tb
            importlib.reload(tb)

        updates_payload = {
            "ok": True,
            "result": [
                {"update_id": 1, "message": {"message_id": 10, "text": "hi", "chat": {"id": 123456}}}
            ],
        }
        client = _make_mock_client(get_json=updates_payload)
        with patch.dict(os.environ, ENV_VARS), \
             patch("tools.telegram_bot.httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__ = AsyncMock(return_value=client)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await tb._get_updates(0)

        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["update_id"] == 1

    @pytest.mark.asyncio
    async def test_get_updates_returns_empty_on_error(self):
        """_get_updates must return [] when the request raises an exception."""
        with patch.dict(os.environ, ENV_VARS):
            import importlib
            import tools.telegram_bot as tb
            importlib.reload(tb)

        with patch.dict(os.environ, ENV_VARS), \
             patch("tools.telegram_bot.httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__ = AsyncMock(side_effect=Exception("network down"))
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await tb._get_updates(0)

        assert result == []
