"""
Telegram notify MCP tool — send messages, alerts, photos, and documents.

Actions:
  send          — send a plain text message (Markdown parse_mode)
  send_alert    — formatted alert with severity icon and UTC timestamp
  send_photo    — send a photo from a /workspace/ file path or a URL
  send_document — send a document from a /workspace/ file path

No SSH dependency.  Uses httpx directly against the Telegram Bot API.

Environment variables required:
  TELEGRAM_BOT_TOKEN  — bot token from @BotFather
  TELEGRAM_CHAT_ID    — destination chat / channel ID

Registered with the tool registry at import time via register().
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

import httpx

from tools import register  # type: ignore[import]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_API_BASE = "https://api.telegram.org/bot{token}/"
_WORKSPACE_PREFIX = "/workspace/"

_SEVERITY_ICONS: dict[str, str] = {
    "info": "\u2139\ufe0f",
    "warning": "\u26a0\ufe0f",
    "critical": "\U0001f6a8",
}

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

SCHEMA: dict[str, Any] = {
    "name": "notify",
    "description": (
        "Send notifications via Telegram.\n"
        "Actions:\n"
        "  send          — send a plain text message\n"
        "  send_photo    — send a photo from /workspace/ file path or URL\n"
        "  send_document — send a document from /workspace/ file path\n"
        "  send_alert    — formatted alert with severity icon and UTC timestamp"
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["send", "send_photo", "send_document", "send_alert"],
            },
            "text": {"type": "string"},
            "severity": {
                "type": "string",
                "enum": ["info", "warning", "critical"],
            },
            "path": {"type": "string"},
            "url": {"type": "string"},
            "caption": {"type": "string"},
        },
        "required": ["action"],
    },
}

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _text(msg: str) -> list[dict[str, Any]]:
    return [{"type": "text", "text": msg}]


def _get_credentials() -> tuple[str, str] | None:
    """Return (token, chat_id) or None if either env var is missing."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        return None
    return token, chat_id


async def _post_json(
    token: str,
    method: str,
    payload: dict[str, Any],
) -> list[dict[str, Any]]:
    """POST a JSON payload to a Telegram Bot API method.

    Returns a text result.  On 429 returns an error immediately (no retry).
    """
    url = _API_BASE.format(token=token) + method
    async with httpx.AsyncClient() as client:
        resp = await client.post(url, json=payload)

    if resp.status_code == 429:
        return _text(f"Rate limited (429): {resp.json().get('description', 'Too Many Requests')}")

    data = resp.json()
    if not data.get("ok"):
        return _text(f"Telegram error: {data.get('description', 'unknown error')}")

    msg_id = data.get("result", {}).get("message_id", "?")
    return _text(f"Message sent (id={msg_id})")


async def _post_multipart(
    token: str,
    method: str,
    fields: dict[str, Any],
    file_field: str,
    file_path: str,
) -> list[dict[str, Any]]:
    """POST a multipart/form-data request with a file upload."""
    url = _API_BASE.format(token=token) + method
    try:
        with open(file_path, "rb") as fh:
            file_bytes = fh.read()
    except OSError as exc:
        return _text(f"Cannot open file {file_path!r}: {exc}")

    # Build multipart dict: {field: (filename, bytes, mime)} for the file,
    # and plain strings for all other fields.
    multipart: dict[str, Any] = {
        k: (None, str(v), "text/plain") for k, v in fields.items()
    }
    import mimetypes
    mime, _ = mimetypes.guess_type(file_path)
    mime = mime or "application/octet-stream"
    multipart[file_field] = (os.path.basename(file_path), file_bytes, mime)

    async with httpx.AsyncClient() as client:
        resp = await client.post(url, files=multipart)

    if resp.status_code == 429:
        return _text(f"Rate limited (429): {resp.json().get('description', 'Too Many Requests')}")

    data = resp.json()
    if not data.get("ok"):
        return _text(f"Telegram error: {data.get('description', 'unknown error')}")

    msg_id = data.get("result", {}).get("message_id", "?")
    return _text(f"File sent (id={msg_id})")


# ---------------------------------------------------------------------------
# Action handlers
# ---------------------------------------------------------------------------


async def _handle_send(args: dict[str, Any], token: str, chat_id: str) -> list[dict[str, Any]]:
    text = args.get("text", "").strip()
    if not text:
        return _text("Missing required parameter: 'text'")
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown",
    }
    return await _post_json(token, "sendMessage", payload)


async def _handle_send_alert(args: dict[str, Any], token: str, chat_id: str) -> list[dict[str, Any]]:
    text = args.get("text", "").strip()
    if not text:
        return _text("Missing required parameter: 'text'")

    severity = args.get("severity", "info").lower()
    icon = _SEVERITY_ICONS.get(severity, _SEVERITY_ICONS["info"])
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    formatted = f"{icon} {severity.upper()} \u2014 {timestamp}\n{text}"

    payload = {
        "chat_id": chat_id,
        "text": formatted,
        "parse_mode": "Markdown",
    }
    return await _post_json(token, "sendMessage", payload)


async def _handle_send_photo(args: dict[str, Any], token: str, chat_id: str) -> list[dict[str, Any]]:
    path = args.get("path", "")
    url = args.get("url", "")
    caption = args.get("caption", "")

    if not path and not url:
        return _text("Missing required parameter: 'path' or 'url'")

    if url:
        # Telegram fetches the image directly from the URL
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "photo": url,
            "parse_mode": "Markdown",
        }
        if caption:
            payload["caption"] = caption
        return await _post_json(token, "sendPhoto", payload)

    # File upload path
    if not path.startswith(_WORKSPACE_PREFIX):
        return _text(
            f"File path must start with {_WORKSPACE_PREFIX!r}. Got: {path!r}"
        )
    fields: dict[str, Any] = {"chat_id": chat_id, "parse_mode": "Markdown"}
    if caption:
        fields["caption"] = caption
    return await _post_multipart(token, "sendPhoto", fields, "photo", path)


async def _handle_send_document(args: dict[str, Any], token: str, chat_id: str) -> list[dict[str, Any]]:
    path = args.get("path", "")
    caption = args.get("caption", "")

    if not path:
        return _text("Missing required parameter: 'path'")

    if not path.startswith(_WORKSPACE_PREFIX):
        return _text(
            f"File path must start with {_WORKSPACE_PREFIX!r}. Got: {path!r}"
        )
    fields: dict[str, Any] = {"chat_id": chat_id, "parse_mode": "Markdown"}
    if caption:
        fields["caption"] = caption
    return await _post_multipart(token, "sendDocument", fields, "document", path)


# ---------------------------------------------------------------------------
# Public handler
# ---------------------------------------------------------------------------


async def handle(args: dict[str, Any], **_kwargs: Any) -> list[dict[str, Any]]:
    """Dispatch a notify action.

    No SSH parameter — talks directly to Telegram's Bot API.
    """
    action = args.get("action", "")
    if not action:
        return _text("Missing required parameter: 'action'")

    # Credential check — required for all actions
    creds = _get_credentials()
    if creds is None:
        return _text(
            "Missing Telegram credentials: set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID env vars"
        )
    token, chat_id = creds

    if action == "send":
        return await _handle_send(args, token, chat_id)
    if action == "send_alert":
        return await _handle_send_alert(args, token, chat_id)
    if action == "send_photo":
        return await _handle_send_photo(args, token, chat_id)
    if action == "send_document":
        return await _handle_send_document(args, token, chat_id)

    return _text(f"Unknown action: {action!r}. Valid: send, send_alert, send_photo, send_document")


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

register(SCHEMA, handle)
