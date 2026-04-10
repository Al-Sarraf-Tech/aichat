"""Code modification handler — SSH -> Claude Code."""
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


async def handle_code(
    intent: Intent,
    chat_id: int,
    reply_to: int | None = None,
) -> None:
    repo = intent.repo
    if not repo or not _VALID_NAME.match(repo):
        await api.send_message(f"Invalid repo name: `{repo}`", reply_to=reply_to)
        return

    task_id = uuid.uuid4().hex[:8]
    ts = TaskState(task_id=task_id, description=intent.task, repo=repo)
    _active_tasks[task_id] = ts

    await db.save_task(task_id, chat_id, "code", intent.task, repo)
    await api.send_message(
        f"Starting task `{task_id}`: {intent.task} (repo: {repo})", reply_to=reply_to,
    )

    escaped = shlex.quote(intent.task)
    ssh_cmd = (
        f'cd "$HOME/git/{repo}" && '
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
            logger.error("Code task %s failed: %s", task_id, exc)
            await api.send_message(f"Task `{task_id}` failed: {exc}", reply_to=reply_to)
            await db.update_task(task_id, "failed", summary=str(exc))
        finally:
            _active_tasks.pop(task_id, None)

    task = asyncio.create_task(_run())
    ts.asyncio_task = task
