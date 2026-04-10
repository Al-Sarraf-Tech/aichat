"""
Knowledge graph tool handlers — NetworkX/SQLite graph database.

Resolved actions: graph_add_node, graph_add_edge, graph_query, graph_path, graph_search
(dispatched from the 'knowledge' mega-tool via _resolve_mega_tool)
"""
from __future__ import annotations

from typing import Any

from tools import TOOL_HANDLERS  # type: ignore[import]
from tools._helpers import text, get_client, GRAPH_URL  # type: ignore[import]


async def _graph_add_node(args: dict[str, Any]) -> list[dict[str, Any]]:
    node_id    = str(args.get("id", "")).strip()
    labels     = list(args.get("labels", []))
    properties = dict(args.get("properties", {}))
    if not node_id:
        return text("graph_add_node: 'id' is required")
    async with get_client() as c:
        r = await c.post(
            f"{GRAPH_URL}/nodes/add",
            json={"id": node_id, "labels": labels, "properties": properties},
            timeout=10,
        )
        if r.status_code >= 400:
            return text(f"graph_add_node failed: {r.status_code} — {r.text[:300]}")
        d = r.json()
        return text(f"Node added: {d.get('added')} (labels={d.get('labels')})")


async def _graph_add_edge(args: dict[str, Any]) -> list[dict[str, Any]]:
    from_id    = str(args.get("from_id", "")).strip()
    to_id      = str(args.get("to_id",   "")).strip()
    etype      = str(args.get("type", "related")).strip() or "related"
    properties = dict(args.get("properties", {}))
    if not from_id or not to_id:
        return text("graph_add_edge: 'from_id' and 'to_id' are required")
    async with get_client() as c:
        r = await c.post(
            f"{GRAPH_URL}/edges/add",
            json={
                "from_id": from_id, "to_id": to_id,
                "type": etype, "properties": properties,
            },
            timeout=10,
        )
        if r.status_code >= 400:
            return text(f"graph_add_edge failed: {r.status_code} — {r.text[:300]}")
        d = r.json()
        return text(
            f"Edge added: {d.get('from_id')} -[{etype}]-> {d.get('to_id')} "
            f"(id={d.get('added')})"
        )


async def _graph_query(args: dict[str, Any]) -> list[dict[str, Any]]:
    node_id = str(args.get("id", "")).strip()
    if not node_id:
        return text("graph_query: 'id' is required")
    async with get_client() as c:
        r = await c.get(f"{GRAPH_URL}/nodes/{node_id}/neighbors", timeout=10)
        if r.status_code == 404:
            return text(f"graph_query: node '{node_id}' not found")
        if r.status_code >= 400:
            return text(f"graph_query failed: {r.status_code} — {r.text[:300]}")
        d = r.json()
        neighbors = d.get("neighbors", [])
        if not neighbors:
            return text(f"Node '{node_id}' has no outgoing neighbors.")
        lines = [f"Neighbors of '{node_id}' ({len(neighbors)}):"]
        for nb in neighbors:
            props = nb.get("properties", {})
            lines.append(
                f"  → {nb.get('id', '?')} [{nb.get('edge_type', '')}]"
                + (f" {props}" if props else "")
            )
        return text("\n".join(lines))


async def _graph_path(args: dict[str, Any]) -> list[dict[str, Any]]:
    from_id = str(args.get("from_id", "")).strip()
    to_id   = str(args.get("to_id",   "")).strip()
    if not from_id or not to_id:
        return text("graph_path: 'from_id' and 'to_id' are required")
    async with get_client() as c:
        r = await c.post(
            f"{GRAPH_URL}/path",
            json={"from_id": from_id, "to_id": to_id},
            timeout=15,
        )
        if r.status_code >= 400:
            return text(f"graph_path failed: {r.status_code} — {r.text[:300]}")
        d = r.json()
        path = d.get("path")
        if not path:
            return text(f"No path found from '{from_id}' to '{to_id}'.")
        length = d.get("length", len(path) - 1)
        return text(f"Path ({length} hops): {' → '.join(path)}")


async def _graph_search(args: dict[str, Any]) -> list[dict[str, Any]]:
    label      = str(args.get("label", "")).strip()
    properties = dict(args.get("properties", {}))
    limit_g    = int(args.get("limit", 50))
    async with get_client() as c:
        r = await c.post(
            f"{GRAPH_URL}/search",
            json={"label": label, "properties": properties, "limit": limit_g},
            timeout=10,
        )
        if r.status_code >= 400:
            return text(f"graph_search failed: {r.status_code} — {r.text[:300]}")
        d = r.json()
        results = d.get("results", [])
        if not results:
            return text(f"graph_search: no nodes found (label={label!r})")
        lines = [f"Found {len(results)} node(s):"]
        for node in results:
            lbl = ",".join(node.get("labels", []))
            props = node.get("properties", {})
            lines.append(f"  {node.get('id', '?')} [{lbl}] {props}")
        return text("\n".join(lines))


# Register handlers (no schema — mega-tool schema is in app.py)
TOOL_HANDLERS["graph_add_node"] = _graph_add_node
TOOL_HANDLERS["graph_add_edge"] = _graph_add_edge
TOOL_HANDLERS["graph_query"]    = _graph_query
TOOL_HANDLERS["graph_path"]     = _graph_path
TOOL_HANDLERS["graph_search"]   = _graph_search
