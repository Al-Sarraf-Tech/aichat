"""
Tool registry for MCP tools expansion.

Each tool module registers itself by calling register() and appending
to TOOL_SCHEMAS / TOOL_HANDLERS.

Usage (in a tool module):
    from tools import register, TOOL_SCHEMAS, TOOL_HANDLERS
    register(schema, handler)
"""
from __future__ import annotations

from typing import Any, Callable, Awaitable

# Populated by individual tool modules via register()
TOOL_SCHEMAS: list[dict[str, Any]] = []
TOOL_HANDLERS: dict[str, Callable[..., Awaitable[Any]]] = {}


def register(
    schema: dict[str, Any],
    handler: Callable[..., Awaitable[Any]],
) -> None:
    """Register a tool schema and its async handler.

    Args:
        schema: MCP tool schema dict with at least a 'name' key.
        handler: Async callable that implements the tool.
    """
    name = schema["name"]
    if name in TOOL_HANDLERS:
        raise ValueError(f"Tool {name!r} already registered")
    TOOL_SCHEMAS.append(schema)
    TOOL_HANDLERS[name] = handler
