"""Task status listing."""
from __future__ import annotations

import time

from tools.telegram import api
from tools.telegram.models import TaskState

_active_tasks: dict[str, TaskState] = {}


async def handle_status(reply_to: int | None = None) -> None:
    if not _active_tasks:
        await api.send_message("No active tasks.", reply_to=reply_to)
        return

    lines = ["**Active tasks:**"]
    for tid, ts in _active_tasks.items():
        elapsed = int(time.monotonic() - ts.started_at)
        lines.append(f"  `{tid}` [{ts.status}] {elapsed}s — {ts.description}")
    await api.send_message("\n".join(lines), reply_to=reply_to)
