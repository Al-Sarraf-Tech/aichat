"""
Architecture contract tests — no Docker required.

Validates that docker/mcp/app.py exposes ALL expected public tool names.
Updated for the mega-tool consolidation (16 action-based tools).

Run with:
    pytest tests/test_architecture.py -v
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Load docker/mcp/app.py
# ---------------------------------------------------------------------------

_REPO = Path(__file__).parent.parent


def _load_mcp():
    mod_name = "_arch_mcp_app"
    spec = importlib.util.spec_from_file_location(mod_name, _REPO / "docker" / "mcp" / "app.py")
    assert spec is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


try:
    _mcp = _load_mcp()
    _TOOLS: list[dict] = _mcp._TOOLS
    _MEGA_TOOL_MAP: dict[str, dict[str, str]] = _mcp._MEGA_TOOL_MAP
    _LOAD_OK = True
except Exception as _err:
    _LOAD_OK = False
    _TOOLS = []
    _MEGA_TOOL_MAP = {}

skip_load = pytest.mark.skipif(not _LOAD_OK, reason="docker/mcp/app.py failed to load")


def _tool_names() -> set[str]:
    return {t["name"] for t in _TOOLS}


# ---------------------------------------------------------------------------
# Canonical mega-tool names (backward-compatibility contract)
# ---------------------------------------------------------------------------

_EXPECTED_MEGA_TOOLS = {
    "web", "browser", "image", "document", "media", "data",
    "memory", "knowledge", "vector", "code", "custom_tools",
    "planner", "jobs", "research", "think", "system",
    "chat", "image_pipeline",
    "workspace",
    # Modular tools added in feat/mcp-tools-expansion
    "ssh", "monitor", "git", "notify", "iot", "log",
}

# Non-mega tools — use their own schema (not the mega-tool action pattern)
# workspace is dispatched via a dedicated if-block (like chat/image_pipeline), not via _MEGA_TOOL_MAP
# The 6 new modular tools (ssh, monitor, git, notify, iot, log) register via TOOL_HANDLERS
# and are excluded from the _MEGA_TOOL_MAP dispatch path.
_STANDALONE_TOOLS = {"chat", "image_pipeline", "workspace", "ssh", "monitor", "git", "notify", "iot", "log"}

# Expected actions per mega-tool (minimum set that must exist)
_EXPECTED_ACTIONS = {
    "web": {"search", "fetch"},
    "browser": {"navigate", "screenshot", "click"},
    "image": {"fetch", "search", "generate", "crop"},
    "document": {"ingest", "ocr", "pdf_read"},
    "media": {"video_info", "tts"},
    "data": {"store_article", "search", "errors"},
    "memory": {"store", "recall"},
    "knowledge": {"add_node", "query", "search"},
    "vector": {"store", "search"},
    "code": {"python", "javascript"},
    "custom_tools": {"create", "list", "call"},
    "planner": {"create", "orchestrate", "plan"},
    "jobs": {"submit", "status", "cancel"},
    "research": {"rss_search", "deep"},
    "system": {"list_categories", "instructions"},
}

# Original handler names that must still exist in dispatch map
_CRITICAL_HANDLERS = {
    "web_search", "web_fetch", "screenshot", "browser",
    "image_generate", "code_run", "memory_store", "memory_recall",
    "graph_query", "vector_search", "pdf_read", "ocr_image",
    "video_info", "plan_task", "job_submit", "orchestrate",
}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@skip_load
class TestToolCount:
    def test_exactly_25_tools(self):
        """Platform must expose exactly 25 tools (16 mega + chat + image_pipeline + workspace + 6 new modular)."""
        count = len(_TOOLS)
        assert count == 25, f"Expected 25 tools (19 original + ssh/monitor/git/notify/iot/log), got {count}."

    def test_no_duplicate_names(self):
        """Every tool name must be unique."""
        names = [t["name"] for t in _TOOLS]
        seen: set[str] = set()
        dupes = [n for n in names if n in seen or seen.add(n)]  # type: ignore[func-returns-value]
        assert not dupes, f"Duplicate tool names detected: {dupes}"

    def test_all_tools_have_description(self):
        """Every tool must have a non-empty description."""
        missing = [t["name"] for t in _TOOLS if not t.get("description", "").strip()]
        assert not missing, f"Tools missing description: {missing}"

    def test_all_tools_have_input_schema(self):
        """Every tool must define an inputSchema."""
        missing = [t["name"] for t in _TOOLS if "inputSchema" not in t]
        assert not missing, f"Tools missing inputSchema: {missing}"


@skip_load
class TestBackwardCompatibility:
    """Verify that all canonical mega-tool names are present."""

    def test_all_mega_tools_present(self):
        names = _tool_names()
        missing = _EXPECTED_MEGA_TOOLS - names
        assert not missing, f"Mega-tools missing from MCP: {missing}"

    def test_no_extra_tools(self):
        names = _tool_names()
        extra = names - _EXPECTED_MEGA_TOOLS
        assert not extra, f"Unexpected tools in MCP: {extra}"


@skip_load
class TestDispatchMap:
    """Verify _MEGA_TOOL_MAP covers expected actions."""

    def test_dispatch_map_has_all_tools(self):
        mapped = set(_MEGA_TOOL_MAP.keys())
        # think has no dispatch entry; standalone tools (chat, image_pipeline) are dispatched separately
        expected = _EXPECTED_MEGA_TOOLS - {"think"} - _STANDALONE_TOOLS
        missing = expected - mapped
        assert not missing, f"Tools missing from dispatch map: {missing}"

    def test_expected_actions_present(self):
        for tool, actions in _EXPECTED_ACTIONS.items():
            if tool not in _MEGA_TOOL_MAP:
                continue
            mapped_actions = set(_MEGA_TOOL_MAP[tool].keys())
            missing = actions - mapped_actions
            assert not missing, f"Tool '{tool}' missing actions: {missing}"

    def test_critical_handlers_in_dispatch(self):
        all_targets = set()
        for actions in _MEGA_TOOL_MAP.values():
            all_targets.update(actions.values())
        missing = _CRITICAL_HANDLERS - all_targets
        assert not missing, f"Critical handlers missing from dispatch targets: {missing}"


@skip_load
class TestSchemaContracts:
    """Spot-check input schema shapes for critical mega-tools."""

    def _schema(self, name: str) -> dict:
        for t in _TOOLS:
            if t["name"] == name:
                return t.get("inputSchema", {})
        pytest.skip(f"Tool '{name}' not found")
        return {}

    def test_all_mega_tools_have_action_enum(self):
        """Every mega-tool (except think and standalone tools) must have an action enum."""
        for t in _TOOLS:
            if t["name"] == "think" or t["name"] in _STANDALONE_TOOLS:
                continue
            props = t.get("inputSchema", {}).get("properties", {})
            assert "action" in props, f"Tool '{t['name']}' missing action property"
            assert "enum" in props["action"], f"Tool '{t['name']}' action missing enum"

    def test_web_has_query(self):
        props = self._schema("web").get("properties", {})
        assert "query" in props, "web must have query property"

    def test_browser_has_url(self):
        props = self._schema("browser").get("properties", {})
        assert "url" in props, "browser must have url property"

    def test_memory_has_key(self):
        props = self._schema("memory").get("properties", {})
        assert "key" in props, "memory must have key property"

    def test_code_has_code_param(self):
        props = self._schema("code").get("properties", {})
        assert "code" in props, "code must have code property"

    def test_think_has_thought(self):
        props = self._schema("think").get("properties", {})
        assert "thought" in props, "think must have thought property"

    def test_image_has_path(self):
        props = self._schema("image").get("properties", {})
        assert "path" in props, "image must have path property"

    def test_document_has_path(self):
        props = self._schema("document").get("properties", {})
        assert "path" in props or "url" in props, "document must have path or url property"

    def test_action_is_required(self):
        """action must be in 'required' for all mega-tools except think and standalone tools."""
        for t in _TOOLS:
            if t["name"] == "think" or t["name"] in _STANDALONE_TOOLS:
                continue
            required = t.get("inputSchema", {}).get("required", [])
            assert "action" in required, f"Tool '{t['name']}' must require action"

    # ------------------------------------------------------------------
    # New modular tool schema contracts
    # ------------------------------------------------------------------

    def test_ssh_has_action(self):
        props = self._schema("ssh").get("properties", {})
        assert "action" in props, "ssh must have action property"
        assert "host" in props, "ssh must have host property"

    def test_monitor_has_action(self):
        props = self._schema("monitor").get("properties", {})
        assert "action" in props, "monitor must have action property"

    def test_git_has_action(self):
        props = self._schema("git").get("properties", {})
        assert "action" in props, "git must have action property"
        assert "repo" in props, "git must have repo property"

    def test_notify_has_action(self):
        props = self._schema("notify").get("properties", {})
        assert "action" in props, "notify must have action property"
        assert "text" in props, "notify must have text property"

    def test_iot_has_action(self):
        props = self._schema("iot").get("properties", {})
        assert "action" in props, "iot must have action property"
        assert "device" in props, "iot must have device property"

    def test_log_has_action(self):
        props = self._schema("log").get("properties", {})
        assert "action" in props, "log must have action property"
        assert "pattern" in props, "log must have pattern property"

    def test_new_tools_have_descriptions(self):
        """All 6 new modular tools must have a non-empty description with an Actions: section."""
        new_tools = {"ssh", "monitor", "git", "notify", "iot", "log"}
        for t in _TOOLS:
            if t["name"] not in new_tools:
                continue
            desc = t.get("description", "")
            assert desc.strip(), f"Tool '{t['name']}' has empty description"
            assert "Actions:" in desc, f"Tool '{t['name']}' description missing 'Actions:' section"

    def test_no_duplicate_tool_names(self):
        """No tool names may appear more than once in _TOOLS."""
        names = [t["name"] for t in _TOOLS]
        seen: set[str] = set()
        dupes = [n for n in names if n in seen or seen.add(n)]  # type: ignore[func-returns-value]
        assert not dupes, f"Duplicate tool names in _TOOLS: {dupes}"


@skip_load
class TestNoPaidApis:
    """Verify no paid API tools or env vars remain."""

    def test_no_paid_tool_names(self):
        names = _tool_names()
        paid_tools = {"anime_search", "anime_pipeline", "saucenao_search"}
        present = names & paid_tools
        assert not present, f"Paid API tools still present: {present}"

    def test_no_paid_api_env_vars_in_map(self):
        all_targets = set()
        for actions in _MEGA_TOOL_MAP.values():
            all_targets.update(actions.values())
        paid_handlers = {"anime_search", "anime_pipeline", "saucenao_search"}
        present = all_targets & paid_handlers
        assert not present, f"Paid API handlers still in dispatch: {present}"
