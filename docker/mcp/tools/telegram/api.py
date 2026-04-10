"""Telegram Bot API client — send messages, poll updates, handle rate limits."""
from __future__ import annotations

import asyncio
import logging

import httpx

from tools.telegram import config

logger = logging.getLogger(__name__)

_API_BASE = "https://api.telegram.org/bot{token}"


async def send_message(
    text: str,
    reply_to: int | None = None,
    chat_id: str | None = None,
) -> None:
    """Send a message to Telegram. Handles truncation, Markdown 400 retry, and 429 rate limit."""
    chat_id = chat_id or config.CHAT_ID
    if not config.BOT_TOKEN:
        return

    if len(text) > config.MAX_MSG_LEN:
        text = text[: config.MAX_MSG_LEN - 12] + "\n(truncated)"

    url = f"{_API_BASE.format(token=config.BOT_TOKEN)}/sendMessage"
    payload: dict = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    if reply_to is not None:
        payload["reply_to_message_id"] = reply_to

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(url, json=payload)

            # Markdown parse error — retry without parse_mode
            if resp.status_code == 400:
                payload.pop("parse_mode", None)
                resp = await client.post(url, json=payload)

            # Rate limit — sleep and retry once
            if resp.status_code == 429:
                retry_after = _extract_retry_after(resp)
                logger.warning("Telegram 429 — sleeping %ds", retry_after)
                await asyncio.sleep(retry_after)
                resp = await client.post(url, json=payload)
                if resp.status_code == 429:
                    logger.error("Telegram 429 after retry — dropping message")

    except Exception:
        logger.exception("send_message failed")


async def get_updates(offset: int) -> list[dict]:
    """Long-poll for new messages. Returns list of update dicts, or [] on error."""
    if not config.BOT_TOKEN:
        return []

    url = f"{_API_BASE.format(token=config.BOT_TOKEN)}/getUpdates"
    params = {"offset": offset, "timeout": 30, "allowed_updates": '["message"]'}

    try:
        async with httpx.AsyncClient(timeout=35) as client:
            resp = await client.get(url, params=params)
            data = resp.json()
            return data.get("result", [])
    except Exception:
        logger.exception("get_updates failed")
        return []


def _extract_retry_after(resp: httpx.Response) -> int:
    """Extract retry_after from 429 response (header or body)."""
    try:
        return int(resp.headers.get("Retry-After", 5))
    except (ValueError, TypeError):
        pass
    try:
        return resp.json().get("parameters", {}).get("retry_after", 5)
    except Exception:
        return 5
