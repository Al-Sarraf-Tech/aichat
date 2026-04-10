"""
Data tool handlers — article storage, cache, image registry, error log.

Resolved actions: db_store_article, db_search, db_cache_store, db_cache_get,
                  db_store_image, get_errors
(dispatched from the 'data' mega-tool via _resolve_mega_tool)

Note: db_list_images stays in app.py because it depends on _image_blocks/renderer.
"""
from __future__ import annotations

from typing import Any

from tools import TOOL_HANDLERS  # type: ignore[import]
from tools._helpers import text, json_or_err, get_client, DATABASE_URL  # type: ignore[import]


async def _db_store_article(args: dict[str, Any]) -> list[dict[str, Any]]:
    async with get_client() as c:
        r = await c.post(f"{DATABASE_URL}/articles/store", json=args)
        return json_or_err(r, "db_store_article")


async def _db_search(args: dict[str, Any]) -> list[dict[str, Any]]:
    params: dict[str, Any] = {}
    if args.get("topic"):
        params["topic"] = str(args["topic"])
    if args.get("q"):
        params["q"] = str(args["q"])
    try:
        params["limit"] = max(1, min(200, int(args.get("limit", 20))))
        params["offset"] = max(0, int(args.get("offset", 0)))
    except (ValueError, TypeError) as exc:
        return text(f"db_search: 'limit' and 'offset' must be integers — {exc}")
    if args.get("summary_only"):
        params["summary_only"] = "true"
    async with get_client() as c:
        r = await c.get(f"{DATABASE_URL}/articles/search", params=params)
        return json_or_err(r, "db_search")


async def _db_cache_store(args: dict[str, Any]) -> list[dict[str, Any]]:
    url = str(args.get("url", "")).strip()
    content = str(args.get("content", "")).strip()
    if not url:
        return text("db_cache_store: 'url' is required")
    if not content:
        return text("db_cache_store: 'content' is required")
    payload: dict[str, Any] = {"url": url, "content": content}
    if args.get("title"):
        payload["title"] = str(args["title"])
    async with get_client() as c:
        r = await c.post(f"{DATABASE_URL}/cache/store", json=payload)
        return json_or_err(r, "db_cache_store")


async def _db_cache_get(args: dict[str, Any]) -> list[dict[str, Any]]:
    url = str(args.get("url", "")).strip()
    if not url:
        return text("db_cache_get: 'url' is required")
    async with get_client() as c:
        r = await c.get(f"{DATABASE_URL}/cache/get", params={"key": url})
        return json_or_err(r, "db_cache_get")


async def _db_store_image(args: dict[str, Any]) -> list[dict[str, Any]]:
    async with get_client() as c:
        r = await c.post(f"{DATABASE_URL}/images/store", json={
            "url":       args.get("url", ""),
            "host_path": args.get("host_path", ""),
            "alt_text":  args.get("alt_text", ""),
        })
        return json_or_err(r, "db_store_image")


async def _get_errors(args: dict[str, Any]) -> list[dict[str, Any]]:
    limit = max(1, min(int(args.get("limit", 50)), 200))
    params: dict[str, Any] = {"limit": limit}
    svc = str(args.get("service", "")).strip()
    if svc:
        params["service"] = svc
    async with get_client() as c:
        r = await c.get(f"{DATABASE_URL}/errors/recent", params=params, timeout=10.0)
        if r.status_code != 200:
            return text(f"get_errors: upstream returned {r.status_code} — {r.text[:300]}")
        data = r.json()
        errors = data.get("errors", [])
        if not errors:
            return text("No errors logged yet." + (f" (service={svc})" if svc else ""))
        lines = [f"Recent errors ({len(errors)}):"]
        for e in errors:
            ts = str(e.get("logged_at", ""))[:19].replace("T", " ")
            lines.append(
                f"  [{ts}] [{e.get('level', '?')}] {e.get('service', '?')}: "
                f"{e.get('message', '')}"
                + (f"\n    detail: {e['detail']}" if e.get("detail") else "")
            )
        return text("\n".join(lines))


# Register handlers (no schema — mega-tool schema is in app.py)
TOOL_HANDLERS["db_store_article"] = _db_store_article
TOOL_HANDLERS["db_search"]        = _db_search
TOOL_HANDLERS["db_cache_store"]   = _db_cache_store
TOOL_HANDLERS["db_cache_get"]     = _db_cache_get
TOOL_HANDLERS["db_store_image"]   = _db_store_image
TOOL_HANDLERS["get_errors"]       = _get_errors
