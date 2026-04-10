"""Build human-readable task summaries for Telegram."""
from __future__ import annotations

import os
import re
import time

from tools.telegram.models import TaskState


def build_summary(
    task_state: TaskState,
    final_text: str,
    edited_files: list[str],
    last_bash_output: str,
    returncode: int | None,
) -> str:
    elapsed = int(time.monotonic() - task_state.started_at)
    status = "Done" if returncode == 0 else f"Failed (exit {returncode})"
    repo = task_state.repo or "unknown"

    lines = [f"*{status}* — {repo} ({elapsed}s)"]

    desc = final_text or task_state.description
    if len(desc) > 300:
        desc = desc[:297] + "..."
    lines.append(f"\n{desc}")

    if edited_files:
        basenames = sorted(set(os.path.basename(f) for f in edited_files))
        lines.append(f"\nFiles: {', '.join(basenames)}")

    sha_match = re.search(r"\[.+?\s+([0-9a-f]{7,})\]", last_bash_output)
    if sha_match:
        lines.append(f"Commit: `{sha_match.group(1)}`")

    return "\n".join(lines)
