"""
Unit tests for tools/ssh.py — SSH MCP tool.

Test groups:
  - exec action (5 tests)
  - test action (2 tests)
  - list_hosts action (1 test)
  - upload path restriction (2 tests)
  - download path restriction (1 test)
  - unknown / missing action (2 tests)

Run with:
  cd ~/git/aichat
  python -m pytest tests/tools/test_ssh_tool.py -v
"""
from __future__ import annotations

import json

import pytest

from tools._ssh import SSHResult  # type: ignore[import]


# ===========================================================================
# exec — 5 tests
# ===========================================================================

class TestExecAction:
    """handle() with action='exec'."""

    @pytest.mark.asyncio
    async def test_exec_returns_stdout(self, mock_ssh):
        """exec with a successful command must include stdout in the result text."""
        from tools.ssh import handle  # type: ignore[import]

        mock_ssh.run.return_value = SSHResult(
            stdout="hello\n", stderr="", returncode=0, host="amarillo", elapsed=0.1
        )
        result = await handle({"action": "exec", "host": "amarillo", "command": "echo hello"}, mock_ssh)

        assert isinstance(result, list)
        assert result[0]["type"] == "text"
        assert "hello" in result[0]["text"]

    @pytest.mark.asyncio
    async def test_exec_includes_exit_code(self, mock_ssh):
        """exec result text must contain the returncode."""
        from tools.ssh import handle  # type: ignore[import]

        mock_ssh.run.return_value = SSHResult(
            stdout="", stderr="not found", returncode=127, host="amarillo", elapsed=0.2
        )
        result = await handle({"action": "exec", "host": "amarillo", "command": "badcmd"}, mock_ssh)

        assert "127" in result[0]["text"]

    @pytest.mark.asyncio
    async def test_exec_includes_stderr(self, mock_ssh):
        """exec result text must contain stderr when the command emits it."""
        from tools.ssh import handle  # type: ignore[import]

        mock_ssh.run.return_value = SSHResult(
            stdout="", stderr="permission denied", returncode=1, host="amarillo", elapsed=0.1
        )
        result = await handle({"action": "exec", "host": "amarillo", "command": "cat /etc/shadow"}, mock_ssh)

        assert "permission denied" in result[0]["text"]

    @pytest.mark.asyncio
    async def test_exec_missing_host_returns_error(self, mock_ssh):
        """exec without 'host' must return an error without calling ssh.run."""
        from tools.ssh import handle  # type: ignore[import]

        result = await handle({"action": "exec", "command": "id"}, mock_ssh)

        assert result[0]["type"] == "text"
        assert "host" in result[0]["text"].lower()
        mock_ssh.run.assert_not_called()

    @pytest.mark.asyncio
    async def test_exec_missing_command_returns_error(self, mock_ssh):
        """exec without 'command' must return an error without calling ssh.run."""
        from tools.ssh import handle  # type: ignore[import]

        result = await handle({"action": "exec", "host": "amarillo"}, mock_ssh)

        assert result[0]["type"] == "text"
        assert "command" in result[0]["text"].lower()
        mock_ssh.run.assert_not_called()


# ===========================================================================
# test action — 2 tests
# ===========================================================================

class TestTestAction:
    """handle() with action='test'."""

    @pytest.mark.asyncio
    async def test_test_reachable(self, mock_ssh):
        """test action must report reachable when ssh.run returns returncode=0."""
        from tools.ssh import handle  # type: ignore[import]

        mock_ssh.run.return_value = SSHResult(
            stdout="ok", stderr="", returncode=0, host="amarillo", elapsed=0.05
        )
        result = await handle({"action": "test", "host": "amarillo"}, mock_ssh)

        assert result[0]["type"] == "text"
        assert "reachable" in result[0]["text"].lower()

    @pytest.mark.asyncio
    async def test_test_unreachable(self, mock_ssh):
        """test action must report unreachable when ssh.run returns non-zero returncode."""
        from tools.ssh import handle  # type: ignore[import]

        mock_ssh.run.return_value = SSHResult(
            stdout="", stderr="Connection refused", returncode=255, host="dominus", elapsed=5.0
        )
        result = await handle({"action": "test", "host": "dominus"}, mock_ssh)

        assert result[0]["type"] == "text"
        assert "unreachable" in result[0]["text"].lower()


# ===========================================================================
# list_hosts action — 1 test
# ===========================================================================

class TestListHostsAction:
    """handle() with action='list_hosts'."""

    @pytest.mark.asyncio
    async def test_list_hosts_returns_text(self, mock_ssh):
        """list_hosts must return a text result containing host information."""
        from tools.ssh import handle  # type: ignore[import]

        tailscale_json = json.dumps({
            "Self": {
                "HostName": "amarillo",
                "TailscaleIPs": ["100.64.0.1"],
                "Online": True,
            },
            "Peers": {
                "peer1": {
                    "HostName": "dominus",
                    "TailscaleIPs": ["100.64.0.2"],
                    "Online": True,
                },
            },
        })
        mock_ssh.run.return_value = SSHResult(
            stdout=tailscale_json, stderr="", returncode=0, host="amarillo", elapsed=0.1
        )
        result = await handle({"action": "list_hosts"}, mock_ssh)

        assert isinstance(result, list)
        assert result[0]["type"] == "text"
        assert len(result[0]["text"]) > 0


# ===========================================================================
# upload path restriction — 2 tests
# ===========================================================================

class TestUploadPathRestriction:
    """handle() with action='upload' — path must start with /workspace/."""

    @pytest.mark.asyncio
    async def test_upload_valid_path_calls_ssh(self, mock_ssh):
        """upload with local_path under /workspace/ must proceed and call ssh.run."""
        from tools.ssh import handle  # type: ignore[import]

        mock_ssh.run.return_value = SSHResult(
            stdout="", stderr="", returncode=0, host="amarillo", elapsed=0.3
        )
        result = await handle(
            {
                "action": "upload",
                "host": "amarillo",
                "local_path": "/workspace/data.csv",
                "remote_path": "/tmp/data.csv",
            },
            mock_ssh,
        )

        assert result[0]["type"] == "text"
        mock_ssh.run.assert_called_once()

    @pytest.mark.asyncio
    async def test_upload_invalid_path_returns_error(self, mock_ssh):
        """upload with local_path outside /workspace/ must return an error."""
        from tools.ssh import handle  # type: ignore[import]

        result = await handle(
            {
                "action": "upload",
                "host": "amarillo",
                "local_path": "/etc/passwd",
                "remote_path": "/tmp/passwd",
            },
            mock_ssh,
        )

        assert result[0]["type"] == "text"
        assert "/workspace/" in result[0]["text"] or "workspace" in result[0]["text"].lower()
        mock_ssh.run.assert_not_called()


# ===========================================================================
# download path restriction — 1 test
# ===========================================================================

class TestDownloadPathRestriction:
    """handle() with action='download' — path must start with /workspace/."""

    @pytest.mark.asyncio
    async def test_download_invalid_path_returns_error(self, mock_ssh):
        """download with local_path outside /workspace/ must return an error."""
        from tools.ssh import handle  # type: ignore[import]

        result = await handle(
            {
                "action": "download",
                "host": "amarillo",
                "remote_path": "/var/log/syslog",
                "local_path": "/tmp/syslog",
            },
            mock_ssh,
        )

        assert result[0]["type"] == "text"
        assert "/workspace/" in result[0]["text"] or "workspace" in result[0]["text"].lower()
        mock_ssh.run.assert_not_called()


# ===========================================================================
# unknown / missing action — 2 tests
# ===========================================================================

class TestUnknownMissingAction:
    """handle() edge cases for missing or unrecognised action."""

    @pytest.mark.asyncio
    async def test_unknown_action_returns_error(self, mock_ssh):
        """An unrecognised action must return a descriptive error message."""
        from tools.ssh import handle  # type: ignore[import]

        result = await handle({"action": "fly_to_moon"}, mock_ssh)

        assert result[0]["type"] == "text"
        assert "fly_to_moon" in result[0]["text"]

    @pytest.mark.asyncio
    async def test_missing_action_returns_error(self, mock_ssh):
        """Omitting the action key entirely must return an error."""
        from tools.ssh import handle  # type: ignore[import]

        result = await handle({}, mock_ssh)

        assert result[0]["type"] == "text"
        assert "action" in result[0]["text"].lower()
