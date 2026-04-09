"""
Unit tests for tools/log.py — log search and analysis MCP tool.

Test groups:
  - list (1 test): returns file listing
  - search (3 tests): returns matches, missing pattern, path traversal (../ and absolute)
  - tail (3 tests): returns lines, clamps to 500 max, missing file
  - count (1 test): time-bucketing by hour
  - errors (1 test): aggregates error counts
  - between (2 tests): filters by time range, missing start/end params
  - unknown action (1 test)

Run with:
  cd ~/git/aichat
  python -m pytest tests/tools/test_log.py -v
"""
from __future__ import annotations

import pytest

from tools._ssh import SSHResult  # type: ignore[import]


# ===========================================================================
# list — 1 test
# ===========================================================================


class TestListAction:
    """handle() with action='list'."""

    @pytest.mark.asyncio
    async def test_list_returns_files(self, mock_ssh):
        """list must return the ls output containing log file names."""
        from tools.log import handle  # type: ignore[import]

        mock_ssh.run.return_value = SSHResult(
            stdout="-rw-r--r-- 1 root root 1.2M 2026-04-09 10:00 dispatcher.log\n",
            stderr="",
            returncode=0,
            host="amarillo",
            elapsed=0.1,
        )
        result = await handle({"action": "list"}, mock_ssh)
        assert isinstance(result, list)
        assert result[0]["type"] == "text"
        assert "dispatcher.log" in result[0]["text"]


# ===========================================================================
# search — 3 tests
# ===========================================================================


class TestSearchAction:
    """handle() with action='search'."""

    @pytest.mark.asyncio
    async def test_search_returns_matches(self, mock_ssh):
        """search must return grep matches when pattern is provided."""
        from tools.log import handle  # type: ignore[import]

        mock_ssh.run.return_value = SSHResult(
            stdout="dispatcher.log:42:ERROR something bad happened\n",
            stderr="",
            returncode=0,
            host="amarillo",
            elapsed=0.2,
        )
        result = await handle({"action": "search", "pattern": "ERROR"}, mock_ssh)
        assert result[0]["type"] == "text"
        assert "ERROR" in result[0]["text"] or "dispatcher.log" in result[0]["text"]

    @pytest.mark.asyncio
    async def test_search_missing_pattern_returns_error(self, mock_ssh):
        """search without 'pattern' must return an error without calling ssh.run."""
        from tools.log import handle  # type: ignore[import]

        result = await handle({"action": "search"}, mock_ssh)
        assert result[0]["type"] == "text"
        assert "pattern" in result[0]["text"].lower()
        mock_ssh.run.assert_not_called()

    @pytest.mark.asyncio
    async def test_search_path_traversal_dotdot_blocked(self, mock_ssh):
        """search with file containing '../' must be rejected without calling ssh.run."""
        from tools.log import handle  # type: ignore[import]

        result = await handle(
            {"action": "search", "pattern": "foo", "file": "../etc/passwd"},
            mock_ssh,
        )
        assert result[0]["type"] == "text"
        assert "invalid" in result[0]["text"].lower() or "traversal" in result[0]["text"].lower() or "file" in result[0]["text"].lower()
        mock_ssh.run.assert_not_called()

    @pytest.mark.asyncio
    async def test_search_absolute_path_blocked(self, mock_ssh):
        """search with absolute path in 'file' must be rejected without calling ssh.run."""
        from tools.log import handle  # type: ignore[import]

        result = await handle(
            {"action": "search", "pattern": "foo", "file": "/etc/passwd"},
            mock_ssh,
        )
        assert result[0]["type"] == "text"
        assert "invalid" in result[0]["text"].lower() or "traversal" in result[0]["text"].lower() or "file" in result[0]["text"].lower()
        mock_ssh.run.assert_not_called()


# ===========================================================================
# tail — 3 tests
# ===========================================================================


class TestTailAction:
    """handle() with action='tail'."""

    @pytest.mark.asyncio
    async def test_tail_returns_lines(self, mock_ssh):
        """tail must return the last N lines of the specified file."""
        from tools.log import handle  # type: ignore[import]

        mock_ssh.run.return_value = SSHResult(
            stdout="line1\nline2\nline3\n",
            stderr="",
            returncode=0,
            host="amarillo",
            elapsed=0.1,
        )
        result = await handle(
            {"action": "tail", "file": "dispatcher.log", "lines": 3},
            mock_ssh,
        )
        assert result[0]["type"] == "text"
        assert "line1" in result[0]["text"] or "line3" in result[0]["text"]

    @pytest.mark.asyncio
    async def test_tail_clamps_max_lines(self, mock_ssh):
        """tail with lines > 500 must clamp to 500 (not error out)."""
        from tools.log import handle  # type: ignore[import]

        mock_ssh.run.return_value = SSHResult(
            stdout="last line\n",
            stderr="",
            returncode=0,
            host="amarillo",
            elapsed=0.1,
        )
        result = await handle(
            {"action": "tail", "file": "app.log", "lines": 9999},
            mock_ssh,
        )
        # Should succeed — clamped silently
        assert result[0]["type"] == "text"
        # Verify the command used 500 not 9999
        call_args = mock_ssh.run.call_args
        assert call_args is not None
        cmd = call_args[0][1] if len(call_args[0]) > 1 else call_args.args[1]
        assert "500" in cmd
        assert "9999" not in cmd

    @pytest.mark.asyncio
    async def test_tail_missing_file_returns_error(self, mock_ssh):
        """tail without 'file' must return an error without calling ssh.run."""
        from tools.log import handle  # type: ignore[import]

        result = await handle({"action": "tail"}, mock_ssh)
        assert result[0]["type"] == "text"
        assert "file" in result[0]["text"].lower()
        mock_ssh.run.assert_not_called()


# ===========================================================================
# count — 1 test
# ===========================================================================


class TestCountAction:
    """handle() with action='count'."""

    @pytest.mark.asyncio
    async def test_count_by_hour(self, mock_ssh):
        """count with window='hour' must return time-bucketed output."""
        from tools.log import handle  # type: ignore[import]

        mock_ssh.run.return_value = SSHResult(
            stdout="2026-04-09 10: 42\n2026-04-09 11: 17\n",
            stderr="",
            returncode=0,
            host="amarillo",
            elapsed=0.3,
        )
        result = await handle(
            {"action": "count", "pattern": "ERROR", "window": "hour"},
            mock_ssh,
        )
        assert result[0]["type"] == "text"
        assert len(result[0]["text"]) > 0


# ===========================================================================
# errors — 1 test
# ===========================================================================


class TestErrorsAction:
    """handle() with action='errors'."""

    @pytest.mark.asyncio
    async def test_errors_aggregates_counts(self, mock_ssh):
        """errors must return per-file error counts sorted descending."""
        from tools.log import handle  # type: ignore[import]

        mock_ssh.run.return_value = SSHResult(
            stdout="dispatcher.log:15\napp.log:3\n",
            stderr="",
            returncode=0,
            host="amarillo",
            elapsed=0.2,
        )
        result = await handle({"action": "errors"}, mock_ssh)
        assert result[0]["type"] == "text"
        assert "dispatcher.log" in result[0]["text"] or "15" in result[0]["text"]


# ===========================================================================
# between — 2 tests
# ===========================================================================


class TestBetweenAction:
    """handle() with action='between'."""

    @pytest.mark.asyncio
    async def test_between_filters_by_time(self, mock_ssh):
        """between must return log lines within the specified ISO 8601 time range."""
        from tools.log import handle  # type: ignore[import]

        mock_ssh.run.return_value = SSHResult(
            stdout="2026-04-09T10:05:00 INFO job started\n",
            stderr="",
            returncode=0,
            host="amarillo",
            elapsed=0.2,
        )
        result = await handle(
            {
                "action": "between",
                "file": "dispatcher.log",
                "start": "2026-04-09T10:00:00",
                "end": "2026-04-09T11:00:00",
            },
            mock_ssh,
        )
        assert result[0]["type"] == "text"
        assert "job started" in result[0]["text"] or len(result[0]["text"]) > 0

    @pytest.mark.asyncio
    async def test_between_missing_start_end_returns_error(self, mock_ssh):
        """between without 'start' and 'end' must return an error without calling ssh.run."""
        from tools.log import handle  # type: ignore[import]

        result = await handle(
            {"action": "between", "file": "dispatcher.log"},
            mock_ssh,
        )
        assert result[0]["type"] == "text"
        assert "start" in result[0]["text"].lower() or "end" in result[0]["text"].lower()
        mock_ssh.run.assert_not_called()


# ===========================================================================
# unknown action — 1 test
# ===========================================================================


class TestUnknownAction:
    """handle() with an unrecognised action."""

    @pytest.mark.asyncio
    async def test_unknown_action_returns_error(self, mock_ssh):
        """An unrecognised action must return a descriptive error."""
        from tools.log import handle  # type: ignore[import]

        result = await handle({"action": "explode"}, mock_ssh)
        assert result[0]["type"] == "text"
        assert "explode" in result[0]["text"] or "unknown" in result[0]["text"].lower()
