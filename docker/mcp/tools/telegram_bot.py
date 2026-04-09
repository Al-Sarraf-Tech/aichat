"""
Telegram command handler — background poll loop + intent dispatcher.

This module is NOT registered as an MCP tool.  It runs as a background
asyncio task (started from app.py) and listens for inbound Telegram
messages directed at the configured bot.

Environment variables:
  TELEGRAM_BOT_TOKEN   — bot token from @BotFather (required)
  TELEGRAM_CHAT_ID     — destination chat / channel ID (required)
  IMAGE_GEN_BASE_URL   — LM Studio base URL (default: http://192.168.50.2:1234)
  TEAM_SSH_HOST        — SSH host for code dispatch (default: host.docker.internal)
  TEAM_SSH_PORT        — SSH port (default: 1337)
  TEAM_SSH_USER        — SSH user (default: jalsarraf)
  TEAM_SSH_KEY         — path to SSH private key (default: /app/.ssh/team_key)
"""
from __future__ import annotations

import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants (resolved at module load from environment)
# ---------------------------------------------------------------------------

_TOKEN: str = os.environ.get("TELEGRAM_BOT_TOKEN", "")
_CHAT_ID: str = os.environ.get("TELEGRAM_CHAT_ID", "")
_BASE_URL: str = f"https://api.telegram.org/bot{_TOKEN}"
_LM_STUDIO_URL: str = os.environ.get("IMAGE_GEN_BASE_URL", "http://192.168.50.2:1234")
_MAX_MSG_LEN: int = 4096
_SSH_HOST: str = os.environ.get("TEAM_SSH_HOST", "host.docker.internal")
_SSH_PORT: int = int(os.environ.get("TEAM_SSH_PORT", "1337"))
_SSH_USER: str = os.environ.get("TEAM_SSH_USER", "jalsarraf")
_SSH_KEY: str = os.environ.get("TEAM_SSH_KEY", "/app/.ssh/team_key")

# ---------------------------------------------------------------------------
# Telegram API helpers
# ---------------------------------------------------------------------------


async def _send_telegram(text: str, reply_to: int | None = None) -> None:
    """POST a message to the configured Telegram chat.

    Truncates text exceeding _MAX_MSG_LEN to fit the API limit.
    Errors are logged but not raised.
    """
    token = os.environ.get("TELEGRAM_BOT_TOKEN", _TOKEN)
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", _CHAT_ID)

    if len(text) > _MAX_MSG_LEN:
        text = text[: _MAX_MSG_LEN - len("(truncated)")] + "(truncated)"

    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown",
    }
    if reply_to is not None:
        payload["reply_to_message_id"] = reply_to

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, json=payload)
        if resp.status_code != 200:
            logger.error("Telegram sendMessage failed: %s %s", resp.status_code, resp.text)
    except Exception as exc:  # noqa: BLE001
        logger.error("_send_telegram error: %s", exc)


async def _get_updates(offset: int) -> list[dict[str, Any]]:
    """GET /getUpdates with long-polling.

    Returns a list of update dicts, or [] on any error.
    """
    token = os.environ.get("TELEGRAM_BOT_TOKEN", _TOKEN)
    url = f"https://api.telegram.org/bot{token}/getUpdates"
    params = {
        "offset": offset,
        "timeout": 30,
        "allowed_updates": ["message"],
    }
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, params=params)
        data = resp.json()
        if data.get("ok"):
            return data.get("result", [])
        logger.warning("getUpdates not ok: %s", data)
        return []
    except Exception as exc:  # noqa: BLE001
        logger.error("_get_updates error: %s", exc)
        return []
