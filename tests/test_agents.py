"""Tests for agents — CLI chat and image pipeline.

Unit tests (no Docker required):
    pytest tests/test_agents.py -v -k unit

Integration tests (requires Docker stack running):
    pytest tests/test_agents.py -v -k integration
"""
from __future__ import annotations

import os
import sys

import pytest
import httpx

# Add docker/mcp to path for unit tests
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "docker", "mcp"))

_MCP_URL = os.environ.get("MCP_URL", "http://localhost:8096")


def _mcp_reachable() -> bool:
    try:
        r = httpx.get(f"{_MCP_URL}/health", timeout=3)
        return r.is_success
    except Exception:
        return False


skip_no_mcp = pytest.mark.skipif(not _mcp_reachable(), reason="MCP not reachable")


# ---------------------------------------------------------------------------
# Unit tests — no Docker, no network
# ---------------------------------------------------------------------------

class TestChatDispatch:
    """Test chat() dispatcher validation."""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_chat_rejects_unknown_agent(self):
        from agents import chat
        result = await chat("hello", agent="nonexistent")
        assert not result.success
        assert "Unknown agent" in result.error

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_chat_valid_agents(self):
        from agents import _VALID_AGENTS
        assert _VALID_AGENTS == {"claude", "codex", "gemini", "qwen"}


class TestAgentResults:
    """Test data types."""

    @pytest.mark.unit
    def test_agent_result_success(self):
        from agents import AgentResult
        r = AgentResult(agent="test", content="hello", exit_code=0)
        assert r.success
        assert r.content == "hello"

    @pytest.mark.unit
    def test_agent_result_failure(self):
        from agents import AgentResult
        r = AgentResult(agent="test", exit_code=1, error="failed")
        assert not r.success

    @pytest.mark.unit
    def test_image_result_success(self):
        from agents import ImageResult
        r = ImageResult(backend="arc", image_b64="AAAA", width=512, height=512)
        assert r.success

    @pytest.mark.unit
    def test_image_result_failure(self):
        from agents import ImageResult
        r = ImageResult(backend="arc", error="no GPU")
        assert not r.success


class TestAspectRatios:
    """Test aspect ratio resolution."""

    @pytest.mark.unit
    def test_known_ratios(self):
        from agents import ASPECT_RATIOS
        assert ASPECT_RATIOS["1:1"] == (1024, 1024)
        assert ASPECT_RATIOS["16:9"] == (1820, 1024)
        assert ASPECT_RATIOS["9:16"] == (1024, 1820)


class TestInputLimits:
    """Test input truncation."""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_chat_truncates_long_message(self):
        from agents import chat, _MAX_INPUT
        # This will fail at SSH but should not crash on truncation
        result = await chat("x" * (_MAX_INPUT + 1000), agent="qwen")
        # Expected to fail (no LM Studio) but should not raise
        assert isinstance(result.error, str)


class TestValidation:
    """Test parameter validation in agent runners."""

    @pytest.mark.unit
    def test_valid_claude_models(self):
        from agents import _VALID_MODELS_CLAUDE
        assert "haiku" in _VALID_MODELS_CLAUDE
        assert "sonnet" in _VALID_MODELS_CLAUDE
        assert "opus" in _VALID_MODELS_CLAUDE

    @pytest.mark.unit
    def test_valid_efforts(self):
        from agents import _VALID_EFFORTS
        assert "low" in _VALID_EFFORTS
        assert "max" in _VALID_EFFORTS

    @pytest.mark.unit
    def test_valid_reasoning(self):
        from agents import _VALID_REASONING
        assert "xhigh" in _VALID_REASONING


# ---------------------------------------------------------------------------
# Integration tests — require Docker stack
# ---------------------------------------------------------------------------

@skip_no_mcp
class TestMCPChatTool:
    """Test chat MCP tool via the MCP server."""

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_chat_requires_message(self):
        """chat with no message should return error."""
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(f"{_MCP_URL}/mcp", json={
                "jsonrpc": "2.0", "id": 1,
                "method": "tools/call",
                "params": {"name": "chat", "arguments": {"agent": "qwen"}},
            })
            assert resp.is_success
            data = resp.json()
            text = str(data.get("result", {}).get("content", []))
            assert "required" in text.lower()

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_chat_requires_agent(self):
        """chat with no agent should return error."""
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(f"{_MCP_URL}/mcp", json={
                "jsonrpc": "2.0", "id": 2,
                "method": "tools/call",
                "params": {"name": "chat", "arguments": {"message": "hello"}},
            })
            assert resp.is_success
            data = resp.json()
            text = str(data.get("result", {}).get("content", []))
            assert "required" in text.lower()

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_tools_in_catalog(self):
        """chat and image_pipeline should appear in tools/list."""
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(f"{_MCP_URL}/mcp", json={
                "jsonrpc": "2.0", "id": 3,
                "method": "tools/list", "params": {},
            })
            assert resp.is_success
            data = resp.json()
            tools = data.get("result", {}).get("tools", [])
            tool_names = {t["name"] for t in tools}
            assert "chat" in tool_names
            assert "image_pipeline" in tool_names
            assert "team_chat" not in tool_names
            assert "team_image" not in tool_names
            assert "team_agents" not in tool_names
            assert "team_status" not in tool_names

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_image_pipeline_requires_prompt(self):
        """image_pipeline with no prompt should return error."""
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(f"{_MCP_URL}/mcp", json={
                "jsonrpc": "2.0", "id": 4,
                "method": "tools/call",
                "params": {"name": "image_pipeline", "arguments": {}},
            })
            assert resp.is_success
            data = resp.json()
            text = str(data.get("result", {}).get("content", []))
            assert "required" in text.lower()
