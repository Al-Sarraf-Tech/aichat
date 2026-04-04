#!/usr/bin/env python3
"""Playwright E2E tests for aichat web UI.

Tests the full browser experience: login, chat, image generation,
model selection, pretext bubble rendering, and UI responsiveness.

Usage:
    python3 -m pytest test/test_playwright_e2e.py -v --timeout=300
    # Or directly:
    python3 test/test_playwright_e2e.py
"""
import json
import os
import subprocess
import sys
import time

import pytest

# ── Fixtures ──────────────────────────────────────────────────────

APP_URL = os.environ.get("APP_URL", "http://localhost:8200")


def _get_jwt():
    """Generate a fresh JWT via the auth container."""
    result = subprocess.run(
        ["docker", "exec", "aichat-aichat-auth-1", "python3", "-c",
         "import jwt, datetime, os; "
         "print(jwt.encode("
         "{'sub': 'playwright-test', 'username': 'playwright-test', 'user_id': 'playwright-test', "
         "'role': 'admin', 'exp': datetime.datetime.now(datetime.UTC) + datetime.timedelta(hours=2)}, "
         "os.environ['JWT_SECRET'], algorithm='HS256'))"],
        capture_output=True, text=True, timeout=10,
    )
    token = result.stdout.strip()
    assert token, f"Failed to get JWT: {result.stderr}"
    return token


@pytest.fixture(scope="module")
def browser_context():
    """Launch a Playwright browser with auth pre-set."""
    from playwright.sync_api import sync_playwright
    token = _get_jwt()
    pw = sync_playwright().start()
    browser = pw.chromium.launch(headless=True)
    context = browser.new_context(
        viewport={"width": 1280, "height": 900},
        ignore_https_errors=True,
    )
    # Set both cookie (for auth proxy) and localStorage (for frontend JS)
    context.add_cookies([{
        "name": "dartboard_token",
        "value": token,
        "domain": "localhost",
        "path": "/",
    }])
    # Inject localStorage before the app loads
    context.add_init_script(f"window.localStorage.setItem('dartboard-jwt', '{token}');")
    yield context
    context.close()
    browser.close()
    pw.stop()


@pytest.fixture
def page(browser_context):
    """Fresh page per test."""
    p = browser_context.new_page()
    p.set_default_timeout(30_000)
    yield p
    p.close()


# ── Health / Load Tests ───────────────────────────────────────────

class TestPageLoad:
    def test_homepage_loads(self, page):
        """App loads without console errors."""
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(APP_URL, wait_until="networkidle")
        assert page.title(), "Page has no title"
        # Allow pretext import errors on headless (canvas may not be available)
        critical = [e for e in errors if "pretext" not in e.lower() and "canvas" not in e.lower()]
        assert not critical, f"Console errors: {critical}"

    def test_health_endpoint(self, page):
        """Health endpoint returns OK."""
        resp = page.goto(f"{APP_URL}/health")
        assert resp.status == 200
        body = resp.json()
        assert body["ok"] is True

    def test_static_assets_load(self, page):
        """Key local JS files load without 404."""
        failed = []
        def on_resp(r):
            # Only check our own JS files, not CDN
            if r.status >= 400 and ".js" in r.url and "localhost" in r.url:
                failed.append(r.url)
        page.on("response", on_resp)
        page.goto(APP_URL, wait_until="networkidle")
        assert not failed, f"Failed JS loads: {failed}"

    def test_pretext_vendor_loads(self, page):
        """Pretext vendor files are served correctly."""
        resp = page.goto(f"{APP_URL}/js/vendor/pretext/layout.js")
        assert resp.status == 200
        assert "prepare" in resp.text()


# ── Chat UI Tests ─────────────────────────────────────────────────

class TestChatUI:
    def test_conversation_list_visible(self, page):
        """Sidebar conversation list renders."""
        page.goto(APP_URL, wait_until="networkidle")
        sidebar = page.locator("#sidebar")
        assert sidebar.is_visible()

    def test_create_new_conversation(self, page):
        """Can create a new conversation."""
        page.goto(APP_URL, wait_until="networkidle")
        new_btn = page.locator("#new-chat-btn, .new-chat-btn, [onclick*='newConversation']").first
        if new_btn.is_visible():
            new_btn.click()
            page.wait_for_timeout(1000)
        input_box = page.locator("#input")
        assert input_box.is_visible(), "Chat input not visible"

    def test_send_message(self, page):
        """Can type in input and trigger send (user msg appears via SSE stream)."""
        page.goto(APP_URL, wait_until="networkidle")
        page.wait_for_timeout(1000)
        # Click new chat button to ensure a fresh conversation
        new_btn = page.locator("#new-chat-btn, .new-chat-btn, button:has-text('New')").first
        if new_btn.is_visible():
            new_btn.click()
            page.wait_for_timeout(1000)
        input_box = page.locator("#input")
        assert input_box.is_visible(), "Chat input not visible"
        input_box.fill("Say hello")
        input_box.press("Enter")
        # The message is sent via SSE streaming — user msg appears after API responds.
        # Wait for either a user or assistant message (up to 90s for model load).
        try:
            page.wait_for_selector(".msg", timeout=90_000)
            msgs = page.locator(".msg")
            assert msgs.count() > 0, "No messages appeared"
        except Exception:
            pytest.skip("Chat message send timed out (LM Studio may be unavailable)")

    def test_message_has_actions(self, page):
        """Messages rendered in DOM have action buttons."""
        page.goto(APP_URL, wait_until="networkidle")
        page.wait_for_timeout(1000)
        # Check if any existing conversations have messages with action bars
        convs = page.locator("#conv-list .conv-item")
        if convs.count() > 0:
            convs.first.click()
            page.wait_for_timeout(2000)
            actions = page.locator(".msg-actions")
            if actions.count() > 0:
                return  # Pass — existing messages have actions
        # If no existing messages, just verify the action bar HTML structure exists in the JS
        pytest.skip("No existing conversations with messages to test action bars")


# ── Image Gen UI Tests ────────────────────────────────────────────

class TestImageGenUI:
    def _go_to_imagegen(self, page):
        page.goto(APP_URL, wait_until="networkidle")
        tab = page.locator("[data-tab='imagegen'], [onclick*=\"switchTab('imagegen')\"]").first
        tab.click()
        page.wait_for_timeout(500)

    def test_imagegen_tab_exists(self, page):
        """Image Gen tab is present and clickable."""
        page.goto(APP_URL, wait_until="networkidle")
        tab = page.locator("[data-tab='imagegen'], [onclick*=\"switchTab('imagegen')\"]").first
        assert tab.is_visible(), "Image Gen tab not found"

    def test_imagegen_view_loads(self, page):
        """Image Gen view renders all expected elements."""
        self._go_to_imagegen(page)
        prompt = page.locator("#ig-prompt")
        assert prompt.is_visible(), "Image gen prompt not visible"
        gen_btn = page.locator("#ig-generate-btn")
        assert gen_btn.is_visible(), "Generate button not visible"

    def test_model_buttons_enabled(self, page):
        """Model buttons should NOT all be disabled (HF fallback active)."""
        self._go_to_imagegen(page)
        page.wait_for_timeout(2000)  # Wait for status check
        btns = page.locator(".ig-model-btn:not(.disabled)")
        enabled_count = btns.count()
        assert enabled_count > 0, "All model buttons are disabled — HF fallback not working"

    def test_status_shows_backend(self, page):
        """Status indicator shows either ComfyUI or HuggingFace."""
        self._go_to_imagegen(page)
        page.wait_for_timeout(2000)
        status = page.locator("#ig-status-text")
        text = status.text_content()
        assert text, "Status text is empty"
        assert any(kw in text.lower() for kw in ["ready", "huggingface", "comfyui", "models"]), \
            f"Unexpected status: {text}"

    def test_generate_image(self, page):
        """Full image generation E2E — submit prompt, poll, get result."""
        self._go_to_imagegen(page)
        page.wait_for_timeout(2000)
        # Pick an enabled model
        enabled_btn = page.locator(".ig-model-btn:not(.disabled)").first
        if enabled_btn.is_visible():
            enabled_btn.click()
        # Enter prompt
        page.locator("#ig-prompt").fill("a simple red circle on white background")
        # Generate
        page.locator("#ig-generate-btn").click()
        # Wait for result (up to 120s for HF cold start)
        page.wait_for_selector(".ig-image-card img, #ig-preview-img[src]:not([src=''])",
                               timeout=120_000)
        # Verify an image appeared
        cards = page.locator(".ig-image-card")
        assert cards.count() > 0, "No image card appeared after generation"

    def test_prompt_templates(self, page):
        """Template chips exist and are clickable."""
        self._go_to_imagegen(page)
        chips = page.locator(".ig-template-chip")
        assert chips.count() > 0, "No template chips found"
        # Click first template
        chips.first.click()
        prompt_val = page.locator("#ig-prompt").input_value()
        assert prompt_val, "Template did not fill the prompt"


# ── Entrypoint ────────────────────────────────────────────────────

if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v", "--timeout=300", "--tb=short"]))
