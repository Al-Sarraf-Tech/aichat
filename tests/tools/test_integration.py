"""
Integration tests for the 6 new MCP tools.

Requires a live SSH tunnel and (optionally) a running Docker stack.
Tests are skipped automatically when the target is unreachable.

Run:
    pytest tests/tools/test_integration.py -v -m integration
"""
from __future__ import annotations

import os
import socket
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Reachability probes — run once at collection time
# ---------------------------------------------------------------------------

SSH_HOST: str = os.environ.get("TEAM_SSH_HOST", "host.docker.internal")
SSH_PORT: int = int(os.environ.get("TEAM_SSH_PORT", "1337"))

SSH_REACHABLE: bool = False
try:
    _s = socket.create_connection((SSH_HOST, SSH_PORT), timeout=3)
    _s.close()
    SSH_REACHABLE = True
except Exception:
    pass

ROKU_HOST: str = "192.168.50.13"
ROKU_PORT: int = 8060

ROKU_REACHABLE: bool = False
try:
    _r = socket.create_connection((ROKU_HOST, ROKU_PORT), timeout=3)
    _r.close()
    ROKU_REACHABLE = True
except Exception:
    pass

HAS_TELEGRAM: bool = bool(
    os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID")
)

# ---------------------------------------------------------------------------
# Skip markers
# ---------------------------------------------------------------------------

skip_no_ssh = pytest.mark.skipif(
    not SSH_REACHABLE,
    reason=f"SSH tunnel not reachable at {SSH_HOST}:{SSH_PORT}",
)

skip_no_roku = pytest.mark.skipif(
    not ROKU_REACHABLE,
    reason=f"Roku ECP not reachable at {ROKU_HOST}:{ROKU_PORT}",
)

skip_no_telegram = pytest.mark.skipif(
    not HAS_TELEGRAM,
    reason="TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID env vars not set",
)

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _first_text(result: list[dict[str, Any]]) -> str:
    """Return the text of the first content block in an MCP result."""
    for block in result:
        if block.get("type") == "text":
            return block.get("text", "")
    return ""


# ---------------------------------------------------------------------------
# SSH tool
# ---------------------------------------------------------------------------

@skip_no_ssh
class TestSSHIntegration:
    """Integration tests for the ssh tool against a live SSH tunnel."""

    @pytest.mark.asyncio
    async def test_exec_echo(self) -> None:
        """ssh exec: run echo on amarillo and confirm 'hello' in output."""
        from tools.ssh import handle  # type: ignore[import]
        result = await handle({"action": "exec", "host": "amarillo", "command": "echo hello"})
        text = _first_text(result)
        assert "hello" in text, f"Expected 'hello' in output, got: {text!r}"

    @pytest.mark.asyncio
    async def test_connectivity(self) -> None:
        """ssh test: probe amarillo for reachability."""
        from tools.ssh import handle  # type: ignore[import]
        result = await handle({"action": "test", "host": "amarillo"})
        text = _first_text(result)
        assert "reachable" in text.lower(), f"Expected reachable in output, got: {text!r}"


# ---------------------------------------------------------------------------
# Monitor tool
# ---------------------------------------------------------------------------

@skip_no_ssh
class TestMonitorIntegration:
    """Integration tests for the monitor tool."""

    @pytest.mark.asyncio
    async def test_thermals(self) -> None:
        """monitor thermals: returns a section header."""
        from tools.monitor import handle  # type: ignore[import]
        result = await handle({"action": "thermals"})
        text = _first_text(result)
        assert "Thermals" in text, f"Expected 'Thermals' header, got: {text!r}"

    @pytest.mark.asyncio
    async def test_containers(self) -> None:
        """monitor containers: returns a containers section for amarillo."""
        from tools.monitor import handle  # type: ignore[import]
        result = await handle({"action": "containers", "host": "amarillo"})
        text = _first_text(result)
        assert "Containers" in text, f"Expected 'Containers' header, got: {text!r}"

    @pytest.mark.asyncio
    async def test_tailscale(self) -> None:
        """monitor tailscale: returns a tailscale section."""
        from tools.monitor import handle  # type: ignore[import]
        result = await handle({"action": "tailscale"})
        text = _first_text(result)
        assert "Tailscale" in text, f"Expected 'Tailscale' header, got: {text!r}"


# ---------------------------------------------------------------------------
# Git tool
# ---------------------------------------------------------------------------

@skip_no_ssh
class TestGitIntegration:
    """Integration tests for the git tool."""

    @pytest.mark.asyncio
    async def test_status_aichat(self) -> None:
        """git status: returns status output for the aichat repo."""
        from tools.git import handle  # type: ignore[import]
        result = await handle({"action": "status", "repo": "aichat"})
        text = _first_text(result)
        # Should either show branch info, clean status, or error; never empty
        assert text.strip(), "Expected non-empty output from git status"

    @pytest.mark.asyncio
    async def test_log_aichat(self) -> None:
        """git log: returns recent commits for the aichat repo."""
        from tools.git import handle  # type: ignore[import]
        result = await handle({"action": "log", "repo": "aichat", "limit": 3})
        text = _first_text(result)
        assert text.strip(), "Expected non-empty output from git log"

    @pytest.mark.asyncio
    async def test_scorecard(self) -> None:
        """git scorecard: returns CI health overview across repos."""
        from tools.git import handle  # type: ignore[import]
        result = await handle({"action": "scorecard"})
        text = _first_text(result)
        assert text.strip(), "Expected non-empty output from git scorecard"


# ---------------------------------------------------------------------------
# Notify tool
# ---------------------------------------------------------------------------

@skip_no_telegram
class TestNotifyIntegration:
    """Integration tests for the notify tool (requires Telegram credentials)."""

    @pytest.mark.asyncio
    async def test_send_message(self) -> None:
        """notify send: send a test message via Telegram."""
        from tools.notify import handle  # type: ignore[import]
        result = await handle({
            "action": "send",
            "text": "[aichat integration test] notify tool test message — ignore",
        })
        text = _first_text(result)
        assert "sent" in text.lower() or "ok" in text.lower() or "200" in text, (
            f"Expected success indicator in notify output, got: {text!r}"
        )


# ---------------------------------------------------------------------------
# Log tool
# ---------------------------------------------------------------------------

@skip_no_ssh
class TestLogIntegration:
    """Integration tests for the log tool."""

    @pytest.mark.asyncio
    async def test_list_files(self) -> None:
        """log list: returns a list of log files from amarillo."""
        from tools.log import handle  # type: ignore[import]
        result = await handle({"action": "list"})
        text = _first_text(result)
        assert text.strip(), "Expected non-empty output from log list"


# ---------------------------------------------------------------------------
# IoT tool
# ---------------------------------------------------------------------------

@skip_no_roku
class TestIoTIntegration:
    """Integration tests for the iot tool (Roku ECP)."""

    @pytest.mark.asyncio
    async def test_roku_query(self) -> None:
        """iot query: query Roku device state."""
        from tools.iot import handle  # type: ignore[import]
        result = await handle({"action": "query", "device": "roku"})
        text = _first_text(result)
        assert text.strip(), "Expected non-empty output from iot query"
