"""Tests for workspace — per-user file storage MCP tool.

Unit tests (no Docker):
    pytest tests/test_workspace.py -v -k unit

Integration tests (requires Docker stack):
    pytest tests/test_workspace.py -v -k integration
"""
from __future__ import annotations

import os
import uuid

import pytest
import httpx

_MCP_URL = os.environ.get("MCP_URL", "http://localhost:8096")


def _mcp_reachable() -> bool:
    try:
        return httpx.get(f"{_MCP_URL}/health", timeout=3).is_success
    except Exception:
        return False


skip_no_mcp = pytest.mark.skipif(not _mcp_reachable(), reason="MCP not reachable")


async def _mcp_call(tool: str, args: dict) -> dict:
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(f"{_MCP_URL}/mcp", json={
            "jsonrpc": "2.0", "id": 1,
            "method": "tools/call",
            "params": {"name": tool, "arguments": args},
        })
        resp.raise_for_status()
        return resp.json()


def _extract_text(data: dict) -> str:
    content = data.get("result", {}).get("content", [])
    return " ".join(b.get("text", "") for b in content if b.get("type") == "text")


# ---------------------------------------------------------------------------
# Unit tests — path traversal validation
# ---------------------------------------------------------------------------

class TestPathTraversalValidation:
    """Verify path traversal patterns are rejected."""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_dotdot_blocked(self):
        """../../../etc/passwd must be blocked."""
        data = await _mcp_call("workspace", {
            "action": "read", "user": "testuser", "path": "../../../etc/passwd",
        })
        text = _extract_text(data)
        assert "traversal" in text.lower() or "blocked" in text.lower() or "invalid" in text.lower()

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_absolute_path_blocked(self):
        """/etc/passwd must be blocked."""
        data = await _mcp_call("workspace", {
            "action": "read", "user": "testuser", "path": "/etc/passwd",
        })
        text = _extract_text(data)
        assert "invalid" in text.lower()

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_null_byte_blocked(self):
        """Null bytes in path must be blocked."""
        data = await _mcp_call("workspace", {
            "action": "read", "user": "testuser", "path": "file\x00.txt",
        })
        text = _extract_text(data)
        assert "invalid" in text.lower()

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_invalid_username_blocked(self):
        """Usernames with special chars must be rejected."""
        data = await _mcp_call("workspace", {
            "action": "list", "user": "../admin",
        })
        text = _extract_text(data)
        assert "invalid" in text.lower()


# ---------------------------------------------------------------------------
# Integration tests — full write/read/list/delete cycle
# ---------------------------------------------------------------------------

@skip_no_mcp
class TestWorkspaceCRUD:
    """Test workspace file operations via MCP."""

    _TEST_USER = f"test-{uuid.uuid4().hex[:8]}"

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_write_and_read(self):
        data = await _mcp_call("workspace", {
            "action": "write", "user": self._TEST_USER,
            "path": "hello.txt", "content": "Hello workspace!",
        })
        text = _extract_text(data)
        assert "written" in text.lower() or "16 bytes" in text.lower()

        # Read it back
        data = await _mcp_call("workspace", {
            "action": "read", "user": self._TEST_USER, "path": "hello.txt",
        })
        text = _extract_text(data)
        assert "Hello workspace!" in text

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_list(self):
        # Write a file first
        await _mcp_call("workspace", {
            "action": "write", "user": self._TEST_USER,
            "path": "list-test.txt", "content": "test",
        })
        data = await _mcp_call("workspace", {
            "action": "list", "user": self._TEST_USER,
        })
        text = _extract_text(data)
        assert "list-test.txt" in text

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_mkdir(self):
        data = await _mcp_call("workspace", {
            "action": "mkdir", "user": self._TEST_USER, "path": "scripts",
        })
        text = _extract_text(data)
        assert "created" in text.lower() or "scripts" in text.lower()

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_info(self):
        data = await _mcp_call("workspace", {
            "action": "info", "user": self._TEST_USER,
        })
        text = _extract_text(data)
        assert "files" in text.lower() or "bytes" in text.lower()

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_delete(self):
        await _mcp_call("workspace", {
            "action": "write", "user": self._TEST_USER,
            "path": "deleteme.txt", "content": "bye",
        })
        data = await _mcp_call("workspace", {
            "action": "delete", "user": self._TEST_USER, "path": "deleteme.txt",
        })
        text = _extract_text(data)
        assert "deleted" in text.lower()


@skip_no_mcp
class TestWorkspaceUserIsolation:
    """Verify users cannot access each other's files."""

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_user_isolation(self):
        user_a = f"iso-a-{uuid.uuid4().hex[:6]}"
        user_b = f"iso-b-{uuid.uuid4().hex[:6]}"

        # User A writes a file
        await _mcp_call("workspace", {
            "action": "write", "user": user_a,
            "path": "secret.txt", "content": "User A secret",
        })

        # User B lists their (empty) workspace — should NOT see User A's file
        data = await _mcp_call("workspace", {
            "action": "list", "user": user_b,
        })
        text = _extract_text(data)
        assert "secret.txt" not in text

        # User B tries to read User A's file via their own workspace — not found
        data = await _mcp_call("workspace", {
            "action": "read", "user": user_b, "path": "secret.txt",
        })
        text = _extract_text(data)
        assert "not found" in text.lower()


@skip_no_mcp
class TestWorkspaceInToolCatalog:
    """Verify workspace tool appears in MCP catalog."""

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_workspace_in_tools_list(self):
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(f"{_MCP_URL}/mcp", json={
                "jsonrpc": "2.0", "id": 1,
                "method": "tools/list", "params": {},
            })
            tools = resp.json().get("result", {}).get("tools", [])
            names = {t["name"] for t in tools}
            assert "workspace" in names
