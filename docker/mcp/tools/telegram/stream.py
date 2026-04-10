"""Claude Code stream-json parser with milestone detection and heartbeat."""
from __future__ import annotations

import asyncio
import json
import logging
import re

from tools.telegram import api, config
from tools.telegram.models import TaskState
from tools.telegram.summary import build_summary

logger = logging.getLogger(__name__)

_READ_TOOLS = {"Read", "Glob", "Grep"}
_WRITE_TOOLS = {"Edit", "Write"}
_TEST_PATTERNS = re.compile(r"pytest|jest|cargo test|go test|npm test|make test", re.IGNORECASE)


async def stream_claude(
    ssh_command: str,
    reply_to: int | None,
    task_state: TaskState,
) -> str:
    ssh_full = (
        f"ssh -i {config.SSH_KEY} "
        f"-o StrictHostKeyChecking=accept-new "
        f"-o ConnectTimeout=10 "
        f"-o BatchMode=yes "
        f"-o ServerAliveInterval=30 "
        f"-o ServerAliveCountMax=3 "
        f"-p {config.SSH_PORT} "
        f"{config.SSH_USER}@{config.SSH_HOST} "
        f"{ssh_command}"
    )

    proc = await asyncio.create_subprocess_shell(
        ssh_full,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    task_state.process = proc

    edited_files: list[str] = []
    last_bash_output = ""
    final_text = ""
    sent_milestones: set[str] = set()

    last_activity = asyncio.get_event_loop().time()

    async def heartbeat():
        nonlocal last_activity
        while True:
            await asyncio.sleep(config.HEARTBEAT_INTERVAL)
            elapsed = asyncio.get_event_loop().time() - last_activity
            if elapsed >= config.HEARTBEAT_INTERVAL:
                await api.send_message(
                    f"Still working on: {task_state.description}...", reply_to=reply_to,
                )
                last_activity = asyncio.get_event_loop().time()

    heartbeat_task = asyncio.create_task(heartbeat())

    try:
        while True:
            try:
                line = await asyncio.wait_for(
                    proc.stdout.readline(), timeout=config.CLAUDE_TIMEOUT,
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                heartbeat_task.cancel()
                return f"Timed out after {config.CLAUDE_TIMEOUT}s"

            if not line:
                break

            last_activity = asyncio.get_event_loop().time()

            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            tool = event.get("tool", "")
            tool_input = event.get("tool_input", {})

            if tool in _READ_TOOLS and "reading" not in sent_milestones:
                sent_milestones.add("reading")
                await api.send_message("Reading codebase...", reply_to=reply_to)

            if tool in _WRITE_TOOLS and "writing" not in sent_milestones:
                sent_milestones.add("writing")
                await api.send_message("Writing code...", reply_to=reply_to)

            if tool in _WRITE_TOOLS:
                fp = tool_input.get("file_path", "")
                if fp:
                    edited_files.append(fp)

            if tool == "Bash":
                cmd = tool_input.get("command", "")
                if _TEST_PATTERNS.search(cmd) and "testing" not in sent_milestones:
                    sent_milestones.add("testing")
                    await api.send_message("Running tests...", reply_to=reply_to)
                if "git commit" in cmd and "committing" not in sent_milestones:
                    sent_milestones.add("committing")
                    await api.send_message("Committing...", reply_to=reply_to)

            if event.get("type") == "tool_result":
                content = event.get("content", [])
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            last_bash_output = block["text"]

            if event.get("type") in ("assistant", "result"):
                text = event.get("result", "") or event.get("content", "")
                if isinstance(text, str) and text:
                    final_text = text

    finally:
        heartbeat_task.cancel()
        try:
            await heartbeat_task
        except asyncio.CancelledError:
            pass

    await proc.wait()
    returncode = proc.returncode

    return build_summary(task_state, final_text, edited_files, last_bash_output, returncode)
