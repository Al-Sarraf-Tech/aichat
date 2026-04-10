"""Task cancellation."""
from __future__ import annotations

import logging

from tools.telegram import api, db
from tools.telegram.handlers.status import _active_tasks

logger = logging.getLogger(__name__)


async def handle_cancel(reply_to: int | None = None) -> None:
    if not _active_tasks:
        await api.send_message("Nothing to cancel.", reply_to=reply_to)
        return

    tid = max(_active_tasks, key=lambda k: _active_tasks[k].started_at)
    ts = _active_tasks.pop(tid)

    if ts.process:
        try:
            ts.process.kill()
        except Exception:
            logger.warning("Failed to kill process for task %s", tid)

    if ts.asyncio_task:
        ts.asyncio_task.cancel()

    await db.update_task(tid, status="cancelled", summary="Cancelled by user")
    await api.send_message(f"Cancelled task `{tid}`: {ts.description}", reply_to=reply_to)
