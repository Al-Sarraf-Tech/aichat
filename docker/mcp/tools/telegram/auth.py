"""Authorization gate — checks chat ID against config."""
from __future__ import annotations

from tools.telegram import config


def is_authorized(message: dict) -> bool:
    """Return True if message is from the authorized chat."""
    if not config.CHAT_ID:
        return False
    return str(message.get("chat", {}).get("id", "")) == config.CHAT_ID
