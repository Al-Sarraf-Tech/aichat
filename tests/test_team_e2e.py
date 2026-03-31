"""End-to-end Playwright tests for Team of Experts MCP tools.

Tests the full MCP HTTP transport pipeline — sends JSON-RPC requests to the
aichat-mcp server and validates responses.

Requires:
  - Docker stack running (docker compose up -d)
  - Port 8096 exposed (docker-compose.ports.yml)
  - playwright + chromium installed

Run:
    pytest tests/test_team_e2e.py -v --timeout=120
"""
from __future__ import annotations

import json
import os

import pytest

try:
    from playwright.async_api import async_playwright
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False

import httpx

_MCP_URL = os.environ.get("MCP_URL", "http://localhost:8096")


def _mcp_reachable() -> bool:
    try:
        return httpx.get(f"{_MCP_URL}/health", timeout=3).is_success
    except Exception:
        return False


skip_no_playwright = pytest.mark.skipif(not HAS_PLAYWRIGHT, reason="playwright not installed")
skip_no_mcp = pytest.mark.skipif(not _mcp_reachable(), reason="MCP server not reachable")


async def _mcp_call(tool_name: str, arguments: dict) -> dict:
    """Send a JSON-RPC tools/call request to the MCP server."""
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(f"{_MCP_URL}/mcp", json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
        })
        resp.raise_for_status()
        return resp.json()


async def _mcp_tools_list() -> list[dict]:
    """Get the full tool catalog from the MCP server."""
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(f"{_MCP_URL}/mcp", json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/list",
            "params": {},
        })
        resp.raise_for_status()
        return resp.json().get("result", {}).get("tools", [])


# ---------------------------------------------------------------------------
# E2E: MCP Transport Tests (via httpx — validates the JSON-RPC wiring)
# ---------------------------------------------------------------------------

@skip_no_mcp
class TestTeamMCPTransport:
    """Test Team of Experts tools via the MCP HTTP transport."""

    @pytest.mark.asyncio
    async def test_tools_list_includes_team(self):
        """All 4 team_* tools should appear in the MCP catalog."""
        tools = await _mcp_tools_list()
        names = {t["name"] for t in tools}
        for expected in ("team_chat", "team_image", "team_agents", "team_status"):
            assert expected in names, f"{expected} missing from tool catalog"

    @pytest.mark.asyncio
    async def test_team_agents_returns_fleet(self):
        """team_agents should list all agents with availability."""
        data = await _mcp_call("team_agents", {})
        content = data.get("result", {}).get("content", [])
        text = " ".join(b.get("text", "") for b in content if b.get("type") == "text")
        assert "Team of Experts" in text
        # Should list at least claude, codex, gemini, qwen, arc
        for agent in ("claude", "codex", "gemini", "qwen", "arc"):
            assert agent in text.lower(), f"{agent} not in team_agents output"

    @pytest.mark.asyncio
    async def test_team_status_enabled(self):
        """team_status should show the system is enabled."""
        data = await _mcp_call("team_status", {})
        content = data.get("result", {}).get("content", [])
        text = " ".join(b.get("text", "") for b in content if b.get("type") == "text")
        assert "Enabled" in text or "enabled" in text

    @pytest.mark.asyncio
    async def test_team_chat_validation(self):
        """team_chat with empty message should return validation error."""
        data = await _mcp_call("team_chat", {"message": ""})
        content = data.get("result", {}).get("content", [])
        text = " ".join(b.get("text", "") for b in content if b.get("type") == "text")
        assert "required" in text.lower()

    @pytest.mark.asyncio
    async def test_team_image_validation(self):
        """team_image with empty prompt should return validation error."""
        data = await _mcp_call("team_image", {"prompt": ""})
        content = data.get("result", {}).get("content", [])
        text = " ".join(b.get("text", "") for b in content if b.get("type") == "text")
        assert "required" in text.lower()

    @pytest.mark.asyncio
    async def test_team_chat_schema_correct(self):
        """team_chat tool schema should have required fields."""
        tools = await _mcp_tools_list()
        chat_tool = next((t for t in tools if t["name"] == "team_chat"), None)
        assert chat_tool is not None
        schema = chat_tool.get("inputSchema", {})
        assert "message" in schema.get("properties", {})
        assert "message" in schema.get("required", [])
        # Optional fields
        assert "agent" in schema.get("properties", {})
        assert "task_type" in schema.get("properties", {})

    @pytest.mark.asyncio
    async def test_team_image_schema_correct(self):
        """team_image tool schema should have required fields."""
        tools = await _mcp_tools_list()
        img_tool = next((t for t in tools if t["name"] == "team_image"), None)
        assert img_tool is not None
        schema = img_tool.get("inputSchema", {})
        assert "prompt" in schema.get("properties", {})
        assert "prompt" in schema.get("required", [])
        # Optional fields
        props = schema.get("properties", {})
        assert "mode" in props
        assert "backend" in props
        assert "style" in props
        assert "aspect_ratio" in props


# ---------------------------------------------------------------------------
# E2E: Playwright Browser Tests (validates MCP via browser fetch)
# ---------------------------------------------------------------------------

@skip_no_mcp
@skip_no_playwright
class TestTeamPlaywright:
    """Playwright-based e2e tests — use a real browser to call MCP endpoints."""

    @pytest.mark.asyncio
    async def test_mcp_health_via_browser(self):
        """Verify MCP health endpoint is reachable from a browser context."""
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            resp = await page.goto(f"{_MCP_URL}/health")
            assert resp is not None
            assert resp.status == 200
            body = await page.text_content("body")
            assert body is not None
            data = json.loads(body)
            assert data.get("ok") is True
            await browser.close()

    @pytest.mark.asyncio
    async def test_team_agents_via_browser_fetch(self):
        """Call team_agents via browser fetch() — validates CORS and transport."""
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            # Navigate to MCP health first to establish origin
            await page.goto(f"{_MCP_URL}/health")
            # Use fetch() to call the MCP JSON-RPC endpoint
            result = await page.evaluate(f"""
                async () => {{
                    const resp = await fetch('{_MCP_URL}/mcp', {{
                        method: 'POST',
                        headers: {{'Content-Type': 'application/json'}},
                        body: JSON.stringify({{
                            jsonrpc: '2.0', id: 1,
                            method: 'tools/call',
                            params: {{name: 'team_agents', arguments: {{}}}}
                        }})
                    }});
                    return await resp.json();
                }}
            """)
            content = result.get("result", {}).get("content", [])
            text = " ".join(b.get("text", "") for b in content if isinstance(b, dict))
            assert "Team of Experts" in text
            await browser.close()

    @pytest.mark.asyncio
    async def test_team_status_via_browser_fetch(self):
        """Call team_status via browser fetch()."""
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.goto(f"{_MCP_URL}/health")
            result = await page.evaluate(f"""
                async () => {{
                    const resp = await fetch('{_MCP_URL}/mcp', {{
                        method: 'POST',
                        headers: {{'Content-Type': 'application/json'}},
                        body: JSON.stringify({{
                            jsonrpc: '2.0', id: 1,
                            method: 'tools/call',
                            params: {{name: 'team_status', arguments: {{}}}}
                        }})
                    }});
                    return await resp.json();
                }}
            """)
            content = result.get("result", {}).get("content", [])
            text = " ".join(b.get("text", "") for b in content if isinstance(b, dict))
            assert "Team of Experts" in text
            assert "Enabled" in text or "available" in text.lower()
            await browser.close()

    @pytest.mark.asyncio
    async def test_tools_list_via_browser_fetch(self):
        """Verify all team_* tools appear in tools/list via browser fetch()."""
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.goto(f"{_MCP_URL}/health")
            result = await page.evaluate(f"""
                async () => {{
                    const resp = await fetch('{_MCP_URL}/mcp', {{
                        method: 'POST',
                        headers: {{'Content-Type': 'application/json'}},
                        body: JSON.stringify({{
                            jsonrpc: '2.0', id: 1,
                            method: 'tools/list', params: {{}}
                        }})
                    }});
                    return await resp.json();
                }}
            """)
            tools = result.get("result", {}).get("tools", [])
            names = {t["name"] for t in tools}
            for expected in ("team_chat", "team_image", "team_agents", "team_status"):
                assert expected in names, f"{expected} missing from tools/list via browser"
            await browser.close()
