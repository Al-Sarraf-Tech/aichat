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

import dataclasses
import json
import logging
import os
import re
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


# ---------------------------------------------------------------------------
# Intent classifier
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class Intent:
    """Structured intent parsed from a Telegram message."""

    type: str                           # "tool" | "code" | "create" | "question"
    tool: str = ""
    action: str = ""
    args: dict = dataclasses.field(default_factory=dict)
    repo: str | None = None
    task: str = ""
    name: str = ""                      # project name (create)
    description: str = ""              # project description (create)
    language: str = ""                 # project language (create)
    text: str = ""                     # question text


_CLASSIFIER_SYSTEM_PROMPT = """\
You are an intent classifier for a Telegram bot that controls a dev-ops assistant.

Given a user message, output ONLY valid JSON (no markdown, no explanation) matching
one of these four intent types:

---
TYPE: tool
Use when the user wants to invoke an existing MCP tool.
Supported tools and their actions:
  monitor  — overview, containers, thermals, disk, gpu, services, tailscale
  git      — status, log, diff, ci, scorecard, create_pr, merge, push, trigger_ci, issues
  notify   — send, send_alert, send_photo, send_document
  ssh      — exec
  log      — read, tail, list
  iot      — list_devices, read_sensor, toggle_switch

JSON schema:
{"type": "tool", "tool": "<name>", "action": "<action>", "args": {}}

Examples:
  "show me the fleet overview"       → {"type": "tool", "tool": "monitor", "action": "overview", "args": {}}
  "check disk usage"                 → {"type": "tool", "tool": "monitor", "action": "disk", "args": {}}
  "git status of aichat"             → {"type": "tool", "tool": "git", "action": "status", "args": {"repo": "aichat"}}
  "send me a telegram alert"         → {"type": "tool", "tool": "notify", "action": "send_alert", "args": {}}

---
TYPE: code
Use when the user wants to modify an existing repository.

JSON schema:
{"type": "code", "repo": "<repo_name_or_null>", "task": "<what to do>"}

Examples:
  "add logging to main.py in aichat"   → {"type": "code", "repo": "aichat", "task": "add logging to main.py"}
  "fix the memory leak in cyberdeck"   → {"type": "code", "repo": "cyberdeck", "task": "fix the memory leak"}
  "update the README"                  → {"type": "code", "repo": null, "task": "update the README"}

---
TYPE: create
Use when the user wants to create a NEW project from scratch.

JSON schema:
{"type": "create", "name": "<project_name>", "description": "<what it does>", "language": "<lang>"}

Examples:
  "make me a rust cli for ssh keys"   → {"type": "create", "name": "ssh-keytool", "description": "a rust cli for managing ssh keys", "language": "rust"}
  "create a python script to backup postgres" → {"type": "create", "name": "pg-backup", "description": "backup postgres databases", "language": "python"}

---
TYPE: question
Use for any question, lookup, or request that does not fit the above.
Fallback when uncertain — prefer question over guessing wrong.

JSON schema:
{"type": "question", "text": "<the original message>"}

Examples:
  "what is the qdrant port?"         → {"type": "question", "text": "what is the qdrant port?"}
  "how do I add a new MCP tool?"     → {"type": "question", "text": "how do I add a new MCP tool?"}

---
IMPORTANT:
- Output ONLY raw JSON, no markdown code blocks, no prose.
- If in doubt, use type=question.
- Never invent tool names not listed above.
"""


async def _classify_intent(message: str) -> Intent:
    """Classify a message using LM Studio (gemma-4-e2b-it).

    Returns an Intent dataclass.  Falls back to Intent(type='question') on
    any error (network, JSON parse, unexpected schema).
    """
    lm_url = os.environ.get("IMAGE_GEN_BASE_URL", _LM_STUDIO_URL)
    url = f"{lm_url}/v1/chat/completions"
    payload = {
        "model": "gemma-4-e2b-it",
        "messages": [
            {"role": "system", "content": _CLASSIFIER_SYSTEM_PROMPT},
            {"role": "user", "content": message},
        ],
        "temperature": 0.1,
        "max_tokens": 256,
    }

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, json=payload, timeout=15.0)
        data = resp.json()
        raw_content: str = data["choices"][0]["message"]["content"].strip()

        # Gemma sometimes wraps output in ```json ... ``` — strip that
        raw_content = re.sub(r"^```(?:json)?\s*", "", raw_content)
        raw_content = re.sub(r"\s*```$", "", raw_content)

        parsed = json.loads(raw_content)
        intent_type = parsed.get("type", "question")

        if intent_type == "tool":
            return Intent(
                type="tool",
                tool=parsed.get("tool", ""),
                action=parsed.get("action", ""),
                args=parsed.get("args", {}),
            )
        if intent_type == "code":
            return Intent(
                type="code",
                repo=parsed.get("repo"),
                task=parsed.get("task", ""),
            )
        if intent_type == "create":
            return Intent(
                type="create",
                name=parsed.get("name", ""),
                description=parsed.get("description", ""),
                language=parsed.get("language", ""),
            )
        # question or unknown
        return Intent(type="question", text=parsed.get("text", message))

    except Exception as exc:  # noqa: BLE001
        logger.warning("_classify_intent error (%s), falling back to question", exc)
        return Intent(type="question", text=message)
