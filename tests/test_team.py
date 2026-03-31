"""Tests for Team of Experts — multi-agent orchestration.

Unit tests (no Docker required):
    pytest tests/test_team.py -v -k unit

Integration tests (requires Docker stack running):
    pytest tests/test_team.py -v -k integration

MCP tool tests (requires Docker stack running):
    pytest tests/test_team.py -v -k mcp
"""
from __future__ import annotations

import json
import os
import sys
from unittest.mock import AsyncMock, patch

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

class TestTeamRouter:
    """Test TeamRouter agent selection logic."""

    @pytest.mark.unit
    def test_pick_default_simple_qa(self):
        from team import TeamRouter
        agent = TeamRouter.pick("simple_qa")
        assert agent in ("qwen", "gemini")  # free agents preferred

    @pytest.mark.unit
    def test_pick_architecture_prefers_claude(self):
        from team import TeamRouter
        assert TeamRouter.pick("architecture") == "claude"

    @pytest.mark.unit
    def test_pick_force_agent(self):
        from team import TeamRouter
        assert TeamRouter.pick("simple_qa", force_agent="claude") == "claude"

    @pytest.mark.unit
    def test_pick_unknown_agent_raises(self):
        from team import TeamRouter
        with pytest.raises(ValueError, match="Unknown agent"):
            TeamRouter.pick("simple_qa", force_agent="nonexistent")

    @pytest.mark.unit
    def test_is_available_local_agents(self):
        from team import TeamRouter
        assert TeamRouter.is_available("qwen")
        assert TeamRouter.is_available("arc")
        assert TeamRouter.is_available("claude")
        assert TeamRouter.is_available("codex")

    @pytest.mark.unit
    def test_is_available_gemini_always(self):
        """Gemini is always available — CLI on host, no API key needed."""
        from team import TeamRouter
        assert TeamRouter.is_available("gemini")

    @pytest.mark.unit
    def test_is_available_openai_needs_key(self):
        from team import TeamRouter
        import team
        original = team.OPENAI_API_KEY
        team.OPENAI_API_KEY = ""
        assert not TeamRouter.is_available("openai")
        team.OPENAI_API_KEY = "test-key"
        assert TeamRouter.is_available("openai")
        team.OPENAI_API_KEY = original


class TestTaskClassifier:
    """Test keyword-based task classification."""

    @pytest.mark.unit
    def test_classify_code_review(self):
        from team import _classify_task
        assert _classify_task("review this PR") == "code_review"

    @pytest.mark.unit
    def test_classify_debugging(self):
        from team import _classify_task
        assert _classify_task("debug this error in main.py") == "debugging"

    @pytest.mark.unit
    def test_classify_simple_qa(self):
        from team import _classify_task
        assert _classify_task("what is the capital of France") == "simple_qa"

    @pytest.mark.unit
    def test_classify_security(self):
        from team import _classify_task
        assert _classify_task("run a security audit") == "security"


class TestAgentResults:
    """Test data types."""

    @pytest.mark.unit
    def test_agent_result_success(self):
        from team import AgentResult
        r = AgentResult(agent="test", content="hello", exit_code=0)
        assert r.success
        assert r.content == "hello"

    @pytest.mark.unit
    def test_agent_result_failure(self):
        from team import AgentResult
        r = AgentResult(agent="test", exit_code=1, error="failed")
        assert not r.success

    @pytest.mark.unit
    def test_image_result_success(self):
        from team import ImageResult
        r = ImageResult(backend="arc", image_b64="AAAA", width=512, height=512)
        assert r.success

    @pytest.mark.unit
    def test_image_result_failure(self):
        from team import ImageResult
        r = ImageResult(backend="arc", error="no GPU")
        assert not r.success


class TestProgressReporter:
    """Test progress reporting."""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_report_calls_callbacks(self):
        from team import ProgressReporter
        pr = ProgressReporter()
        called = []
        async def cb(**kwargs):
            called.append(kwargs)
        pr.register(cb)
        await pr.report("wf1", 1, 5, "qwen", "prompt", "working...")
        assert len(called) == 1
        assert "Team of Experts" in called[0]["detail"]


class TestAspectRatios:
    """Test aspect ratio resolution."""

    @pytest.mark.unit
    def test_known_ratios(self):
        from team import ASPECT_RATIOS
        assert ASPECT_RATIOS["1:1"] == (1024, 1024)
        assert ASPECT_RATIOS["16:9"] == (1820, 1024)
        assert ASPECT_RATIOS["9:16"] == (1024, 1820)


class TestTeamAgents:
    """Test team_agents listing."""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_team_agents_returns_all(self):
        from team import team_agents
        agents = await team_agents()
        names = [a["name"] for a in agents]
        assert "claude" in names
        assert "codex" in names
        assert "qwen" in names
        assert "arc" in names
        assert "gemini" in names
        assert "comfyui" in names


# ---------------------------------------------------------------------------
# Integration tests — require Docker stack
# ---------------------------------------------------------------------------

@skip_no_mcp
class TestMCPTeamTools:
    """Test Team of Experts MCP tools via the MCP server."""

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_team_agents_tool(self):
        """team_agents should return agent list via MCP."""
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(f"{_MCP_URL}/mcp", json={
                "jsonrpc": "2.0", "id": 1,
                "method": "tools/call",
                "params": {"name": "team_agents", "arguments": {}},
            })
            assert resp.is_success
            data = resp.json()
            content = data.get("result", {}).get("content", [])
            assert any("Team of Experts" in str(b) for b in content)

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_team_status_tool(self):
        """team_status should report system status."""
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(f"{_MCP_URL}/mcp", json={
                "jsonrpc": "2.0", "id": 2,
                "method": "tools/call",
                "params": {"name": "team_status", "arguments": {}},
            })
            assert resp.is_success
            data = resp.json()
            content = data.get("result", {}).get("content", [])
            text = str(content)
            assert "Team of Experts" in text

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_team_tools_in_catalog(self):
        """team_* tools should appear in tools/list."""
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(f"{_MCP_URL}/mcp", json={
                "jsonrpc": "2.0", "id": 3,
                "method": "tools/list", "params": {},
            })
            assert resp.is_success
            data = resp.json()
            tools = data.get("result", {}).get("tools", [])
            tool_names = {t["name"] for t in tools}
            assert "team_chat" in tool_names
            assert "team_image" in tool_names
            assert "team_agents" in tool_names
            assert "team_status" in tool_names

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_team_chat_requires_message(self):
        """team_chat with no message should return error."""
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(f"{_MCP_URL}/mcp", json={
                "jsonrpc": "2.0", "id": 4,
                "method": "tools/call",
                "params": {"name": "team_chat", "arguments": {}},
            })
            data = resp.json()
            content = str(data.get("result", {}).get("content", []))
            assert "required" in content.lower()

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_team_image_requires_prompt(self):
        """team_image with no prompt should return error."""
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(f"{_MCP_URL}/mcp", json={
                "jsonrpc": "2.0", "id": 5,
                "method": "tools/call",
                "params": {"name": "team_image", "arguments": {}},
            })
            data = resp.json()
            content = str(data.get("result", {}).get("content", []))
            assert "required" in content.lower()
