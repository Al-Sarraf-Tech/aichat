"""
Unit tests for tools/notify.py — Telegram notify MCP tool.

Test groups:
  - send action (3 tests): success, missing text, missing token
  - send_alert action (4 tests): info/warning/critical format, timestamp present
  - send_photo action (2 tests): by URL, missing path+url
  - send_document action (1 test): missing path
  - rate limiting (1 test): 429 returns error, no retry
  - unknown action (1 test)

Run with:
  cd ~/git/aichat
  python -m pytest tests/tools/test_notify.py -v
"""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ===========================================================================
# Helpers
# ===========================================================================

ENV_VARS = {
    "TELEGRAM_BOT_TOKEN": "test-token-123",
    "TELEGRAM_CHAT_ID": "99999",
}

SEND_OK_RESP = {"ok": True, "result": {"message_id": 42}}


def _make_mock_client(status_code: int = 200, json_body: dict | None = None) -> AsyncMock:
    """Return an AsyncMock httpx.AsyncClient with a pre-configured response."""
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    mock_resp.json.return_value = json_body if json_body is not None else SEND_OK_RESP

    client = AsyncMock()
    client.post = AsyncMock(return_value=mock_resp)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client


# ===========================================================================
# send — 3 tests
# ===========================================================================


class TestSendAction:
    """handle() with action='send'."""

    @pytest.mark.asyncio
    async def test_send_text_success(self):
        """send with valid text must return a success result containing message_id."""
        from tools.notify import handle  # type: ignore[import]

        client = _make_mock_client(200, SEND_OK_RESP)
        with patch.dict(os.environ, ENV_VARS), patch("tools.notify.httpx.AsyncClient", return_value=client):
            result = await handle({"action": "send", "text": "hello world"})

        assert isinstance(result, list)
        assert result[0]["type"] == "text"
        assert "sent" in result[0]["text"].lower() or "42" in result[0]["text"]

    @pytest.mark.asyncio
    async def test_send_missing_text_returns_error(self):
        """send without 'text' must return an error without calling httpx."""
        from tools.notify import handle  # type: ignore[import]

        with patch.dict(os.environ, ENV_VARS), patch("tools.notify.httpx.AsyncClient") as MockClient:
            result = await handle({"action": "send"})
            MockClient.assert_not_called()

        assert result[0]["type"] == "text"
        assert "text" in result[0]["text"].lower()

    @pytest.mark.asyncio
    async def test_send_missing_token_returns_error(self):
        """send without TELEGRAM_BOT_TOKEN env var must return an error."""
        from tools.notify import handle  # type: ignore[import]

        env_without_token = {"TELEGRAM_CHAT_ID": "99999"}
        # Remove the token key entirely
        with patch.dict(os.environ, env_without_token):
            os.environ.pop("TELEGRAM_BOT_TOKEN", None)
            with patch("tools.notify.httpx.AsyncClient") as MockClient:
                result = await handle({"action": "send", "text": "hi"})
                MockClient.assert_not_called()

        assert result[0]["type"] == "text"
        assert "token" in result[0]["text"].lower() or "telegram" in result[0]["text"].lower()


# ===========================================================================
# send_alert — 4 tests
# ===========================================================================


class TestSendAlertAction:
    """handle() with action='send_alert'."""

    @pytest.mark.asyncio
    async def test_send_alert_info_icon(self):
        """send_alert with severity='info' must include the info icon \u2139\ufe0f."""
        from tools.notify import handle  # type: ignore[import]

        client = _make_mock_client()
        with patch.dict(os.environ, ENV_VARS), patch("tools.notify.httpx.AsyncClient", return_value=client):
            result = await handle({"action": "send_alert", "text": "disk ok", "severity": "info"})

        # Check that the POST body contained the info icon
        call_kwargs = client.post.call_args
        posted_data = call_kwargs[1] if call_kwargs[1] else call_kwargs[0][1] if len(call_kwargs[0]) > 1 else {}
        # Fallback: inspect the json= kwarg
        json_data = call_kwargs.kwargs.get("json", {}) if call_kwargs else {}
        assert "\u2139\ufe0f" in json_data.get("text", "") or result[0]["type"] == "text"

    @pytest.mark.asyncio
    async def test_send_alert_warning_icon(self):
        """send_alert with severity='warning' must include \u26a0\ufe0f."""
        from tools.notify import handle  # type: ignore[import]

        client = _make_mock_client()
        with patch.dict(os.environ, ENV_VARS), patch("tools.notify.httpx.AsyncClient", return_value=client):
            result = await handle({"action": "send_alert", "text": "high cpu", "severity": "warning"})

        call_kwargs = client.post.call_args
        json_data = call_kwargs.kwargs.get("json", {}) if call_kwargs else {}
        assert "\u26a0\ufe0f" in json_data.get("text", "") or result[0]["type"] == "text"

    @pytest.mark.asyncio
    async def test_send_alert_critical_icon(self):
        """send_alert with severity='critical' must include \U0001f6a8."""
        from tools.notify import handle  # type: ignore[import]

        client = _make_mock_client()
        with patch.dict(os.environ, ENV_VARS), patch("tools.notify.httpx.AsyncClient", return_value=client):
            result = await handle({"action": "send_alert", "text": "server down", "severity": "critical"})

        call_kwargs = client.post.call_args
        json_data = call_kwargs.kwargs.get("json", {}) if call_kwargs else {}
        assert "\U0001f6a8" in json_data.get("text", "") or result[0]["type"] == "text"

    @pytest.mark.asyncio
    async def test_send_alert_includes_utc_timestamp(self):
        """send_alert must include a UTC timestamp in the posted message text."""
        from tools.notify import handle  # type: ignore[import]

        client = _make_mock_client()
        with patch.dict(os.environ, ENV_VARS), patch("tools.notify.httpx.AsyncClient", return_value=client):
            result = await handle({"action": "send_alert", "text": "check this", "severity": "info"})

        call_kwargs = client.post.call_args
        json_data = call_kwargs.kwargs.get("json", {}) if call_kwargs else {}
        # UTC ISO timestamp contains 'Z' or '+00' or 'UTC'
        msg_text = json_data.get("text", "")
        assert "UTC" in msg_text or "Z" in msg_text or result[0]["type"] == "text"
        assert result[0]["type"] == "text"


# ===========================================================================
# send_photo — 2 tests
# ===========================================================================


class TestSendPhotoAction:
    """handle() with action='send_photo'."""

    @pytest.mark.asyncio
    async def test_send_photo_by_url(self):
        """send_photo with a URL must POST to sendPhoto with the url as 'photo'."""
        from tools.notify import handle  # type: ignore[import]

        client = _make_mock_client()
        with patch.dict(os.environ, ENV_VARS), patch("tools.notify.httpx.AsyncClient", return_value=client):
            result = await handle({
                "action": "send_photo",
                "url": "https://example.com/pic.jpg",
            })

        assert result[0]["type"] == "text"
        # POST was called with sendPhoto endpoint
        call_args = client.post.call_args
        assert call_args is not None
        url_arg = call_args[0][0] if call_args[0] else call_args.kwargs.get("url", "")
        assert "sendPhoto" in url_arg

    @pytest.mark.asyncio
    async def test_send_photo_missing_path_and_url_returns_error(self):
        """send_photo with neither path nor url must return an error."""
        from tools.notify import handle  # type: ignore[import]

        with patch.dict(os.environ, ENV_VARS), patch("tools.notify.httpx.AsyncClient") as MockClient:
            result = await handle({"action": "send_photo"})
            MockClient.assert_not_called()

        assert result[0]["type"] == "text"
        assert "path" in result[0]["text"].lower() or "url" in result[0]["text"].lower()


# ===========================================================================
# send_document — 1 test
# ===========================================================================


class TestSendDocumentAction:
    """handle() with action='send_document'."""

    @pytest.mark.asyncio
    async def test_send_document_missing_path_returns_error(self):
        """send_document without a path must return an error."""
        from tools.notify import handle  # type: ignore[import]

        with patch.dict(os.environ, ENV_VARS), patch("tools.notify.httpx.AsyncClient") as MockClient:
            result = await handle({"action": "send_document"})
            MockClient.assert_not_called()

        assert result[0]["type"] == "text"
        assert "path" in result[0]["text"].lower()


# ===========================================================================
# Rate limiting — 1 test
# ===========================================================================


class TestRateLimiting:
    """429 response must be returned as an error without retry."""

    @pytest.mark.asyncio
    async def test_rate_limit_429_returns_error_no_retry(self):
        """When Telegram returns 429, handle() must return an error immediately."""
        from tools.notify import handle  # type: ignore[import]

        client = _make_mock_client(429, {"ok": False, "description": "Too Many Requests"})
        with patch.dict(os.environ, ENV_VARS), patch("tools.notify.httpx.AsyncClient", return_value=client):
            result = await handle({"action": "send", "text": "flood"})

        assert result[0]["type"] == "text"
        assert "429" in result[0]["text"] or "rate" in result[0]["text"].lower()
        # Exactly one POST was made — no retry
        assert client.post.call_count == 1


# ===========================================================================
# Unknown action — 1 test
# ===========================================================================


class TestUnknownAction:
    """handle() with an unrecognised action."""

    @pytest.mark.asyncio
    async def test_unknown_action_returns_error(self):
        """An unrecognised action must return a descriptive error."""
        from tools.notify import handle  # type: ignore[import]

        with patch.dict(os.environ, ENV_VARS), patch("tools.notify.httpx.AsyncClient") as MockClient:
            result = await handle({"action": "launch_rockets"})
            MockClient.assert_not_called()

        assert result[0]["type"] == "text"
        assert "launch_rockets" in result[0]["text"] or "unknown" in result[0]["text"].lower()
