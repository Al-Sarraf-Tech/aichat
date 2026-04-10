"""
Custom tools handlers — user-defined tool creation and invocation.

Resolved actions: create_tool, list_custom_tools, delete_custom_tool, call_custom_tool
(dispatched from the 'custom_tools' mega-tool via _resolve_mega_tool)
"""
from __future__ import annotations

from typing import Any

from tools import TOOL_HANDLERS  # type: ignore[import]
from tools._helpers import text, json_or_err, get_client, TOOLKIT_URL  # type: ignore[import]


async def _create_tool(args: dict[str, Any]) -> list[dict[str, Any]]:
    tool_name   = str(args.get("tool_name", "")).strip()
    description = str(args.get("description", "")).strip()
    parameters  = args.get("parameters", {})
    code        = str(args.get("code", "")).strip()
    if not tool_name or not description or not code:
        return text("create_tool: 'tool_name', 'description', and 'code' are required")
    async with get_client() as c:
        r = await c.post(
            f"{TOOLKIT_URL}/register",
            json={
                "tool_name": tool_name,
                "description": description,
                "parameters": parameters,
                "code": code,
            },
            timeout=10.0,
        )
        return json_or_err(r, "create_tool")


async def _list_custom_tools(args: dict[str, Any]) -> list[dict[str, Any]]:
    async with get_client() as c:
        r = await c.get(f"{TOOLKIT_URL}/tools", timeout=10.0)
        return json_or_err(r, "list_custom_tools")


async def _delete_custom_tool(args: dict[str, Any]) -> list[dict[str, Any]]:
    tool_name = str(args.get("tool_name", "")).strip()
    if not tool_name:
        return text("delete_custom_tool: 'tool_name' is required")
    async with get_client() as c:
        r = await c.delete(f"{TOOLKIT_URL}/tool/{tool_name}", timeout=10.0)
        return json_or_err(r, "delete_custom_tool")


async def _call_custom_tool(args: dict[str, Any]) -> list[dict[str, Any]]:
    tool_name = str(args.get("tool_name", "")).strip()
    params    = args.get("params", {})
    if not tool_name:
        return text("call_custom_tool: 'tool_name' is required")
    async with get_client() as c:
        r = await c.post(
            f"{TOOLKIT_URL}/call/{tool_name}",
            json={"params": params},
            timeout=30.0,
        )
        return json_or_err(r, "call_custom_tool")


async def _researchbox_search(args: dict[str, Any]) -> list[dict[str, Any]]:
    from tools._helpers import RESEARCH_URL  # type: ignore[import]
    async with get_client() as c:
        r = await c.get(
            f"{RESEARCH_URL}/search-feeds",
            params={"topic": args.get("topic", "")},
        )
        return json_or_err(r, "researchbox_search")


async def _researchbox_push(args: dict[str, Any]) -> list[dict[str, Any]]:
    from tools._helpers import RESEARCH_URL  # type: ignore[import]
    async with get_client() as c:
        r = await c.post(f"{RESEARCH_URL}/push-feed", json=args)
        return json_or_err(r, "researchbox_push")


# Register handlers
TOOL_HANDLERS["create_tool"]       = _create_tool
TOOL_HANDLERS["list_custom_tools"] = _list_custom_tools
TOOL_HANDLERS["delete_custom_tool"] = _delete_custom_tool
TOOL_HANDLERS["call_custom_tool"]  = _call_custom_tool
TOOL_HANDLERS["researchbox_search"] = _researchbox_search
TOOL_HANDLERS["researchbox_push"]  = _researchbox_push
