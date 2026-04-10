"""
Vector tool handlers — Qdrant vector database operations.

Resolved actions: vector_delete, vector_collections
(dispatched from the 'vector' mega-tool via _resolve_mega_tool)

Note: vector_store and vector_search stay in app.py (depend on _get_embed_model
and IMAGE_GEN_BASE_URL for embedding).
"""
from __future__ import annotations

import uuid
from typing import Any

from tools import TOOL_HANDLERS  # type: ignore[import]
from tools._helpers import text, get_client, VECTOR_URL  # type: ignore[import]


async def _vector_delete(args: dict[str, Any]) -> list[dict[str, Any]]:
    vid        = str(args.get("id", "")).strip()
    collection = str(args.get("collection", "default")).strip() or "default"
    if not vid:
        return text("vector_delete: 'id' is required")
    qdrant_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, vid))
    try:
        async with get_client() as c:
            r = await c.post(
                f"{VECTOR_URL}/collections/{collection}/points/delete",
                json={"points": [qdrant_id]},
                timeout=10,
            )
            r.raise_for_status()
    except Exception as exc:
        return text(f"vector_delete: failed — {exc}")
    return text(f"Deleted vector id={vid} from collection '{collection}'")


async def _vector_collections(args: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        async with get_client() as c:
            r = await c.get(f"{VECTOR_URL}/collections", timeout=10)
            r.raise_for_status()
            colls = r.json().get("result", {}).get("collections", [])
    except Exception as exc:
        return text(f"vector_collections: failed — {exc}")
    if not colls:
        return text("No Qdrant collections found. Use vector_store to create one.")
    lines = ["Qdrant collections:"]
    for col in colls:
        lines.append(f"  {col.get('name')} — vectors: {col.get('vectors_count', '?')}")
    return text("\n".join(lines))


# Register handlers
TOOL_HANDLERS["vector_delete"]      = _vector_delete
TOOL_HANDLERS["vector_collections"] = _vector_collections
