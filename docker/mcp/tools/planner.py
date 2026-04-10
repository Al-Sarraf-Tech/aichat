"""
Planner tool handlers — task management and job status queries.

Resolved actions: plan_create_task, plan_get_task, plan_complete_task,
    plan_fail_task, plan_list_tasks, plan_delete_task,
    job_status, job_result, job_list, think

Note: job_submit, job_cancel, batch_submit stay in app.py because
they depend on _execute_job and _job_cancelled (circular with _call_tool).
"""
from __future__ import annotations

import json
from typing import Any

from tools import TOOL_HANDLERS  # type: ignore[import]
from tools._helpers import text, get_client, PLANNER_URL, JOB_URL  # type: ignore[import]


# ── Planner Tasks ────────────────────────────────────────────────

async def _plan_create_task(args: dict[str, Any]) -> list[dict[str, Any]]:
    title = str(args.get("title", "")).strip()
    if not title:
        return text("plan_create_task: 'title' is required")
    body: dict[str, Any] = {
        "title":       title,
        "description": str(args.get("description", "")),
        "depends_on":  list(args.get("depends_on", [])),
        "priority":    int(args.get("priority", 0)),
        "metadata":    dict(args.get("metadata", {})),
    }
    if args.get("due_at"):
        body["due_at"] = str(args["due_at"])
    try:
        async with get_client() as c:
            r = await c.post(f"{PLANNER_URL}/tasks", json=body, timeout=10)
            r.raise_for_status()
            d = r.json()
    except Exception as exc:
        return text(f"plan_create_task: failed — {exc}")
    return text(
        f"Task created: id={d.get('id')} title={d.get('title')!r} "
        f"status={d.get('status')} priority={d.get('priority')} "
        f"depends_on={d.get('depends_on')}"
    )


async def _plan_get_task(args: dict[str, Any]) -> list[dict[str, Any]]:
    tid = str(args.get("id", "")).strip()
    if not tid:
        return text("plan_get_task: 'id' is required")
    try:
        async with get_client() as c:
            r = await c.get(f"{PLANNER_URL}/tasks/{tid}", timeout=10)
            if r.status_code == 404:
                return text(f"plan_get_task: task '{tid}' not found")
            r.raise_for_status()
            d = r.json()
    except Exception as exc:
        return text(f"plan_get_task: failed — {exc}")
    return text(
        f"Task {d['id']}: {d['title']!r}\n"
        f"  Status: {d['status']}  Priority: {d['priority']}\n"
        f"  Description: {d.get('description', '')}\n"
        f"  Depends on: {d.get('depends_on', [])}\n"
        f"  Created: {d.get('created_at', '')}  Updated: {d.get('updated_at', '')}"
    )


async def _plan_complete_task(args: dict[str, Any]) -> list[dict[str, Any]]:
    tid = str(args.get("id", "")).strip()
    if not tid:
        return text("plan_complete_task: 'id' is required")
    try:
        async with get_client() as c:
            r = await c.post(f"{PLANNER_URL}/tasks/{tid}/complete", timeout=10)
            if r.status_code == 404:
                return text(f"plan_complete_task: task '{tid}' not found")
            r.raise_for_status()
    except Exception as exc:
        return text(f"plan_complete_task: failed — {exc}")
    return text(f"Task {tid} marked as done.")


async def _plan_fail_task(args: dict[str, Any]) -> list[dict[str, Any]]:
    tid    = str(args.get("id", "")).strip()
    detail = str(args.get("detail", "")).strip()
    if not tid:
        return text("plan_fail_task: 'id' is required")
    try:
        async with get_client() as c:
            r = await c.post(
                f"{PLANNER_URL}/tasks/{tid}/fail",
                json={"detail": detail},
                timeout=10,
            )
            if r.status_code == 404:
                return text(f"plan_fail_task: task '{tid}' not found")
            r.raise_for_status()
    except Exception as exc:
        return text(f"plan_fail_task: failed — {exc}")
    return text(f"Task {tid} marked as failed. Reason: {detail or '(none)'}")


async def _plan_list_tasks(args: dict[str, Any]) -> list[dict[str, Any]]:
    status_filter = str(args.get("status", "")).strip()
    limit         = max(1, int(args.get("limit", 50)))
    try:
        params: dict[str, Any] = {"limit": limit}
        if status_filter:
            params["status"] = status_filter
        async with get_client() as c:
            r = await c.get(f"{PLANNER_URL}/tasks", params=params, timeout=10)
            r.raise_for_status()
            d = r.json()
    except Exception as exc:
        return text(f"plan_list_tasks: failed — {exc}")
    tasks = d.get("tasks", [])
    total = d.get("total", len(tasks))
    if not tasks:
        suffix = f" with status='{status_filter}'" if status_filter else ""
        return text(f"No tasks found{suffix}.")
    header = f"Tasks ({len(tasks)}/{total}"
    if status_filter:
        header += f", status={status_filter}"
    header += "):"
    lines = [header]
    for t in tasks:
        dep = f" [deps: {t['depends_on']}]" if t.get("depends_on") else ""
        lines.append(f"  [{t['status']}] {t['id']}: {t['title']!r}{dep}")
    return text("\n".join(lines))


async def _plan_delete_task(args: dict[str, Any]) -> list[dict[str, Any]]:
    tid = str(args.get("id", "")).strip()
    if not tid:
        return text("plan_delete_task: 'id' is required")
    try:
        async with get_client() as c:
            r = await c.delete(f"{PLANNER_URL}/tasks/{tid}", timeout=10)
            if r.status_code == 404:
                return text(f"plan_delete_task: task '{tid}' not found")
            r.raise_for_status()
    except Exception as exc:
        return text(f"plan_delete_task: failed — {exc}")
    return text(f"Task {tid} deleted.")


# ── Job Status/Result/List (read-only — no _execute_job dependency) ──

async def _job_status(args: dict[str, Any]) -> list[dict[str, Any]]:
    jid = str(args.get("job_id", "")).strip()
    if not jid:
        return text("job_status: 'job_id' is required")
    async with get_client() as c:
        r = await c.get(f"{JOB_URL}/{jid}", timeout=10)
        if r.status_code == 404:
            return text(f"job_status: job '{jid}' not found")
        if r.status_code >= 400:
            return text(f"job_status failed: {r.status_code} — {r.text[:300]}")
        d = r.json()
    return text(json.dumps({
        "job_id":       d["id"],
        "tool_name":    d["tool_name"],
        "status":       d["status"],
        "progress":     d["progress"],
        "submitted_at": d["submitted_at"],
        "started_at":   d["started_at"],
        "finished_at":  d["finished_at"],
        "error":        d["error"],
    }))


async def _job_result(args: dict[str, Any]) -> list[dict[str, Any]]:
    jid = str(args.get("job_id", "")).strip()
    if not jid:
        return text("job_result: 'job_id' is required")
    async with get_client() as c:
        r = await c.get(f"{JOB_URL}/{jid}", timeout=10)
        if r.status_code == 404:
            return text(f"job_result: job '{jid}' not found")
        d = r.json()
    if d["status"] not in ("succeeded", "failed", "cancelled"):
        return text(json.dumps({
            "job_id": jid, "status": d["status"],
            "message": "Job has not completed yet.",
        }))
    if d["status"] == "succeeded":
        return text(d.get("result") or "(empty result)")
    return text(json.dumps({
        "job_id": jid, "status": d["status"],
        "error": d.get("error", "unknown error"),
    }))


async def _job_list(args: dict[str, Any]) -> list[dict[str, Any]]:
    status_f = str(args.get("status",    "")).strip()
    tool_f   = str(args.get("tool_name", "")).strip()
    limit    = max(1, min(int(args.get("limit", 20)), 100))
    params: dict[str, Any] = {"limit": limit}
    if status_f:
        params["status"] = status_f
    if tool_f:
        params["tool_name"] = tool_f
    async with get_client() as c:
        r = await c.get(f"{JOB_URL}", params=params, timeout=10)
        if r.status_code >= 400:
            return text(f"job_list failed: {r.status_code} — {r.text[:300]}")
        d = r.json()
    jobs = d.get("jobs", [])
    lines = [f"Jobs ({d.get('total', 0)} total, showing {len(jobs)}):"]
    for j in jobs:
        lines.append(
            f"  {j['id']}  {j['tool_name']:<22}  {j['status']:<12}  "
            f"submitted={j['submitted_at'][:19]}"
        )
    return text("\n".join(lines))


# ── Think (no-op reasoning scratchpad) ───────────────────────────

async def _think(args: dict[str, Any]) -> list[dict[str, Any]]:
    thought = str(args.get("thought", "")).strip()
    return text(thought if thought else "(empty thought)")


# Register handlers
TOOL_HANDLERS["plan_create_task"]   = _plan_create_task
TOOL_HANDLERS["plan_get_task"]      = _plan_get_task
TOOL_HANDLERS["plan_complete_task"] = _plan_complete_task
TOOL_HANDLERS["plan_fail_task"]     = _plan_fail_task
TOOL_HANDLERS["plan_list_tasks"]    = _plan_list_tasks
TOOL_HANDLERS["plan_delete_task"]   = _plan_delete_task
TOOL_HANDLERS["job_status"]         = _job_status
TOOL_HANDLERS["job_result"]         = _job_result
TOOL_HANDLERS["job_list"]           = _job_list
TOOL_HANDLERS["think"]              = _think
