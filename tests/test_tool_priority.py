"""
Tool routing and priority contract tests — no Docker required.

Validates:
  - All 25 tools registered in MCP app
  - Modular tool files export async handle()
  - Mega-tools carry action enum in schema
  - Model profiles (strong/mid/small) have expected tool counts
  - Every profile includes web and image tools
  - Rule-based routing (image-only, web, code, fallback)
  - CLI model detection (_isCliModel equivalent logic)
  - Tool catalog integrity (no duplicates, descriptions, no paid APIs)

Run with:
    pytest tests/test_tool_priority.py -v
"""
from __future__ import annotations

import importlib
import importlib.util
import inspect
import re
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Repo root
# ---------------------------------------------------------------------------

_REPO = Path(__file__).parent.parent

# Make docker/mcp importable (needed for modular tool imports)
_MCP_DIR = _REPO / "docker" / "mcp"
if str(_MCP_DIR) not in sys.path:
    sys.path.insert(0, str(_MCP_DIR))


# ---------------------------------------------------------------------------
# Load docker/mcp/app.py (reuse pattern from test_architecture.py)
# ---------------------------------------------------------------------------

def _load_mcp():
    mod_name = "_prio_mcp_app"
    spec = importlib.util.spec_from_file_location(
        mod_name, _REPO / "docker" / "mcp" / "app.py"
    )
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

# Standalone tools: use their own schema (NOT the mega-tool action pattern)
_STANDALONE_TOOLS = {
    "chat", "image_pipeline", "workspace",
    "ssh", "monitor", "git", "notify", "iot", "log",
}

# The 6 modular tool files (each must export an async handle() function)
_MODULAR_TOOL_FILES = ["git", "iot", "log", "monitor", "notify", "ssh"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tool_names() -> set[str]:
    return {t["name"] for t in _TOOLS}


def _schema_for(name: str) -> dict:
    for t in _TOOLS:
        if t["name"] == name:
            return t.get("inputSchema", {})
    pytest.skip(f"Tool '{name}' not found in _TOOLS")
    return {}


# ---------------------------------------------------------------------------
# 1. TestToolRegistration
# ---------------------------------------------------------------------------

@skip_load
class TestToolRegistration:
    """Architecture-level: tool names, handle() exports, schema shapes."""

    def test_all_25_tools_registered(self):
        """Platform must expose exactly 25 tools."""
        count = len(_TOOLS)
        assert count == 25, (
            f"Expected 25 tools (16 mega + chat + image_pipeline + workspace + "
            f"6 modular), got {count}."
        )

    def test_modular_tools_have_handle_function(self):
        """Each modular tool file (git, iot, log, monitor, notify, ssh) must
        export an async handle() coroutine function."""
        for mod_name in _MODULAR_TOOL_FILES:
            mod = importlib.import_module(f"tools.{mod_name}")
            assert hasattr(mod, "handle"), (
                f"tools/{mod_name}.py missing handle() export"
            )
            assert inspect.iscoroutinefunction(mod.handle), (
                f"tools/{mod_name}.handle must be an async function"
            )

    def test_tool_schemas_have_action_enum(self):
        """All mega-tools (not think, not standalone) must carry an action
        property with an enum."""
        for t in _TOOLS:
            name = t["name"]
            if name == "think" or name in _STANDALONE_TOOLS:
                continue
            props = t.get("inputSchema", {}).get("properties", {})
            assert "action" in props, (
                f"Mega-tool '{name}' missing action property"
            )
            assert "enum" in props["action"], (
                f"Mega-tool '{name}' action property missing enum"
            )


# ---------------------------------------------------------------------------
# 2. TestModelToolProfiles — parse model_profiles.dart
# ---------------------------------------------------------------------------

def _parse_dart_profiles() -> dict[str, list[str]]:
    """Parse _builtinProfiles from model_profiles.dart.

    Returns mapping of model_id → list of allowedTools (or [] for unrestricted).
    """
    src = (_REPO / "lib" / "model_profiles.dart").read_text()
    profiles: dict[str, list[str]] = {}

    # Each profile block: 'model-id': ModelProfile(... allowedTools: [...] ...)
    # Strategy: find model id strings, then the nearest allowedTools list.
    block_re = re.compile(
        r"'([^']+)':\s*ModelProfile\((.*?)\),",
        re.DOTALL,
    )
    tools_re = re.compile(
        r"allowedTools:\s*\[(.*?)\]",
        re.DOTALL,
    )
    tool_name_re = re.compile(r"'(\w+)'")

    for m in block_re.finditer(src):
        model_id = m.group(1)
        body = m.group(2)
        tm = tools_re.search(body)
        if tm:
            tools = tool_name_re.findall(tm.group(1))
        else:
            # null / unrestricted
            tools = []
        profiles[model_id] = tools

    return profiles


_DART_PROFILES = _parse_dart_profiles()


class TestModelToolProfiles:
    """Validate that tool count tiers in model_profiles.dart match spec."""

    def test_strong_models_have_9_tools(self):
        """gpt-oss-20b, dolphin-24b, qwen3.5-27b each get 9 tools."""
        strong = [
            "gpt-oss-20b-absolute-heresy-i1",
            "cognitivecomputations_dolphin-mistral-24b-venice-edition",
            "qwen3.5-27b-claude-4.6-opus-reasoning-distilled-v2",
        ]
        for model_id in strong:
            assert model_id in _DART_PROFILES, (
                f"Model '{model_id}' not found in parsed dart profiles"
            )
            tools = _DART_PROFILES[model_id]
            assert len(tools) == 9, (
                f"Expected 9 tools for strong model '{model_id}', got {len(tools)}: {tools}"
            )

    def test_mid_models_have_7_tools(self):
        """qwen/qwen3.5-9b and gemma-4-26b each get 7 tools."""
        mid = [
            "qwen/qwen3.5-9b",
            "gemma-4-26b-a4b-it",
        ]
        for model_id in mid:
            assert model_id in _DART_PROFILES, (
                f"Model '{model_id}' not found in parsed dart profiles"
            )
            tools = _DART_PROFILES[model_id]
            assert len(tools) == 7, (
                f"Expected 7 tools for mid model '{model_id}', got {len(tools)}: {tools}"
            )

    def test_small_models_have_5_tools(self):
        """gemma-4-e2b-it gets 5 tools (minimal reliable set)."""
        model_id = "gemma-4-e2b-it"
        assert model_id in _DART_PROFILES, (
            f"Model '{model_id}' not found in parsed dart profiles"
        )
        tools = _DART_PROFILES[model_id]
        assert len(tools) == 5, (
            f"Expected 5 tools for small model '{model_id}', got {len(tools)}: {tools}"
        )

    def test_all_profiles_include_web_and_image(self):
        """Every built-in profile that specifies allowedTools must include
        both 'web' and 'image'."""
        for model_id, tools in _DART_PROFILES.items():
            if not tools:
                # Unrestricted (null allowedTools) — implicitly includes all
                continue
            assert "web" in tools, (
                f"Model '{model_id}' allowedTools missing 'web': {tools}"
            )
            assert "image" in tools, (
                f"Model '{model_id}' allowedTools missing 'image': {tools}"
            )


# ---------------------------------------------------------------------------
# 3. TestRuleBasedRouting — parse tool_router.dart rule patterns
# ---------------------------------------------------------------------------

def _parse_dart_rules() -> dict[str, list[str]]:
    """Parse _rules map from tool_router.dart.

    Returns mapping of tool_name → list of keyword patterns.
    """
    src = (_REPO / "lib" / "tool_router.dart").read_text()

    # Find the _rules const block.
    # Declaration: const _rules = <String, List<String>>{  ...  };
    # Outer regex: grab everything between the opening brace and the
    # closing "};" that ends the const declaration.
    m = re.search(r"const _rules\s*=\s*<[^{]+>\{(.*?)\};", src, re.DOTALL)
    assert m, "_rules map not found in tool_router.dart"

    rules_body = m.group(1)
    # Each entry: 'tool_name': [ 'kw1', 'kw2', ... ],
    entry_re = re.compile(
        r"'(\w+)':\s*\[(.*?)\]",
        re.DOTALL,
    )
    kw_re = re.compile(r"'([^']+)'")

    rules: dict[str, list[str]] = {}
    for em in entry_re.finditer(rules_body):
        tool = em.group(1)
        keywords = kw_re.findall(em.group(2))
        rules[tool] = keywords
    return rules


_DART_RULES = _parse_dart_rules()


def _rule_route(message: str, available: set[str]) -> set[str]:
    """Python re-implementation of _ruleRoute from tool_router.dart."""
    msg = message.lower()
    matched: set[str] = set()

    for tool, keywords in _DART_RULES.items():
        if tool not in available:
            continue
        for kw in keywords:
            if kw in msg:
                matched.add(tool)
                break

    # Image-only rule: image w/o research → strip everything else
    if "image" in matched and "research" not in matched:
        return {"image"}

    # Research gets web as backup
    if "research" in matched and "web" in available:
        matched.add("web")

    # Fallback: nothing matched → web
    if not matched and "web" in available:
        matched.add("web")

    return matched


_ALL_TOOLS = {
    "web", "image", "browser", "research", "code", "document",
    "media", "memory", "knowledge", "vector", "data", "planner",
    "think", "system", "jobs", "custom_tools",
}


class TestRuleBasedRouting:
    """Validate rule-based keyword routing mirrors tool_router.dart logic."""

    def test_image_keywords_route_to_image_only(self):
        """Requests mentioning image keywords must route to ONLY the image tool."""
        messages = [
            "generate an image of a sunset",
            "find images of cats",
            "show me some artwork",
            "I want a wallpaper",
        ]
        for msg in messages:
            result = _rule_route(msg, _ALL_TOOLS)
            assert result == {"image"}, (
                f"Expected {{'image'}} only for '{msg}', got {result}"
            )

    def test_web_keywords_route_to_web(self):
        """Requests with web-search keywords route to web tool."""
        messages = [
            "search for the latest news",
            "what is machine learning",
            "tell me about quantum computing",
            "how to make pasta",
        ]
        for msg in messages:
            result = _rule_route(msg, _ALL_TOOLS)
            assert "web" in result, (
                f"Expected 'web' in routing for '{msg}', got {result}"
            )

    def test_code_keywords_route_to_code(self):
        """Code/execution keywords route to code tool."""
        messages = [
            "run this python code",
            "execute the script",
            "write a javascript function",
        ]
        for msg in messages:
            result = _rule_route(msg, _ALL_TOOLS)
            assert "code" in result, (
                f"Expected 'code' in routing for '{msg}', got {result}"
            )

    def test_fallback_routes_to_web(self):
        """Completely unrecognised messages fall back to web."""
        messages = [
            "zzz123 xyzzy unknown gibberish",
            "hello there",
            "do the thing",
        ]
        for msg in messages:
            result = _rule_route(msg, _ALL_TOOLS)
            assert result == {"web"}, (
                f"Expected fallback to {{'web'}} for '{msg}', got {result}"
            )


# ---------------------------------------------------------------------------
# 4. TestCliModelDetection — parse chat_handler.dart _isCliModel
# ---------------------------------------------------------------------------

def _parse_cli_model_logic() -> tuple[list[str], list[str]]:
    """Parse _isCliModel from chat_handler.dart.

    Returns (prefix_list, exact_list) from the boolean expression.
    """
    src = (_REPO / "lib" / "chat_handler.dart").read_text()

    m = re.search(
        r"static bool _isCliModel\(String model\)\s*\{(.*?)\}",
        src,
        re.DOTALL,
    )
    assert m, "_isCliModel not found in chat_handler.dart"
    body = m.group(1)

    # Extract startsWith(...) prefixes
    prefix_re = re.compile(r"model\.startsWith\('([^']+)'\)")
    prefixes = prefix_re.findall(body)

    # Extract model == '...' exact matches
    exact_re = re.compile(r"model\s*==\s*'([^']+)'")
    exacts = exact_re.findall(body)

    return prefixes, exacts


_CLI_PREFIXES, _CLI_EXACTS = _parse_cli_model_logic()


def _is_cli_model(model: str) -> bool:
    """Python mirror of _isCliModel from chat_handler.dart."""
    return (
        any(model.startswith(p) for p in _CLI_PREFIXES)
        or model in _CLI_EXACTS
    )


class TestCliModelDetection:
    """Validate CLI model detection matches chat_handler.dart _isCliModel."""

    def test_cloud_models_are_cli(self):
        """All claude:*/codex:*/gemini:* prefixes must be detected as CLI."""
        cloud_models = [
            "claude:opus:max",
            "claude:sonnet",
            "codex::high",
            "codex:standard",
            "gemini:gemini-2.5-pro",
            "gemini:flash",
        ]
        for model in cloud_models:
            assert _is_cli_model(model), (
                f"Expected '{model}' to be detected as CLI model"
            )

    def test_qwen_is_cli(self):
        """The bare 'qwen' string must be detected as CLI model."""
        assert _is_cli_model("qwen"), (
            "'qwen' must be detected as CLI model"
        )

    def test_local_models_not_cli(self):
        """Local model IDs must NOT be detected as CLI models."""
        local_models = [
            "gemma-4-e2b-it",
            "gemma-4-26b-a4b-it",
            "qwen/qwen3.5-9b",
            "gpt-oss-20b-absolute-heresy-i1",
            "llama3-8b-instruct",
            "mistral-7b",
        ]
        for model in local_models:
            assert not _is_cli_model(model), (
                f"Expected '{model}' NOT to be detected as CLI model"
            )

    def test_api_models_handled(self):
        """api:* models are NOT CLI (routed via ApiClient, not SSH/CLI)."""
        api_models = [
            "api:claude:sonnet-4",
            "api:claude:opus-4",
            "api:openai:gpt-5.4",
            "api:google:gemini-2.5-pro",
        ]
        for model in api_models:
            assert not _is_cli_model(model), (
                f"Expected 'api:*' model '{model}' NOT to be CLI — handled by ApiClient"
            )


# ---------------------------------------------------------------------------
# 5. TestToolCatalogIntegrity
# ---------------------------------------------------------------------------

@skip_load
class TestToolCatalogIntegrity:
    """Structural and policy integrity of the MCP tool catalog."""

    def test_no_duplicate_tool_names(self):
        """No tool name may appear more than once in _TOOLS."""
        names = [t["name"] for t in _TOOLS]
        seen: set[str] = set()
        dupes = [n for n in names if n in seen or seen.add(n)]  # type: ignore[func-returns-value]
        assert not dupes, f"Duplicate tool names in _TOOLS: {dupes}"

    def test_all_tools_have_descriptions(self):
        """Every tool must carry a non-empty description field."""
        missing = [t["name"] for t in _TOOLS if not t.get("description", "").strip()]
        assert not missing, f"Tools missing description: {missing}"

    def test_no_paid_api_references(self):
        """No tool implementation file may reference paid external API keys
        (OpenAI, SauceNAO, Stability AI)."""
        paid_patterns = [
            r"saucenao",
            r"stability\.ai",
            r"api\.openai\.com",
            r"OPENAI_API_KEY",
            r"STABILITY_API_KEY",
            r"anime_search",
            r"anime_pipeline",
        ]
        pattern = re.compile("|".join(paid_patterns), re.IGNORECASE)

        offending: list[str] = []
        tools_dir = _MCP_DIR / "tools"
        for py_file in tools_dir.rglob("*.py"):
            # Skip telegram sub-package (third-party bot infra)
            if "telegram" in py_file.parts:
                continue
            content = py_file.read_text(errors="ignore")
            if pattern.search(content):
                offending.append(str(py_file.relative_to(_REPO)))

        assert not offending, (
            f"Paid API references found in tool files: {offending}"
        )
