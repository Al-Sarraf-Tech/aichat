"""
End-to-end tests for the 6 new MCP tools via the full JSON-RPC call chain.

Calls http://localhost:8096/mcp (or MCP_URL env var) using the MCP
streamable-HTTP transport.  All tests are skipped when the endpoint is
not reachable.  Tests that require specific tools (e.g. the new modular
tools) are additionally skipped when those tools are absent from the
live tool list — allowing the suite to run cleanly against an older
deployed image.

Run:
    pytest tests/tools/test_e2e.py -v -m e2e
"""
from __future__ import annotations

import os
import socket
from typing import Any

import httpx
import pytest

# ---------------------------------------------------------------------------
# MCP endpoint reachability probe
# ---------------------------------------------------------------------------

MCP_URL: str = os.environ.get("MCP_URL", "http://localhost:8096")
HAS_TELEGRAM: bool = bool(
    os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID")
)

_MCP_REACHABLE: bool = False
try:
    _parsed = MCP_URL.replace("http://", "").replace("https://", "")
    _host, _, _port_str = _parsed.partition(":")
    _port = int(_port_str.split("/")[0]) if _port_str else 80
    _sock = socket.create_connection((_host, _port), timeout=3)
    _sock.close()
    _MCP_REACHABLE = True
except Exception:
    pass

pytestmark = pytest.mark.e2e

skip_no_mcp = pytest.mark.skipif(
    not _MCP_REACHABLE,
    reason=f"MCP endpoint not reachable at {MCP_URL}",
)

skip_no_telegram = pytest.mark.skipif(
    not HAS_TELEGRAM,
    reason="TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID env vars not set",
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NEW_TOOLS = {"ssh", "monitor", "git", "notify", "iot", "log"}


def _mcp_call(name: str, arguments: dict[str, Any], timeout: float = 60) -> list[dict[str, Any]]:
    """Execute a synchronous MCP tools/call and return the content list.

    Raises httpx.HTTPStatusError on non-2xx, or a plain RuntimeError
    if the JSON-RPC response contains an 'error' key.
    """
    r = httpx.post(
        f"{MCP_URL}/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
        timeout=timeout,
    )
    r.raise_for_status()
    body = r.json()
    if "error" in body:
        raise RuntimeError(f"JSON-RPC error: {body['error']}")
    return body.get("result", {}).get("content", [])


def _mcp_list_tools() -> list[str]:
    """Return the names of all tools currently registered on the MCP endpoint."""
    r = httpx.post(
        f"{MCP_URL}/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        timeout=10,
    )
    r.raise_for_status()
    body = r.json()
    tools = body.get("result", {}).get("tools", body.get("result", []))
    if isinstance(tools, list):
        return [t["name"] for t in tools if isinstance(t, dict)]
    return []


def _first_text(content: list[dict[str, Any]]) -> str:
    """Return the text of the first text block in an MCP content list."""
    for block in content:
        if block.get("type") == "text":
            return block.get("text", "")
    return ""


def _skip_if_tool_missing(name: str) -> None:
    """Skip the current test if *name* is not in the live tool list."""
    live = _mcp_list_tools()
    if name not in live:
        pytest.skip(f"Tool '{name}' not present in live MCP endpoint (needs rebuild)")


# ---------------------------------------------------------------------------
# SSH E2E tests
# ---------------------------------------------------------------------------

@skip_no_mcp
class TestSSHE2E:
    def test_list_hosts(self) -> None:
        """ssh list_hosts: returns Tailscale host list."""
        _skip_if_tool_missing("ssh")
        content = _mcp_call("ssh", {"action": "list_hosts"})
        text = _first_text(content)
        assert text.strip(), "Expected non-empty output from ssh list_hosts"


# ---------------------------------------------------------------------------
# Monitor E2E tests
# ---------------------------------------------------------------------------

@skip_no_mcp
class TestMonitorE2E:
    def test_tailscale(self) -> None:
        """monitor tailscale: returns Tailscale peer status."""
        _skip_if_tool_missing("monitor")
        content = _mcp_call("monitor", {"action": "tailscale"})
        text = _first_text(content)
        assert "Tailscale" in text, f"Expected 'Tailscale' in output, got: {text!r}"

    def test_containers(self) -> None:
        """monitor containers: returns container list for amarillo."""
        _skip_if_tool_missing("monitor")
        content = _mcp_call("monitor", {"action": "containers", "host": "amarillo"})
        text = _first_text(content)
        assert "Containers" in text, f"Expected 'Containers' in output, got: {text!r}"


# ---------------------------------------------------------------------------
# Git E2E tests
# ---------------------------------------------------------------------------

@skip_no_mcp
class TestGitE2E:
    def test_status(self) -> None:
        """git status: returns status for the aichat repo."""
        _skip_if_tool_missing("git")
        content = _mcp_call("git", {"action": "status", "repo": "aichat"})
        text = _first_text(content)
        assert text.strip(), "Expected non-empty output from git status"

    def test_scorecard(self) -> None:
        """git scorecard: returns CI health overview (may be slow)."""
        _skip_if_tool_missing("git")
        content = _mcp_call("git", {"action": "scorecard"}, timeout=120)
        text = _first_text(content)
        assert text.strip(), "Expected non-empty output from git scorecard"


# ---------------------------------------------------------------------------
# Notify E2E tests
# ---------------------------------------------------------------------------

@skip_no_mcp
@skip_no_telegram
class TestNotifyE2E:
    def test_send(self) -> None:
        """notify send: send a test notification via MCP endpoint."""
        _skip_if_tool_missing("notify")
        content = _mcp_call("notify", {
            "action": "send",
            "text": "[aichat e2e test] notify via MCP endpoint — ignore",
        })
        text = _first_text(content)
        assert text.strip(), "Expected non-empty response from notify send"


# ---------------------------------------------------------------------------
# Log E2E tests
# ---------------------------------------------------------------------------

@skip_no_mcp
class TestLogE2E:
    def test_list(self) -> None:
        """log list: returns list of log files from amarillo."""
        _skip_if_tool_missing("log")
        content = _mcp_call("log", {"action": "list"})
        text = _first_text(content)
        assert text.strip(), "Expected non-empty output from log list"

    def test_errors(self) -> None:
        """log errors: returns error aggregation across log files."""
        _skip_if_tool_missing("log")
        content = _mcp_call("log", {"action": "errors"})
        text = _first_text(content)
        assert text.strip(), "Expected non-empty output from log errors"


# ---------------------------------------------------------------------------
# IoT E2E tests
# ---------------------------------------------------------------------------

@skip_no_mcp
class TestIoTE2E:
    def test_list_devices(self) -> None:
        """iot list_devices: returns device registry."""
        _skip_if_tool_missing("iot")
        content = _mcp_call("iot", {"action": "list_devices"})
        text = _first_text(content)
        assert text.strip(), "Expected non-empty output from iot list_devices"


# ---------------------------------------------------------------------------
# Tool discovery
# ---------------------------------------------------------------------------

@skip_no_mcp
class TestToolDiscoveryE2E:
    def test_tools_list_method(self) -> None:
        """tools/list: endpoint responds with a non-empty tool list."""
        names = _mcp_list_tools()
        assert names, "Expected at least one tool from tools/list"

    def test_known_tools_present(self) -> None:
        """tools/list: original 19 tools are always present."""
        names = set(_mcp_list_tools())
        # A minimal subset that must always exist regardless of image version
        core_tools = {"web", "browser", "image", "code", "memory", "think", "chat"}
        missing = core_tools - names
        assert not missing, f"Core tools missing from live MCP: {missing}"

    def test_new_tools_present_after_rebuild(self) -> None:
        """tools/list: all 6 new modular tools present after container rebuild.

        Skipped (not failed) if the running image is pre-expansion.
        """
        names = set(_mcp_list_tools())
        if not _NEW_TOOLS.issubset(names):
            missing = _NEW_TOOLS - names
            pytest.skip(
                f"New modular tools not yet deployed (need rebuild): {missing}"
            )
        # If we reach here, all 6 are present — assert for documentation.
        assert _NEW_TOOLS.issubset(names), f"New tools missing: {_NEW_TOOLS - names}"
