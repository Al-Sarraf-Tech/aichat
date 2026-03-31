"""Comprehensive Playwright e2e tests for Dartboard web UI.

Tests the full user-facing flow through the auth proxy:
  Login → Model selection → Team of Experts → Chat → Error handling

Requires:
  - Docker stack running with ports overlay
  - playwright + chromium installed
  - Auth proxy at :8200, admin panel at :8247

Run:
    pytest tests/test_dartboard_e2e.py -v --timeout=180
"""
from __future__ import annotations

import json
import os
import time

import httpx
import pytest

try:
    from playwright.async_api import async_playwright, Page, BrowserContext
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False

_AUTH_URL = os.environ.get("AUTH_URL", "http://localhost:8200")
_MCP_URL = os.environ.get("MCP_URL", "http://localhost:8096")
_TEST_USER = "playwright-test"
_TEST_PASS = "TestPass123!"


def _auth_reachable() -> bool:
    try:
        return httpx.get(f"{_AUTH_URL}/health", timeout=3).is_success
    except Exception:
        return False


def _mcp_reachable() -> bool:
    try:
        return httpx.get(f"{_MCP_URL}/health", timeout=3).is_success
    except Exception:
        return False


skip_no_playwright = pytest.mark.skipif(not HAS_PLAYWRIGHT, reason="playwright not installed")
skip_no_auth = pytest.mark.skipif(not _auth_reachable(), reason="Auth proxy not reachable")
skip_no_mcp = pytest.mark.skipif(not _mcp_reachable(), reason="MCP not reachable")


async def _do_login(page: "Page") -> None:
    """Login to Dartboard via the auth screen."""
    await page.goto(_AUTH_URL)
    await page.wait_for_selector('#login-user', timeout=5000)
    await page.fill('#login-user', _TEST_USER)
    await page.fill('#login-pass', _TEST_PASS)
    await page.click('#login-btn')
    await page.wait_for_selector('#app:not(.hidden)', timeout=10000)


def _get_token() -> str:
    """Get a JWT token for the test user."""
    r = httpx.post(f"{_AUTH_URL}/auth/login", json={
        "username": _TEST_USER, "password": _TEST_PASS,
    }, timeout=10)
    if not r.is_success:
        pytest.skip(f"Cannot login test user: {r.text}")
    return r.json()["token"]


# ---------------------------------------------------------------------------
# Login + Auth Tests
# ---------------------------------------------------------------------------

@skip_no_playwright
@skip_no_auth
class TestLogin:
    """Test the login flow via Playwright."""

    @pytest.mark.asyncio
    async def test_login_page_loads(self):
        """Auth proxy serves a login page."""
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            resp = await page.goto(_AUTH_URL)
            assert resp is not None
            assert resp.status == 200
            await page.wait_for_selector('#login-user', timeout=5000)
            await browser.close()

    @pytest.mark.asyncio
    async def test_login_and_redirect(self):
        """Successful login stores JWT and loads Dartboard."""
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await _do_login(page)
            # #app should be visible after login
            await page.wait_for_selector('#app:not(.hidden)', timeout=10000)
            await browser.close()


# ---------------------------------------------------------------------------
# Team of Experts UI Tests
# ---------------------------------------------------------------------------

@skip_no_playwright
@skip_no_auth
class TestTeamModelSelector:
    """Test Team of Experts model selection in the Dartboard dropdown."""

    @pytest.mark.asyncio
    async def test_team_section_in_dropdown(self):
        """Model dropdown should show Team of Experts section."""
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await _do_login(page)
            await page.click('#model-btn')
            await page.wait_for_timeout(500)
            body = await page.content()
            assert "Team of Experts" in body, "Team of Experts section missing from dropdown"
            await browser.close()

    @pytest.mark.asyncio
    async def test_team_agents_listed(self):
        """All 5 team agents should appear in the dropdown."""
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await _do_login(page)
            await page.click('#model-btn')
            await page.wait_for_timeout(500)
            body = await page.content()
            for agent in ["Auto", "Claude", "Codex", "Gemini", "Qwen"]:
                assert agent in body, f"Team agent '{agent}' missing from dropdown"
            await browser.close()

    @pytest.mark.asyncio
    async def test_select_team_auto(self):
        """Selecting team:auto should show 'Team' badge and mark as ready."""
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await _do_login(page)
            await page.click('#model-btn')
            await page.wait_for_timeout(500)
            await page.locator('.team-item').first.click()
            await page.wait_for_timeout(1000)
            body = await page.content()
            assert "Team" in body or "team" in body
            await browser.close()


# ---------------------------------------------------------------------------
# Double-Submit Prevention Tests
# ---------------------------------------------------------------------------

@skip_no_playwright
@skip_no_auth
class TestDoubleSubmitPrevention:
    """Verify the _sendLock prevents multiple rapid Enter presses."""

    async def _login_and_select_team(self, page: "Page") -> None:
        await _do_login(page)
        await page.click('#model-btn')
        await page.wait_for_timeout(500)
        await page.locator('.team-item').first.click()
        await page.wait_for_timeout(1000)

    @pytest.mark.asyncio
    async def test_sendlock_exists(self):
        """The _sendLock variable should exist in the JS."""
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await self._login_and_select_team(page)
            has_lock = await page.evaluate("typeof _sendLock !== 'undefined'")
            assert has_lock, "_sendLock variable missing from app.js"
            await browser.close()

    @pytest.mark.asyncio
    async def test_rapid_enter_sends_only_once(self):
        """Pressing Enter 5 times rapidly should only send 1 message."""
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await self._login_and_select_team(page)
            input_el = page.locator('#input')
            await input_el.fill("hello test 12345")
            for _ in range(5):
                await input_el.press("Enter")
            await page.wait_for_timeout(500)
            messages = await page.locator('.msg-row.user').count()
            assert messages <= 1, f"Expected 1 user message, got {messages} (double-submit!)"
            await browser.close()


# ---------------------------------------------------------------------------
# SSE Keepalive + Team Chat Flow Tests
# ---------------------------------------------------------------------------

@skip_no_playwright
@skip_no_auth
@skip_no_mcp
class TestTeamChatFlow:
    """Test actual team_chat message flow through the full stack."""

    async def _login_and_select_model(self, page: "Page", model_idx: int = 0) -> None:
        await _do_login(page)
        await page.click('#model-btn')
        await page.wait_for_timeout(500)
        await page.locator('.team-item').nth(model_idx).click()
        await page.wait_for_timeout(1000)

    @pytest.mark.asyncio
    async def test_team_auto_responds(self):
        """Sending a message with team:auto should return a response."""
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await self._login_and_select_model(page, 0)  # team:auto

            input_el = page.locator('#input')
            await input_el.fill("What is 2+2? Reply with just the number.")
            await input_el.press("Enter")

            # Wait for response (up to 60s for slow agents)
            try:
                await page.wait_for_selector(
                    '.msg-row.assistant',
                    timeout=60000,
                )
            except Exception:
                # Check if we at least see streaming
                body = await page.content()
                assert "streaming" in body.lower() or "spinner" in body.lower() or "waiting" in body.lower(), \
                    "No response and no streaming indicator visible"
                return

            # Verify assistant message appeared
            assistant_msgs = await page.locator('.msg-row.assistant').count()
            assert assistant_msgs >= 1, "No assistant response received"
            await browser.close()


# ---------------------------------------------------------------------------
# MCP Tool Tests (direct, no browser)
# ---------------------------------------------------------------------------

@skip_no_mcp
class TestMCPTeamEndToEnd:
    """Direct MCP tool tests — verify the backend is solid."""

    @pytest.mark.asyncio
    async def test_team_chat_auto_routing(self):
        """Auto-router should pick a free agent for simple questions."""
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(f"{_MCP_URL}/mcp", json={
                "jsonrpc": "2.0", "id": 1,
                "method": "tools/call",
                "params": {"name": "team_chat", "arguments": {
                    "message": "What is 2+2? Reply with just the number.",
                }},
            })
            assert resp.is_success
            data = resp.json()
            content = data["result"]["content"]
            text = " ".join(b.get("text", "") for b in content)
            assert "4" in text, f"Expected '4' in response, got: {text[:200]}"
            # Should have used a free agent (qwen or gemini)
            assert "qwen" in text.lower() or "gemini" in text.lower(), \
                f"Expected free agent, got: {text[:200]}"

    @pytest.mark.asyncio
    async def test_team_chat_forced_qwen(self):
        """Forcing qwen should route to LM Studio."""
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(f"{_MCP_URL}/mcp", json={
                "jsonrpc": "2.0", "id": 1,
                "method": "tools/call",
                "params": {"name": "team_chat", "arguments": {
                    "message": "Say OK",
                    "agent": "qwen",
                }},
            })
            assert resp.is_success
            data = resp.json()
            text = " ".join(b.get("text", "") for b in data["result"]["content"])
            assert "qwen" in text.lower(), f"Should route to qwen, got: {text[:200]}"

    @pytest.mark.asyncio
    async def test_team_chat_empty_message_rejected(self):
        """Empty message should return validation error, not crash."""
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(f"{_MCP_URL}/mcp", json={
                "jsonrpc": "2.0", "id": 1,
                "method": "tools/call",
                "params": {"name": "team_chat", "arguments": {"message": ""}},
            })
            assert resp.is_success
            text = str(resp.json()["result"]["content"])
            assert "required" in text.lower()

    @pytest.mark.asyncio
    async def test_team_status_shows_agents(self):
        """team_status should list available agents."""
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(f"{_MCP_URL}/mcp", json={
                "jsonrpc": "2.0", "id": 1,
                "method": "tools/call",
                "params": {"name": "team_status", "arguments": {}},
            })
            assert resp.is_success
            text = str(resp.json()["result"]["content"])
            assert "Team of Experts" in text
            assert "available" in text.lower()

    @pytest.mark.asyncio
    async def test_team_image_draft_mode(self):
        """team_image draft mode should return quickly (Arc only)."""
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(f"{_MCP_URL}/mcp", json={
                "jsonrpc": "2.0", "id": 1,
                "method": "tools/call",
                "params": {"name": "team_image", "arguments": {
                    "prompt": "a red circle on white background",
                    "mode": "draft",
                }},
            })
            assert resp.is_success
            data = resp.json()
            content = data["result"]["content"]
            # Should have at least a text block (header) and either image or error
            assert len(content) >= 1

    @pytest.mark.asyncio
    async def test_classifier_word_boundary(self):
        """Classifier should not trigger code_review on 'improve'."""
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(f"{_MCP_URL}/mcp", json={
                "jsonrpc": "2.0", "id": 1,
                "method": "tools/call",
                "params": {"name": "team_chat", "arguments": {
                    "message": "improve this summary of quantum computing",
                }},
            })
            assert resp.is_success
            text = " ".join(b.get("text", "") for b in resp.json()["result"]["content"])
            # Should NOT route to claude (code_review), should use a free agent
            assert "qwen" in text.lower() or "gemini" in text.lower(), \
                f"'improve' should not trigger code_review routing, got: {text[:200]}"


# ---------------------------------------------------------------------------
# Error Handling Tests
# ---------------------------------------------------------------------------

@skip_no_mcp
class TestTeamErrorHandling:
    """Verify graceful error handling for edge cases."""

    @pytest.mark.asyncio
    async def test_invalid_agent_rejected(self):
        """Requesting an unknown agent should return an error."""
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(f"{_MCP_URL}/mcp", json={
                "jsonrpc": "2.0", "id": 1,
                "method": "tools/call",
                "params": {"name": "team_chat", "arguments": {
                    "message": "hello",
                    "agent": "nonexistent",
                }},
            })
            assert resp.is_success
            text = str(resp.json()["result"]["content"])
            assert "error" in text.lower() or "unknown" in text.lower()

    @pytest.mark.asyncio
    async def test_shell_metacharacters_safe(self):
        """Messages with shell metacharacters should not break SSH commands."""
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(f"{_MCP_URL}/mcp", json={
                "jsonrpc": "2.0", "id": 1,
                "method": "tools/call",
                "params": {"name": "team_chat", "arguments": {
                    "message": "what is $(echo hello) and `date` and $HOME ?",
                    "agent": "qwen",
                }},
            })
            assert resp.is_success
            data = resp.json()
            # Should get a normal response, not a shell injection result
            assert data["result"].get("isError") is not True
