"""
System tool handlers — get_system_instructions, desktop_screenshot,
desktop_control, workspace, and list_tools_by_category.

Extracted from app.py.

Note on list_tools_by_category: this handler needs access to _TOOLS (the
full tool schema list built in app.py) for description lookups.  Importing
_TOOLS from app.py would create a circular import.  Instead we provide
register_tool_list(tools) which app.py calls after building _TOOLS, storing
the reference in a module-level variable used by the handler.  The category
dict and the search-by-name path work without it; only the per-tool
description lookup degrades gracefully to '(no description)' if it has not
been registered yet.

Registered with the tool registry at import time via TOOL_HANDLERS.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any

import httpx

from tools import TOOL_HANDLERS  # type: ignore[import]
from tools._helpers import text as _text, BROWSER_URL, BROWSER_WORKSPACE  # type: ignore[import]
from tools._imaging import image_blocks as _image_blocks  # type: ignore[import]


# ---------------------------------------------------------------------------
# Tool-list reference — populated by app.py after _TOOLS is built
# ---------------------------------------------------------------------------

_registered_tools: list[dict] = []


def register_tool_list(tools: list[dict]) -> None:
    """Called by app.py after _TOOLS is assembled so descriptions are available."""
    _registered_tools.clear()
    _registered_tools.extend(tools)


# ---------------------------------------------------------------------------
# get_system_instructions — returns planner directive for MCP clients
# ---------------------------------------------------------------------------

async def _get_system_instructions(args: dict[str, Any]) -> list[dict[str, Any]]:
    from system_prompt import get_system_prompt as _get_sys_prompt  # type: ignore[import]
    _base_prompt = _get_sys_prompt()
    _planning_hint = (
        "\n\n## Planning Workflow\n\n"
        "For any task that requires more than one tool call, or where the right\n"
        "sequence of tools is unclear, call plan_task FIRST.\n\n"
        "  plan_task(task='...', context='...', max_steps=N)\n\n"
        "It returns an ordered STEPS list with exact tool names, args, and\n"
        "dependencies. Execute each step in order, passing prior outputs forward.\n\n"
        "For single-tool tasks, call the tool directly.\n\n"
        "Always prefer fewer, targeted tool calls over verbose chains.\n"
        "When referencing a prior step's output, describe it clearly in the next call's args."
    )
    return _text(_base_prompt + _planning_hint)


# ---------------------------------------------------------------------------
# desktop_screenshot — full X11 desktop capture via human_browser
# ---------------------------------------------------------------------------

async def _desktop_screenshot(args: dict[str, Any]) -> list[dict[str, Any]]:
    region_ds = args.get("region")
    payload_ds: dict = {}
    if region_ds:
        payload_ds["region"] = region_ds
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            ds_r = await c.post(f"{BROWSER_URL}/desktop/screenshot",
                                json=payload_ds, timeout=15)
        if ds_r.status_code != 200:
            return _text(f"desktop_screenshot: browser server error {ds_r.status_code} — {ds_r.text[:300]}")
        ds_data = ds_r.json()
        img_path_ds = ds_data.get("path", "")
        if not img_path_ds:
            return _text(f"desktop_screenshot: no path in response — {ds_data}")
        # Translate browser-container path to mcp-container bind-mount path
        fname_ds = os.path.basename(img_path_ds)
        local_ds = os.path.join(BROWSER_WORKSPACE, fname_ds)
        return _image_blocks(local_ds, f"Desktop screenshot saved to {fname_ds}")
    except Exception as exc_ds:
        return _text(f"desktop_screenshot: {exc_ds}")


# ---------------------------------------------------------------------------
# desktop_control — xdotool computer use via human_browser
# ---------------------------------------------------------------------------

async def _desktop_control(args: dict[str, Any]) -> list[dict[str, Any]]:
    dc_action = str(args.get("action", "")).strip()
    if not dc_action:
        return _text("desktop_control: 'action' is required")
    payload_dc: dict = {"action": dc_action}
    for k_dc in ("x", "y", "text", "command", "button", "direction", "amount"):
        if args.get(k_dc) is not None:
            payload_dc[k_dc] = args[k_dc]
    try:
        async with httpx.AsyncClient(timeout=25) as c:
            dc_r = await c.post(f"{BROWSER_URL}/desktop/control",
                                json=payload_dc, timeout=20)
        if dc_r.status_code != 200:
            return _text(f"desktop_control: browser server error {dc_r.status_code} — {dc_r.text[:300]}")
        return _text(json.dumps(dc_r.json()))
    except Exception as exc_dc:
        return _text(f"desktop_control: {exc_dc}")


# ---------------------------------------------------------------------------
# list_tools_by_category — tool discovery helper
# ---------------------------------------------------------------------------

_TOOL_CATEGORIES: dict[str, list[str]] = {
    "web": ["web(search)", "web(fetch)", "web(extract)", "web(summarize)",
            "web(news)", "web(wikipedia)", "web(arxiv)", "web(youtube)"],
    "browser": ["browser(navigate)", "browser(read)", "browser(click)", "browser(scroll)",
                "browser(fill)", "browser(eval)", "browser(screenshot)", "browser(screenshot_search)",
                "browser(bulk_screenshot)", "browser(scroll_screenshot)", "browser(screenshot_element)",
                "browser(save_images)", "browser(download_images)", "browser(list_images)",
                "browser(scrape)", "browser(keyboard)", "browser(fill_form)"],
    "image": ["image(fetch)", "image(search)", "image(generate)", "image(edit)",
              "image(crop)", "image(zoom)", "image(enhance)", "image(scan)",
              "image(stitch)", "image(diff)", "image(annotate)", "image(caption)",
              "image(upscale)", "image(remix)", "image(face_detect)", "image(similarity)"],
    "document": ["document(ingest)", "document(tables)", "document(ocr)", "document(ocr_pdf)",
                 "document(pdf_read)", "document(pdf_edit)", "document(pdf_form)",
                 "document(pdf_merge)", "document(pdf_split)"],
    "media": ["media(video_info)", "media(video_frames)", "media(video_thumbnail)",
              "media(video_transcode)", "media(tts)", "media(detect_objects)", "media(detect_humans)"],
    "data": ["data(store_article)", "data(search)", "data(cache_store)", "data(cache_get)",
             "data(store_image)", "data(list_images)", "data(errors)"],
    "memory": ["memory(store)", "memory(recall)"],
    "knowledge": ["knowledge(add_node)", "knowledge(add_edge)", "knowledge(query)",
                  "knowledge(path)", "knowledge(search)"],
    "vector": ["vector(store)", "vector(search)", "vector(delete)", "vector(collections)",
               "vector(embed_store)", "vector(embed_search)"],
    "code": ["code(python)", "code(javascript)", "code(jupyter)"],
    "custom_tools": ["custom_tools(create)", "custom_tools(list)",
                     "custom_tools(delete)", "custom_tools(call)"],
    "planner": ["planner(create)", "planner(get)", "planner(complete)", "planner(fail)",
                "planner(list)", "planner(delete)", "planner(orchestrate)", "planner(plan)"],
    "jobs": ["jobs(submit)", "jobs(status)", "jobs(result)", "jobs(cancel)",
             "jobs(list)", "jobs(batch)"],
    "research": ["research(rss_search)", "research(rss_push)",
                 "research(deep)", "research(realtime)"],
    "system": ["system(list_categories)", "system(instructions)",
               "system(desktop_screenshot)", "system(desktop_control)"],
    "utility": ["think"],
}


async def _list_tools_by_category(args: dict[str, Any]) -> list[dict[str, Any]]:
    cat_q    = str(args.get("category", "")).strip().lower()
    search_q = str(args.get("search", "")).strip().lower()

    # Build tool name→description lookup from the registered tool list
    tool_desc: dict[str, str] = {}
    for t_entry in _registered_tools:
        t_name = t_entry.get("name", "")
        t_desc = t_entry.get("description", "")
        if t_name:
            tool_desc[t_name] = t_desc[:120]

    if cat_q and cat_q in _TOOL_CATEGORIES:
        tool_names = _TOOL_CATEGORIES[cat_q]
        lines_tc: list[str] = [f"Category: {cat_q} ({len(tool_names)} tools)\n"]
        for tn in tool_names:
            lines_tc.append(f"  {tn}: {tool_desc.get(tn, '(no description)')}")
        return _text("\n".join(lines_tc))
    elif search_q:
        matches: list[str] = []
        for tn, td in tool_desc.items():
            if search_q in tn.lower() or search_q in td.lower():
                matches.append(f"  {tn}: {td}")
        if matches:
            return _text(f"Tools matching '{search_q}' ({len(matches)}):\n" + "\n".join(matches[:20]))
        return _text(f"No tools matching '{search_q}'")
    else:
        # List all categories
        lines_all: list[str] = ["Available categories:\n"]
        for cat_name, cat_tools in sorted(_TOOL_CATEGORIES.items()):
            lines_all.append(f"  {cat_name} ({len(cat_tools)} tools)")
        lines_all.append("\nCall with category='<name>' to list tools in that category.")
        return _text("\n".join(lines_all))


# ---------------------------------------------------------------------------
# workspace — per-user file storage
# ---------------------------------------------------------------------------

async def _workspace(args: dict[str, Any]) -> list[dict[str, Any]]:
    import pathlib
    _WS_ROOT = pathlib.Path("/workspace")
    action   = str(args.get("action", "")).strip()
    user     = str(args.get("user", "default")).strip()
    rel_path = str(args.get("path", "")).strip()

    # Sanitize username — alphanumeric, hyphens, underscores only
    if not re.match(r'^[a-zA-Z0-9_-]{1,64}$', user):
        return _text("workspace: invalid username")

    user_root = _WS_ROOT / user
    user_root.mkdir(parents=True, exist_ok=True)

    # Resolve and verify path stays within user's folder
    if rel_path:
        if '\x00' in rel_path or rel_path.startswith('/'):
            return _text("workspace: invalid path")
        target = (user_root / rel_path).resolve()
    else:
        target = user_root.resolve()
    if not str(target).startswith(str(user_root.resolve())):
        return _text("workspace: path traversal blocked")

    if action == "list":
        if not target.exists():
            return _text(f"workspace: path not found: {rel_path or '/'}")
        if target.is_file():
            st = target.stat()
            return _text(f"📄 {target.name} ({st.st_size} bytes)")
        entries = sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name))
        lines = [f"📁 **{user}** workspace — {rel_path or '/'}\n"]
        for e in entries:
            if e.is_dir():
                lines.append(f"  📁 {e.name}/")
            else:
                lines.append(f"  📄 {e.name} ({e.stat().st_size:,} bytes)")
        if len(entries) == 0:
            lines.append("  (empty)")
        return _text("\n".join(lines))

    elif action == "read":
        if not rel_path:
            return _text("workspace: 'path' is required for read")
        if not target.exists():
            return _text(f"workspace: file not found: {rel_path}")
        if not target.is_file():
            return _text(f"workspace: not a file: {rel_path}")
        if target.stat().st_size > 5 * 1024 * 1024:
            return _text("workspace: file too large (>5MB)")
        try:
            content = target.read_text(errors="replace")
        except Exception:
            content = "(binary file — cannot display as text)"
        return _text(f"📄 **{rel_path}**\n```\n{content}\n```")

    elif action == "write":
        if not rel_path:
            return _text("workspace: 'path' is required for write")
        content = str(args.get("content", ""))
        if len(content) > 10 * 1024 * 1024:  # 10 MB write limit
            return _text("workspace: content too large (>10MB)")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        return _text(f"✅ Written {len(content)} bytes to {user}/{rel_path}")

    elif action == "delete":
        if not rel_path:
            return _text("workspace: 'path' is required for delete")
        if not target.exists():
            return _text(f"workspace: not found: {rel_path}")
        if target.is_dir():
            import shutil
            shutil.rmtree(target)
            return _text(f"🗑️ Deleted directory {user}/{rel_path}")
        target.unlink()
        return _text(f"🗑️ Deleted {user}/{rel_path}")

    elif action == "mkdir":
        if not rel_path:
            return _text("workspace: 'path' is required for mkdir")
        target.mkdir(parents=True, exist_ok=True)
        return _text(f"📁 Created {user}/{rel_path}/")

    elif action == "info":
        if not user_root.exists():
            return _text(f"📁 **{user}** workspace: empty (0 files, 0 bytes)")
        total_files = 0
        total_bytes = 0
        for f in user_root.rglob("*"):
            if f.is_file():
                total_files += 1
                total_bytes += f.stat().st_size
        return _text(
            f"📁 **{user}** workspace\n"
            f"  Files: {total_files}\n"
            f"  Size: {total_bytes:,} bytes ({total_bytes / 1024 / 1024:.1f} MB)"
        )
    else:
        return _text(f"workspace: unknown action '{action}'")


# ---------------------------------------------------------------------------
# Register handlers
# ---------------------------------------------------------------------------

TOOL_HANDLERS["get_system_instructions"] = _get_system_instructions
TOOL_HANDLERS["desktop_screenshot"]      = _desktop_screenshot
TOOL_HANDLERS["desktop_control"]         = _desktop_control
TOOL_HANDLERS["list_tools_by_category"]  = _list_tools_by_category
TOOL_HANDLERS["workspace"]               = _workspace
