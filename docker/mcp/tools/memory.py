"""
Memory tool handlers — persistent key-value store.

Resolved actions: memory_store, memory_recall
(dispatched from the 'memory' mega-tool via _resolve_mega_tool)
"""
from __future__ import annotations

from typing import Any

from tools import TOOL_HANDLERS  # type: ignore[import]
from tools._helpers import text, json_or_err, get_client, MEMORY_URL  # type: ignore[import]


async def _memory_store(args: dict[str, Any]) -> list[dict[str, Any]]:
    key = str(args.get("key", "")).strip()
    value = str(args.get("value", "")).strip()
    if not key:
        return text("memory_store: 'key' is required")
    if not value:
        return text("memory_store: 'value' is required")
    payload: dict[str, Any] = {"key": key, "value": value}
    if args.get("ttl_seconds"):
        try:
            payload["ttl_seconds"] = int(args["ttl_seconds"])
        except (ValueError, TypeError):
            return text("memory_store: 'ttl_seconds' must be an integer")
    async with get_client() as c:
        r = await c.post(f"{MEMORY_URL}/store", json=payload)
        return json_or_err(r, "memory_store")


async def _memory_recall(args: dict[str, Any]) -> list[dict[str, Any]]:
    params: dict[str, str] = {}
    if args.get("key"):
        params["key"] = str(args["key"])
    if args.get("pattern"):
        params["pattern"] = str(args["pattern"])
    async with get_client() as c:
        r = await c.get(f"{MEMORY_URL}/recall", params=params)
        return json_or_err(r, "memory_recall")


# Register handlers directly (no schema — the mega-tool schema is in app.py)
TOOL_HANDLERS["memory_store"] = _memory_store
TOOL_HANDLERS["memory_recall"] = _memory_recall
