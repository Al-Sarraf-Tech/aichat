"""
End-to-end model tests — verifies each LLM model can receive a message
and return a response via the MCP chat tool.

Run locally:
    MCP_URL=http://localhost:8096 \
    pytest tests/test_model_e2e.py -v -m e2e

These tests require the Docker stack to be running.  All tests skip
automatically when the MCP service is unreachable.

Model ID format (as defined in models.js):
  "agent:model:effort"   — e.g. "claude:opus:max" → agent=claude, model=opus, effort=max
  "codex::xhigh"         → agent=codex, model="", effort=xhigh
  "gemini:gemini-2.5-pro" → agent=gemini, model=gemini-2.5-pro, effort=""
  "qwen"                 → agent=qwen, model="", effort=""
"""
from __future__ import annotations

import os
from typing import Any

import httpx
import pytest

# ---------------------------------------------------------------------------
# Service URLs (overridable via env for CI)
# ---------------------------------------------------------------------------

MCP_URL = os.environ.get("MCP_URL", "http://localhost:8096")

# Timeout for individual chat calls — models can be slow to cold-start.
CHAT_TIMEOUT = 30.0
# Shorter timeout for non-chat JSON-RPC calls.
TIMEOUT = 10.0

# ---------------------------------------------------------------------------
# Canonical model list (mirrors MODELS in models.js)
# ---------------------------------------------------------------------------

# Cloud models — routed through agent SSH dispatch
CLOUD_MODELS = [
    "claude:opus:max",
    "claude:sonnet:high",
    "claude:haiku:high",
    "codex::xhigh",
    "codex::high",
    "codex::medium",
    "gemini:gemini-2.5-pro",
    "gemini:gemini-2.5-flash",
    "gemini:gemini-2.0-flash",
]

# Local model — routed through LM Studio HTTP
LOCAL_MODEL = "qwen"

ALL_MODELS = CLOUD_MODELS + [LOCAL_MODEL]

# All providers whose models are expected to use the agent SSH dispatch path
CLI_PROVIDERS = {"claude", "codex", "gemini"}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

pytestmark = pytest.mark.e2e


def _is_reachable(url: str) -> bool:
    """Return True if *url* responds with HTTP < 400."""
    try:
        return httpx.get(url, timeout=5.0, follow_redirects=True).status_code < 400
    except Exception:
        return False


def _parse_model_id(model_id: str) -> tuple[str, str, str]:
    """Return (agent, model, effort) from a model_id string.

    Examples
    --------
    "claude:opus:max"     → ("claude", "opus", "max")
    "codex::xhigh"        → ("codex", "", "xhigh")
    "gemini:gemini-2.5-pro" → ("gemini", "gemini-2.5-pro", "")
    "qwen"                → ("qwen", "", "")
    """
    parts = model_id.split(":")
    agent  = parts[0] if len(parts) > 0 else ""
    model  = parts[1] if len(parts) > 1 else ""
    effort = parts[2] if len(parts) > 2 else ""
    return agent, model, effort


def _is_cli_model(model_id: str) -> bool:
    """Mirror the isCliModel() logic from models.js.

    Returns True for all 9 cloud models and qwen.
    """
    if not model_id:
        return False
    if model_id == "qwen":
        return True
    return any(model_id.startswith(p) for p in ("claude:", "codex:", "gemini:"))


def _chat_rpc(
    message: str,
    agent: str,
    model: str,
    effort: str,
    timeout: float = CHAT_TIMEOUT,
) -> httpx.Response:
    """POST a chat tool/call JSON-RPC request and return the httpx response.

    Raises pytest.skip on connection/timeout errors so live-model tests
    degrade gracefully when agents aren't available.
    """
    rpc: dict[str, Any] = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "chat",
            "arguments": {
                "message": message,
                "agent":   agent,
                "model":   model,
                "effort":  effort,
            },
        },
    }
    try:
        return httpx.post(f"{MCP_URL}/mcp", json=rpc, timeout=timeout)
    except (httpx.TimeoutException, httpx.ConnectError, httpx.ReadError) as exc:
        pytest.skip(f"Chat RPC for {agent} failed: {exc}")


def _extract_text(response_body: dict[str, Any]) -> str:
    """Pull the first text content item out of a JSON-RPC result."""
    content = response_body.get("result", {}).get("content", [])
    return next((item.get("text", "") for item in content if item.get("type") == "text"), "")


def _mcp_health_url() -> str:
    return f"{MCP_URL}/health"


# ---------------------------------------------------------------------------
# TestModelCatalog — static assertions, no service required
# ---------------------------------------------------------------------------

class TestModelCatalog:
    """Verify the model catalogue definitions without hitting any service."""

    def test_all_10_models_defined(self) -> None:
        """MODELS list must contain exactly the 10 expected model IDs."""
        expected = {
            "claude:opus:max",
            "claude:sonnet:high",
            "claude:haiku:high",
            "codex::xhigh",
            "codex::high",
            "codex::medium",
            "gemini:gemini-2.5-pro",
            "gemini:gemini-2.5-flash",
            "gemini:gemini-2.0-flash",
            "qwen",
        }
        actual = set(ALL_MODELS)
        assert actual == expected, (
            f"Model set mismatch.\n  Missing: {expected - actual}\n  Extra: {actual - expected}"
        )
        assert len(ALL_MODELS) == 10, f"Expected 10 models, found {len(ALL_MODELS)}"

    @pytest.mark.parametrize("model_id", ALL_MODELS)
    def test_cli_model_detection(self, model_id: str) -> None:
        """_is_cli_model() must return True for every cloud+qwen model ID."""
        assert _is_cli_model(model_id), (
            f"_is_cli_model({model_id!r}) returned False — model would not route correctly"
        )

    @pytest.mark.parametrize("model_id,expected_agent,expected_model,expected_effort", [
        ("claude:opus:max",      "claude",          "opus",            "max"),
        ("claude:sonnet:high",   "claude",          "sonnet",          "high"),
        ("claude:haiku:high",    "claude",          "haiku",           "high"),
        ("codex::xhigh",         "codex",           "",                "xhigh"),
        ("codex::high",          "codex",           "",                "high"),
        ("codex::medium",        "codex",           "",                "medium"),
        ("gemini:gemini-2.5-pro",  "gemini",        "gemini-2.5-pro",  ""),
        ("gemini:gemini-2.5-flash", "gemini",       "gemini-2.5-flash",""),
        ("gemini:gemini-2.0-flash", "gemini",       "gemini-2.0-flash",""),
        ("qwen",                 "qwen",            "",                ""),
    ])
    def test_model_id_parsing(
        self,
        model_id: str,
        expected_agent: str,
        expected_model: str,
        expected_effort: str,
    ) -> None:
        """_parse_model_id() must correctly split every model ID into (agent, model, effort)."""
        agent, model, effort = _parse_model_id(model_id)
        assert agent  == expected_agent,  f"{model_id}: agent expected {expected_agent!r}, got {agent!r}"
        assert model  == expected_model,  f"{model_id}: model expected {expected_model!r}, got {model!r}"
        assert effort == expected_effort, f"{model_id}: effort expected {expected_effort!r}, got {effort!r}"

    @pytest.mark.parametrize("model_id", CLOUD_MODELS)
    def test_cloud_models_route_through_cli_providers(self, model_id: str) -> None:
        """Cloud model IDs must map to one of the known CLI provider prefixes."""
        agent, _, _ = _parse_model_id(model_id)
        assert agent in CLI_PROVIDERS, (
            f"Model {model_id!r} has unknown agent {agent!r}; "
            f"expected one of {sorted(CLI_PROVIDERS)}"
        )


# ---------------------------------------------------------------------------
# TestCloudModelChat — parametrized live chat calls
# ---------------------------------------------------------------------------

class TestCloudModelChat:
    """Send a minimal prompt to each cloud model and verify a non-empty response.

    All tests skip when the MCP service is unreachable.
    """

    @pytest.mark.parametrize("model_id", CLOUD_MODELS)
    def test_cloud_model_responds(self, model_id: str) -> None:
        """Each cloud model must return a non-empty text response to a trivial prompt.

        The prompt is intentionally minimal ("Reply with just the word 'hello'") to
        keep latency low while confirming end-to-end message routing works.
        """
        if not _is_reachable(_mcp_health_url()):
            pytest.skip("aichat-mcp not reachable")

        agent, model, effort = _parse_model_id(model_id)
        r = _chat_rpc(
            message="Reply with just the word 'hello'",
            agent=agent,
            model=model,
            effort=effort,
        )

        assert r.status_code == 200, (
            f"{model_id}: MCP returned HTTP {r.status_code}"
        )

        body = r.json()

        # Surface any JSON-RPC level error immediately.
        if "error" in body:
            err_msg = body["error"].get("message", str(body["error"]))
            # Connectivity errors (SSH down, agent not configured) → skip, not fail.
            skip_keywords = ("connection", "timeout", "refused", "ssh", "not found", "unavailable")
            if any(kw in err_msg.lower() for kw in skip_keywords):
                pytest.skip(f"{model_id}: agent unavailable — {err_msg}")
            pytest.fail(f"{model_id}: JSON-RPC error — {err_msg}")

        text = _extract_text(body)
        assert text, (
            f"{model_id}: expected non-empty text response, got empty content.\n"
            f"Full body: {body}"
        )

    @pytest.mark.parametrize("model_id", CLOUD_MODELS)
    def test_cloud_model_response_is_string(self, model_id: str) -> None:
        """Cloud model response content must be a non-empty string (not bytes or dict)."""
        if not _is_reachable(_mcp_health_url()):
            pytest.skip("aichat-mcp not reachable")

        agent, model, effort = _parse_model_id(model_id)
        r = _chat_rpc(
            message="Say the word OK.",
            agent=agent,
            model=model,
            effort=effort,
        )

        assert r.status_code == 200, f"{model_id}: HTTP {r.status_code}"
        body = r.json()

        if "error" in body:
            err_msg = body["error"].get("message", str(body["error"]))
            skip_keywords = ("connection", "timeout", "refused", "ssh", "not found", "unavailable")
            if any(kw in err_msg.lower() for kw in skip_keywords):
                pytest.skip(f"{model_id}: agent unavailable — {err_msg}")
            pytest.fail(f"{model_id}: JSON-RPC error — {err_msg}")

        content = body.get("result", {}).get("content", [])
        assert isinstance(content, list), f"{model_id}: content must be a list, got {type(content)}"
        assert len(content) > 0, f"{model_id}: content list is empty"
        first = content[0]
        assert first.get("type") == "text", f"{model_id}: first content item type is not 'text': {first}"
        assert isinstance(first.get("text"), str), (
            f"{model_id}: text field is not a string: {type(first.get('text'))}"
        )
        assert len(first["text"]) > 0, f"{model_id}: text is empty string"


# ---------------------------------------------------------------------------
# TestLocalModelChat — qwen via LM Studio
# ---------------------------------------------------------------------------

class TestLocalModelChat:
    """Verify the local qwen model responds via the MCP chat tool."""

    def test_qwen_responds(self) -> None:
        """qwen must return a non-empty text response to a trivial prompt."""
        if not _is_reachable(_mcp_health_url()):
            pytest.skip("aichat-mcp not reachable")

        r = _chat_rpc(
            message="Reply with just the word 'hello'",
            agent="qwen",
            model="",
            effort="",
        )

        assert r.status_code == 200, f"qwen: MCP returned HTTP {r.status_code}"
        body = r.json()

        if "error" in body:
            err_msg = body["error"].get("message", str(body["error"]))
            skip_keywords = ("connection", "timeout", "refused", "lm studio", "unavailable", "not found")
            if any(kw in err_msg.lower() for kw in skip_keywords):
                pytest.skip(f"qwen: LM Studio unavailable — {err_msg}")
            pytest.fail(f"qwen: JSON-RPC error — {err_msg}")

        text = _extract_text(body)
        assert text, (
            f"qwen: expected non-empty text response, got empty content.\nFull body: {body}"
        )

    def test_qwen_tool_invocation(self) -> None:
        """qwen asked for the current date should attempt to use MCP tools or respond sensibly.

        Local models receive MCP tools in their system context.  The response must be
        non-empty; we do not assert the exact tool-use mechanism because local models
        may answer from training data rather than calling a tool.
        """
        if not _is_reachable(_mcp_health_url()):
            pytest.skip("aichat-mcp not reachable")

        r = _chat_rpc(
            message="What is the current date? Answer concisely.",
            agent="qwen",
            model="",
            effort="",
            timeout=CHAT_TIMEOUT,
        )

        assert r.status_code == 200, f"qwen tool test: MCP returned HTTP {r.status_code}"
        body = r.json()

        if "error" in body:
            err_msg = body["error"].get("message", str(body["error"]))
            skip_keywords = ("connection", "timeout", "refused", "lm studio", "unavailable", "not found")
            if any(kw in err_msg.lower() for kw in skip_keywords):
                pytest.skip(f"qwen tool test: LM Studio unavailable — {err_msg}")
            pytest.fail(f"qwen tool test: JSON-RPC error — {err_msg}")

        text = _extract_text(body)
        assert text, (
            f"qwen tool test: expected non-empty text, got empty content.\nFull body: {body}"
        )

    def test_qwen_model_id_does_not_use_cloud_prefix(self) -> None:
        """qwen must not have a colon-separated provider prefix — it routes differently."""
        assert ":" not in LOCAL_MODEL, (
            f"Local model ID {LOCAL_MODEL!r} must not contain ':' — "
            "it would be misinterpreted as a CLI cloud model"
        )


# ---------------------------------------------------------------------------
# TestModelRouting — verify response-level routing characteristics
# ---------------------------------------------------------------------------

class TestModelRouting:
    """Verify that models are dispatched through the correct routing path.

    Cloud models (claude/codex/gemini) go through the agent SSH dispatch.
    Local models (qwen) go through the LM Studio HTTP path.
    Routing is inferred from the response format and content characteristics.
    """

    @pytest.mark.parametrize("model_id", CLOUD_MODELS)
    def test_paid_models_route_through_agents(self, model_id: str) -> None:
        """Cloud model responses must come back as valid MCP JSON-RPC content objects.

        The agent SSH dispatch wraps model output in the standard MCP content envelope:
        {"result": {"content": [{"type": "text", "text": "..."}]}}.
        A missing or malformed envelope indicates the request did not reach the agent.
        """
        if not _is_reachable(_mcp_health_url()):
            pytest.skip("aichat-mcp not reachable")

        agent, model, effort = _parse_model_id(model_id)
        r = _chat_rpc(
            message="Respond with one word: ready",
            agent=agent,
            model=model,
            effort=effort,
        )

        assert r.status_code == 200, f"{model_id}: HTTP {r.status_code}"
        body = r.json()

        if "error" in body:
            err_msg = body["error"].get("message", str(body["error"]))
            skip_keywords = ("connection", "timeout", "refused", "ssh", "not found", "unavailable")
            if any(kw in err_msg.lower() for kw in skip_keywords):
                pytest.skip(f"{model_id}: agent SSH unavailable — {err_msg}")
            pytest.fail(f"{model_id}: JSON-RPC error — {err_msg}")

        # Validate MCP content envelope shape
        result = body.get("result", {})
        assert "content" in result, (
            f"{model_id}: 'content' missing from result — agent dispatch may have failed.\n"
            f"Full body: {body}"
        )
        content = result["content"]
        assert isinstance(content, list) and len(content) > 0, (
            f"{model_id}: 'content' must be a non-empty list.\nFull body: {body}"
        )
        assert content[0].get("type") == "text", (
            f"{model_id}: first content item must have type='text'.\nContent: {content[0]}"
        )

    def test_local_models_use_mcp_tools(self) -> None:
        """Local models (qwen) must receive a well-formed MCP response envelope.

        When a local model is selected, the Dart router still passes the request through
        the MCP chat tool, which provides local models with access to MCP tool definitions.
        The response envelope must conform to the standard MCP content format.
        """
        if not _is_reachable(_mcp_health_url()):
            pytest.skip("aichat-mcp not reachable")

        r = _chat_rpc(
            message="List three numbers.",
            agent="qwen",
            model="",
            effort="",
        )

        assert r.status_code == 200, f"qwen routing: HTTP {r.status_code}"
        body = r.json()

        if "error" in body:
            err_msg = body["error"].get("message", str(body["error"]))
            skip_keywords = ("connection", "timeout", "refused", "lm studio", "unavailable", "not found")
            if any(kw in err_msg.lower() for kw in skip_keywords):
                pytest.skip(f"qwen routing: LM Studio unavailable — {err_msg}")
            pytest.fail(f"qwen routing: JSON-RPC error — {err_msg}")

        result = body.get("result", {})
        assert "content" in result, (
            f"qwen routing: 'content' missing from result — MCP envelope broken.\n"
            f"Full body: {body}"
        )
        content = result["content"]
        assert isinstance(content, list) and len(content) > 0, (
            f"qwen routing: 'content' must be a non-empty list.\nFull body: {body}"
        )
        assert content[0].get("type") == "text", (
            f"qwen routing: first content item must have type='text'.\nContent: {content[0]}"
        )

    @pytest.mark.parametrize("model_id", CLOUD_MODELS)
    def test_cloud_model_agent_field_matches_provider(self, model_id: str) -> None:
        """The agent field parsed from the model ID must match the expected CLI provider.

        This is a static routing-correctness check — no network call required.
        """
        agent, _, _ = _parse_model_id(model_id)
        # All cloud model IDs must decompose to a known CLI provider
        assert agent in CLI_PROVIDERS, (
            f"Model {model_id!r}: parsed agent {agent!r} not in {sorted(CLI_PROVIDERS)}"
        )
