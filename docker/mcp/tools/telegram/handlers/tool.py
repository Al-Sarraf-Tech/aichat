"""Tool dispatch — invoke MCP TOOL_HANDLERS."""
from __future__ import annotations

import logging

from tools import TOOL_HANDLERS
from tools.telegram import api
from tools.telegram.models import Intent

logger = logging.getLogger(__name__)


async def handle_tool(intent: Intent, reply_to: int | None = None) -> None:
    handler = TOOL_HANDLERS.get(intent.tool)
    if handler is None:
        await api.send_message(f"Unknown tool: `{intent.tool}`.", reply_to=reply_to)
        return

    await api.send_message(f"Running: {intent.tool} {intent.action}", reply_to=reply_to)
    try:
        result = await handler({"action": intent.action, **intent.args})
        if isinstance(result, list):
            texts = [b["text"] for b in result if isinstance(b, dict) and b.get("type") == "text"]
            reply_text = "\n".join(texts) if texts else str(result)
        else:
            reply_text = str(result)
        await api.send_message(reply_text, reply_to=reply_to)
    except Exception as exc:
        logger.error("Tool %s failed: %s", intent.tool, exc)
        await api.send_message(f"Error running {intent.tool}: {exc}", reply_to=reply_to)
