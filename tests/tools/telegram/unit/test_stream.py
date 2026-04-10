"""Tests for telegram.stream — Claude streaming, milestones, heartbeat."""
from __future__ import annotations

import asyncio
import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tools.telegram.models import TaskState

ENV = {"TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_CHAT_ID": "1", "POSTGRES_PASSWORD": "pw"}


def _stream_line(event: dict) -> bytes:
    return json.dumps(event).encode() + b"\n"


@pytest.mark.asyncio
class TestStreamClaude:
    async def test_detects_read_milestone(self):
        lines = [
            _stream_line({"type": "tool_use", "tool": "Read", "tool_input": {"file_path": "/app/main.py"}}),
            _stream_line({"type": "result", "result": "done"}),
        ]

        mock_proc = AsyncMock()
        mock_proc.stdout = AsyncMock()
        mock_proc.stdout.readline = AsyncMock(side_effect=lines + [b""])
        mock_proc.wait = AsyncMock(return_value=0)
        mock_proc.returncode = 0

        ts = TaskState(task_id="t1", description="test", repo="test")
        ts.process = mock_proc

        with patch.dict(os.environ, ENV, clear=False):
            from tools.telegram import config
            import importlib
            importlib.reload(config)
            from tools.telegram.stream import stream_claude
            with patch("tools.telegram.stream.api.send_message", new_callable=AsyncMock) as mock_send, \
                 patch("tools.telegram.stream.asyncio.create_subprocess_shell", return_value=mock_proc):
                result = await stream_claude("echo test", reply_to=1, task_state=ts)

        calls = [c[0][0] for c in mock_send.call_args_list]
        assert any("Reading" in c for c in calls)

    async def test_detects_write_milestone(self):
        lines = [
            _stream_line({"type": "tool_use", "tool": "Edit", "tool_input": {"file_path": "/app/main.py"}}),
            _stream_line({"type": "result", "result": "done"}),
        ]

        mock_proc = AsyncMock()
        mock_proc.stdout = AsyncMock()
        mock_proc.stdout.readline = AsyncMock(side_effect=lines + [b""])
        mock_proc.wait = AsyncMock(return_value=0)
        mock_proc.returncode = 0

        ts = TaskState(task_id="t1", description="test", repo="test")
        ts.process = mock_proc

        with patch.dict(os.environ, ENV, clear=False):
            from tools.telegram import config
            import importlib
            importlib.reload(config)
            from tools.telegram.stream import stream_claude
            with patch("tools.telegram.stream.api.send_message", new_callable=AsyncMock) as mock_send, \
                 patch("tools.telegram.stream.asyncio.create_subprocess_shell", return_value=mock_proc):
                result = await stream_claude("echo test", reply_to=1, task_state=ts)

        calls = [c[0][0] for c in mock_send.call_args_list]
        assert any("Writing" in c for c in calls)

    async def test_extracts_edited_files(self):
        lines = [
            _stream_line({"type": "tool_use", "tool": "Edit", "tool_input": {"file_path": "/app/a.py"}}),
            _stream_line({"type": "tool_use", "tool": "Write", "tool_input": {"file_path": "/app/b.py"}}),
            _stream_line({"type": "result", "result": "done"}),
        ]

        mock_proc = AsyncMock()
        mock_proc.stdout = AsyncMock()
        mock_proc.stdout.readline = AsyncMock(side_effect=lines + [b""])
        mock_proc.wait = AsyncMock(return_value=0)
        mock_proc.returncode = 0

        ts = TaskState(task_id="t1", description="test", repo="test")
        ts.process = mock_proc

        with patch.dict(os.environ, ENV, clear=False):
            from tools.telegram import config
            import importlib
            importlib.reload(config)
            from tools.telegram.stream import stream_claude
            with patch("tools.telegram.stream.api.send_message", new_callable=AsyncMock), \
                 patch("tools.telegram.stream.asyncio.create_subprocess_shell", return_value=mock_proc):
                result = await stream_claude("echo test", reply_to=1, task_state=ts)

        assert "a.py" in result
        assert "b.py" in result

    async def test_timeout_kills_process(self):
        async def slow_readline():
            await asyncio.sleep(100)
            return b""

        mock_proc = AsyncMock()
        mock_proc.stdout = AsyncMock()
        mock_proc.stdout.readline = slow_readline
        mock_proc.kill = MagicMock()
        mock_proc.wait = AsyncMock(return_value=-9)
        mock_proc.returncode = -9

        ts = TaskState(task_id="t1", description="test", repo="test")
        ts.process = mock_proc

        with patch.dict(os.environ, {**ENV}, clear=False):
            from tools.telegram import config
            import importlib
            importlib.reload(config)
            with patch("tools.telegram.stream.config.CLAUDE_TIMEOUT", 0.1):
                from tools.telegram.stream import stream_claude
                with patch("tools.telegram.stream.api.send_message", new_callable=AsyncMock), \
                     patch("tools.telegram.stream.asyncio.create_subprocess_shell", return_value=mock_proc):
                    result = await stream_claude("echo test", reply_to=1, task_state=ts)

        assert "timed out" in result.lower() or "Timed out" in result

    async def test_commit_sha_extracted(self):
        lines = [
            _stream_line({"type": "tool_use", "tool": "Bash", "tool_input": {"command": "git commit -m 'fix'"}}),
            _stream_line({"type": "tool_result", "content": [{"type": "text", "text": "[main abc1234] fix: stuff\n 1 file changed"}]}),
            _stream_line({"type": "result", "result": "done"}),
        ]

        mock_proc = AsyncMock()
        mock_proc.stdout = AsyncMock()
        mock_proc.stdout.readline = AsyncMock(side_effect=lines + [b""])
        mock_proc.wait = AsyncMock(return_value=0)
        mock_proc.returncode = 0

        ts = TaskState(task_id="t1", description="test", repo="test")
        ts.process = mock_proc

        with patch.dict(os.environ, ENV, clear=False):
            from tools.telegram import config
            import importlib
            importlib.reload(config)
            from tools.telegram.stream import stream_claude
            with patch("tools.telegram.stream.api.send_message", new_callable=AsyncMock), \
                 patch("tools.telegram.stream.asyncio.create_subprocess_shell", return_value=mock_proc):
                result = await stream_claude("echo test", reply_to=1, task_state=ts)

        assert "abc1234" in result

    async def test_deduplicates_edited_files(self):
        lines = [
            _stream_line({"type": "tool_use", "tool": "Edit", "tool_input": {"file_path": "/app/main.py"}}),
            _stream_line({"type": "tool_use", "tool": "Edit", "tool_input": {"file_path": "/app/main.py"}}),
            _stream_line({"type": "result", "result": "done"}),
        ]

        mock_proc = AsyncMock()
        mock_proc.stdout = AsyncMock()
        mock_proc.stdout.readline = AsyncMock(side_effect=lines + [b""])
        mock_proc.wait = AsyncMock(return_value=0)
        mock_proc.returncode = 0

        ts = TaskState(task_id="t1", description="test", repo="test")
        ts.process = mock_proc

        with patch.dict(os.environ, ENV, clear=False):
            from tools.telegram import config
            import importlib
            importlib.reload(config)
            from tools.telegram.stream import stream_claude
            with patch("tools.telegram.stream.api.send_message", new_callable=AsyncMock), \
                 patch("tools.telegram.stream.asyncio.create_subprocess_shell", return_value=mock_proc):
                result = await stream_claude("echo test", reply_to=1, task_state=ts)

        assert result.count("main.py") == 1
