"""Project scaffolding handler — SSH -> Claude Code."""
from __future__ import annotations

import asyncio
import logging
import re
import shlex
import uuid

from tools.telegram import api, config, db
from tools.telegram.handlers.status import _active_tasks
from tools.telegram.models import Intent, TaskState
from tools.telegram.stream import stream_claude

logger = logging.getLogger(__name__)

_VALID_NAME = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$")


async def handle_create(
    intent: Intent,
    chat_id: int,
    reply_to: int | None = None,
) -> None:
    name = intent.name
    if not name or not _VALID_NAME.match(name):
        await api.send_message(f"Invalid project name: `{name}`", reply_to=reply_to)
        return

    task_id = uuid.uuid4().hex[:8]
    desc = f"Create {intent.language} project: {intent.description}"
    ts = TaskState(task_id=task_id, description=desc, repo=name)
    _active_tasks[task_id] = ts

    await db.save_task(task_id, chat_id, "create", desc, name)
    await api.send_message(
        f"Creating project: {name} ({intent.language})", reply_to=reply_to,
    )

    prompt = (
        f"Create a new {intent.language} project: {intent.description}. "
        f"Set up project structure, CLAUDE.md (inheriting from ~/.claude/CLAUDE.md conventions), "
        f"CI workflow for GitHub Actions (self-hosted runners, no attest-build-provenance, no macOS targets), "
        f"README, and initial source files. "
        f"Initialize git and make the first commit."
    )
    escaped = shlex.quote(prompt)
    ssh_cmd = (
        f'mkdir -p "$HOME/git/{name}" && cd "$HOME/git/{name}" && git init 2>/dev/null; '
        f"claude --output-format stream-json --dangerously-skip-permissions -p {escaped}"
    )

    async def _run():
        try:
            summary = await stream_claude(ssh_cmd, reply_to, ts)
            ts.status = "done"
            await api.send_message(summary, reply_to=reply_to)
            await db.update_task(task_id, "done", summary=summary)
        except asyncio.CancelledError:
            ts.status = "cancelled"
            await api.send_message(f"Task `{task_id}` cancelled.", reply_to=reply_to)
            await db.update_task(task_id, "cancelled", summary="Cancelled")
        except Exception as exc:
            ts.status = "failed"
            logger.error("Create task %s failed: %s", task_id, exc)
            await api.send_message(f"Task `{task_id}` failed: {exc}", reply_to=reply_to)
            await db.update_task(task_id, "failed", summary=str(exc))
        finally:
            _active_tasks.pop(task_id, None)

    task = asyncio.create_task(_run())
    ts.asyncio_task = task
