"""
Unit tests for extracted MCP tool modules.

Runnable WITHOUT Docker — tests pure logic only, mocking all httpx calls.

Usage:
    python3 -m pytest tests/test_tool_modules.py -v
"""
from __future__ import annotations

import json
import sys

# Make the tools package importable from the project root.
sys.path.insert(0, "docker/mcp")

import pytest


# ---------------------------------------------------------------------------
# MockResponse — used in json_or_err tests
# ---------------------------------------------------------------------------

class MockResponse:
    def __init__(self, status_code: int, json_data=None, text: str = ""):
        self.status_code = status_code
        self._json = json_data
        self.text = text

    def json(self):
        if self._json is None:
            raise ValueError("No JSON")
        return self._json


# ===========================================================================
# _helpers.py
# ===========================================================================

class TestText:
    def test_returns_list_with_one_item(self):
        from tools._helpers import text
        result = text("hello world")
        assert isinstance(result, list)
        assert len(result) == 1

    def test_item_has_type_text(self):
        from tools._helpers import text
        block = text("some content")[0]
        assert block["type"] == "text"

    def test_item_has_correct_text_value(self):
        from tools._helpers import text
        block = text("my content")[0]
        assert block["text"] == "my content"

    def test_empty_string(self):
        from tools._helpers import text
        block = text("")[0]
        assert block["text"] == ""

    def test_multiline_string(self):
        from tools._helpers import text
        s = "line1\nline2\nline3"
        block = text(s)[0]
        assert block["text"] == s


class TestJsonOrErr:
    def test_2xx_with_json_returns_serialised_json(self):
        from tools._helpers import json_or_err
        r = MockResponse(200, json_data={"key": "value"})
        result = json_or_err(r, "my_tool")
        assert result[0]["type"] == "text"
        parsed = json.loads(result[0]["text"])
        assert parsed == {"key": "value"}

    def test_2xx_non_json_returns_error_message(self):
        from tools._helpers import json_or_err
        r = MockResponse(200, json_data=None, text="plain text response")
        result = json_or_err(r, "my_tool")
        assert "non-JSON" in result[0]["text"]
        assert "my_tool" in result[0]["text"]
        assert "200" in result[0]["text"]

    def test_4xx_returns_error_with_status_code(self):
        from tools._helpers import json_or_err
        r = MockResponse(404, text="Not Found")
        result = json_or_err(r, "my_tool")
        assert "404" in result[0]["text"]
        assert "my_tool" in result[0]["text"]

    def test_5xx_returns_error_with_status_code(self):
        from tools._helpers import json_or_err
        r = MockResponse(500, text="Internal Server Error")
        result = json_or_err(r, "my_tool")
        assert "500" in result[0]["text"]
        assert "my_tool" in result[0]["text"]

    def test_4xx_includes_response_body_truncated(self):
        from tools._helpers import json_or_err
        long_body = "x" * 500
        r = MockResponse(422, text=long_body)
        result = json_or_err(r, "validation_tool")
        # Body is truncated to 300 chars per implementation
        assert len(result[0]["text"]) < 600

    def test_2xx_returns_mcp_content_block_list(self):
        from tools._helpers import json_or_err
        r = MockResponse(201, json_data=[1, 2, 3])
        result = json_or_err(r, "tool")
        assert isinstance(result, list)
        assert result[0]["type"] == "text"

    def test_exact_400_boundary_is_error(self):
        from tools._helpers import json_or_err
        r = MockResponse(400, text="Bad Request")
        result = json_or_err(r, "tool")
        assert "400" in result[0]["text"]

    def test_exact_399_boundary_is_success(self):
        from tools._helpers import json_or_err
        r = MockResponse(399, json_data={"ok": True})
        result = json_or_err(r, "tool")
        parsed = json.loads(result[0]["text"])
        assert parsed == {"ok": True}


class TestResolveImagePath:
    def test_empty_string_returns_none(self):
        from tools._helpers import resolve_image_path
        assert resolve_image_path("") is None

    def test_none_returns_none(self):
        from tools._helpers import resolve_image_path
        assert resolve_image_path(None) is None  # type: ignore[arg-type]

    def test_nonexistent_absolute_path_returns_none(self):
        from tools._helpers import resolve_image_path
        assert resolve_image_path("/nonexistent/does/not/exist/image.png") is None

    def test_nonexistent_bare_filename_returns_none(self):
        from tools._helpers import resolve_image_path
        # BROWSER_WORKSPACE won't contain this file in CI / host
        assert resolve_image_path("totally_made_up_file_xyz.png") is None


# ===========================================================================
# _search.py — pure functions, no network calls
# ===========================================================================

class TestDhash:
    def test_returns_empty_string_when_haspil_false(self):
        from unittest.mock import patch
        from tools._search import dhash
        from PIL import Image
        img = Image.new("RGB", (64, 64), color="blue")
        with patch("tools._search._HAS_PIL", False):
            result = dhash(img)
        assert result == ""

    def test_returns_16_char_hex_when_pil_available(self):
        from tools._search import dhash
        from tools._imaging import HAS_PIL
        if not HAS_PIL:
            pytest.skip("PIL not available in this environment")
        from PIL import Image
        img = Image.new("RGB", (64, 64), color="red")
        result = dhash(img)
        assert len(result) == 16
        # Must be valid hex
        int(result, 16)

    def test_different_images_may_differ(self):
        from tools._search import dhash
        from tools._imaging import HAS_PIL
        if not HAS_PIL:
            pytest.skip("PIL not available in this environment")
        from PIL import Image
        white = Image.new("L", (64, 64), color=255)
        black = Image.new("L", (64, 64), color=0)
        h_white = dhash(white)
        h_black = dhash(black)
        # Both are 16-char hex; they differ (white > black gradient)
        assert len(h_white) == 16
        assert len(h_black) == 16


class TestHamming:
    def test_empty_strings_return_64(self):
        from tools._search import hamming
        assert hamming("", "") == 64

    def test_one_empty_string_returns_64(self):
        from tools._search import hamming
        assert hamming("0000000000000000", "") == 64
        assert hamming("", "0000000000000000") == 64

    def test_wrong_length_returns_64(self):
        from tools._search import hamming
        assert hamming("abc", "def") == 64
        assert hamming("0" * 15, "0" * 16) == 64

    def test_identical_hashes_return_zero(self):
        from tools._search import hamming
        h = "deadbeefcafebabe"
        assert hamming(h, h) == 0

    def test_all_bits_different_returns_64(self):
        from tools._search import hamming
        assert hamming("0000000000000000", "ffffffffffffffff") == 64

    def test_one_bit_different_returns_one(self):
        from tools._search import hamming
        assert hamming("0000000000000000", "0000000000000001") == 1

    def test_known_distance(self):
        from tools._search import hamming
        # 0x1 ^ 0x3 = 0x2 = 1 bit set
        h1 = "0000000000000001"
        h2 = "0000000000000003"
        assert hamming(h1, h2) == 1


class TestDomainFromUrl:
    def test_strips_www_prefix(self):
        from tools._search import domain_from_url
        assert domain_from_url("https://www.example.com/path") == "example.com"

    def test_preserves_subdomain_other_than_www(self):
        from tools._search import domain_from_url
        assert domain_from_url("https://sub.example.com/path") == "sub.example.com"

    def test_lowercases_hostname(self):
        from tools._search import domain_from_url
        assert domain_from_url("https://EXAMPLE.COM/Path") == "example.com"

    def test_empty_string_returns_empty(self):
        from tools._search import domain_from_url
        assert domain_from_url("") == ""

    def test_non_url_returns_empty(self):
        from tools._search import domain_from_url
        assert domain_from_url("not-a-url") == ""

    def test_bare_hostname(self):
        from tools._search import domain_from_url
        assert domain_from_url("https://example.com") == "example.com"


class TestNormalizeSearchQuery:
    def test_fixes_kluki_typo(self):
        from tools._search import normalize_search_query
        query, note = normalize_search_query("kluki artwork")
        assert "Klukai" in query
        assert note != ""

    def test_fixes_girls_frontline2_compact(self):
        from tools._search import normalize_search_query
        # Compact form (no spaces) should be expanded and normalized.
        query, note = normalize_search_query("girlsfrontline2 gameplay")
        assert "Girls Frontline 2" in query
        assert note != ""

    def test_spaced_girls_frontline_2_passes_through(self):
        from tools._search import normalize_search_query
        # Already-spaced form is case-insensitively equal after the fix,
        # so the implementation returns the original untouched (no note).
        query, note = normalize_search_query("girls frontline 2 gameplay")
        assert query == "girls frontline 2 gameplay"
        assert note == ""

    def test_passthrough_normal_query(self):
        from tools._search import normalize_search_query
        query, note = normalize_search_query("python programming tutorial")
        assert query == "python programming tutorial"
        assert note == ""

    def test_empty_query(self):
        from tools._search import normalize_search_query
        query, note = normalize_search_query("")
        assert query == ""
        assert note == ""

    def test_collapses_extra_whitespace(self):
        from tools._search import normalize_search_query
        query, _ = normalize_search_query("  multiple   spaces   ")
        assert "  " not in query
        assert query == query.strip()

    def test_case_insensitive_kluki(self):
        from tools._search import normalize_search_query
        query, note = normalize_search_query("KLUKI fanart")
        assert "Klukai" in query
        assert note != ""


class TestSearchTerms:
    def test_returns_lowercase_tokens(self):
        from tools._search import search_terms
        terms = search_terms("Girls Frontline 2 Gameplay")
        assert "girls" in terms
        assert "frontline" in terms
        assert "gameplay" in terms

    def test_filters_short_words(self):
        from tools._search import search_terms
        # Words shorter than 3 chars are excluded
        terms = search_terms("a ab abc abcd")
        assert "a" not in terms
        assert "ab" not in terms
        assert "abc" in terms
        assert "abcd" in terms

    def test_empty_query(self):
        from tools._search import search_terms
        assert search_terms("") == []

    def test_none_safe(self):
        from tools._search import search_terms
        assert search_terms(None) == []  # type: ignore[arg-type]

    def test_strips_non_alphanumeric(self):
        from tools._search import search_terms
        terms = search_terms("hello-world foo_bar test.py")
        # Regex [a-z0-9]{3,} — hyphens/underscores/dots are delimiters
        assert "hello" in terms
        assert "world" in terms
        assert "foo" in terms
        assert "bar" in terms


class TestScoreUrlRelevance:
    def test_matching_terms_increase_score(self):
        from tools._search import score_url_relevance
        terms = ["python", "tutorial"]
        score_match = score_url_relevance("https://example.com/python-tutorial", terms)
        score_no_match = score_url_relevance("https://example.com/random-page", terms)
        assert score_match > score_no_match

    def test_preferred_domain_adds_bonus(self):
        from tools._search import score_url_relevance
        terms = ["query"]
        preferred = ("preferred.com",)
        score_preferred = score_url_relevance("https://preferred.com/page", terms, preferred)
        score_other = score_url_relevance("https://other.com/page", terms, preferred)
        assert score_preferred > score_other

    def test_empty_url_returns_zero(self):
        from tools._search import score_url_relevance
        assert score_url_relevance("", ["term"]) == 0

    def test_no_terms_returns_zero(self):
        from tools._search import score_url_relevance
        assert score_url_relevance("https://example.com/page", []) == 0

    def test_subdomain_of_preferred_domain_also_gets_bonus(self):
        from tools._search import score_url_relevance
        terms: list[str] = []
        preferred = ("preferred.com",)
        score = score_url_relevance("https://sub.preferred.com/page", terms, preferred)
        assert score > 0


class TestUnwrapDdgRedirect:
    def test_unwraps_uddg_parameter(self):
        from tools._search import unwrap_ddg_redirect
        url = "https://duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fpage&rut=abc"
        result = unwrap_ddg_redirect(url)
        assert result == "https://example.com/page"

    def test_returns_original_if_not_ddg(self):
        from tools._search import unwrap_ddg_redirect
        url = "https://example.com/normal"
        assert unwrap_ddg_redirect(url) == url

    def test_handles_protocol_relative_url(self):
        from tools._search import unwrap_ddg_redirect
        url = "//duckduckgo.com/l/?uddg=https%3A%2F%2Ftarget.com"
        result = unwrap_ddg_redirect(url)
        assert result == "https://target.com"

    def test_empty_string_returns_empty(self):
        from tools._search import unwrap_ddg_redirect
        assert unwrap_ddg_redirect("") == ""

    def test_non_redirect_ddg_url_returned_as_is(self):
        from tools._search import unwrap_ddg_redirect
        url = "https://duckduckgo.com/search?q=test"
        result = unwrap_ddg_redirect(url)
        # Not a /l/ redirect — returned unchanged
        assert result == url


class TestExtractDdgLinks:
    def test_parses_result_a_anchor(self):
        from tools._search import extract_ddg_links
        html = '<a class="result__a" href="https://example.com/page">Example Title</a>'
        links = extract_ddg_links(html)
        assert len(links) == 1
        url, title = links[0]
        assert url == "https://example.com/page"
        assert title == "Example Title"

    def test_unwraps_ddg_redirect_in_href(self):
        from tools._search import extract_ddg_links
        html = '<a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Ftarget.com">Target</a>'
        links = extract_ddg_links(html)
        assert links[0][0] == "https://target.com"

    def test_fallback_uddg_extraction(self):
        from tools._search import extract_ddg_links
        # uddg without a result__a anchor — fallback path
        html = 'some text uddg=https%3A%2F%2Ffallback.com more text'
        links = extract_ddg_links(html)
        assert any(u == "https://fallback.com" for u, _ in links)

    def test_deduplicates_urls(self):
        from tools._search import extract_ddg_links
        html = (
            '<a class="result__a" href="https://example.com">First</a>'
            '<a class="result__a" href="https://example.com">Duplicate</a>'
        )
        links = extract_ddg_links(html)
        urls = [u for u, _ in links]
        assert len(urls) == len(set(urls))

    def test_respects_max_results(self):
        from tools._search import extract_ddg_links
        # Generate 20 unique anchors
        html = "".join(
            f'<a class="result__a" href="https://example{i}.com">Title {i}</a>'
            for i in range(20)
        )
        links = extract_ddg_links(html, max_results=5)
        assert len(links) <= 5

    def test_empty_html_returns_empty_list(self):
        from tools._search import extract_ddg_links
        assert extract_ddg_links("") == []

    def test_skips_non_http_hrefs(self):
        from tools._search import extract_ddg_links
        html = '<a class="result__a" href="/relative/path">Internal</a>'
        assert extract_ddg_links(html) == []


class TestExtractBingLinks:
    def test_parses_b_algo_result(self):
        from tools._search import extract_bing_links
        html = (
            '<li class="b_algo">'
            '<h2><a href="https://example.com">Bing Result Title</a></h2>'
            "</li>"
        )
        links = extract_bing_links(html)
        assert len(links) >= 1
        url, title = links[0]
        assert url == "https://example.com"
        assert title == "Bing Result Title"

    def test_empty_html_returns_empty_list(self):
        from tools._search import extract_bing_links
        assert extract_bing_links("") == []

    def test_deduplicates_urls(self):
        from tools._search import extract_bing_links
        block = (
            '<li class="b_algo"><h2>'
            '<a href="https://dup.com">Title</a>'
            "</h2></li>"
        )
        links = extract_bing_links(block + block)
        urls = [u for u, _ in links]
        assert len(urls) == len(set(urls))

    def test_respects_max_results(self):
        from tools._search import extract_bing_links
        html = "".join(
            f'<li class="b_algo"><h2><a href="https://r{i}.com">R{i}</a></h2></li>'
            for i in range(20)
        )
        links = extract_bing_links(html, max_results=3)
        assert len(links) <= 3

    def test_skips_non_http_hrefs(self):
        from tools._search import extract_bing_links
        html = '<li class="b_algo"><h2><a href="/relative">Title</a></h2></li>'
        assert extract_bing_links(html) == []


class TestUrlHasExplicitContent:
    def test_always_returns_false(self):
        from tools._search import url_has_explicit_content
        # Filter is disabled — all calls must return False
        assert url_has_explicit_content("https://explicit-domain.com") is False
        assert url_has_explicit_content("https://normal.com") is False
        assert url_has_explicit_content("") is False
        assert url_has_explicit_content("https://example.com", text="any text") is False


# ===========================================================================
# Module registration — TOOL_HANDLERS must be populated after import
# ===========================================================================

class TestToolHandlersRegistration:
    """Verify that importing each module registers its handlers into TOOL_HANDLERS."""

    def _get_handlers(self) -> dict:
        # Re-import to get the current (populated) registry
        # Each test class gets a clean check; we rely on module-level imports above
        from tools import TOOL_HANDLERS  # type: ignore[import]
        return TOOL_HANDLERS

    def test_memory_handlers_registered(self):
        import tools.memory  # noqa: F401
        handlers = self._get_handlers()
        assert "memory_store" in handlers
        assert "memory_recall" in handlers

    def test_knowledge_handlers_registered(self):
        import tools.knowledge  # noqa: F401
        handlers = self._get_handlers()
        for name in ("graph_add_node", "graph_add_edge", "graph_query", "graph_path", "graph_search"):
            assert name in handlers, f"Expected '{name}' in TOOL_HANDLERS"

    def test_planner_handlers_registered(self):
        import tools.planner  # noqa: F401
        handlers = self._get_handlers()
        for name in (
            "plan_create_task", "plan_get_task", "plan_complete_task",
            "plan_fail_task", "plan_list_tasks", "plan_delete_task",
            "job_status", "job_result", "job_list", "think",
        ):
            assert name in handlers, f"Expected '{name}' in TOOL_HANDLERS"

    def test_code_handlers_registered(self):
        import tools.code  # noqa: F401
        handlers = self._get_handlers()
        for name in ("code_run", "run_javascript", "jupyter_exec"):
            assert name in handlers, f"Expected '{name}' in TOOL_HANDLERS"

    def test_data_handlers_registered(self):
        import tools.data  # noqa: F401
        handlers = self._get_handlers()
        for name in (
            "db_store_article", "db_search", "db_cache_store",
            "db_cache_get", "db_store_image", "get_errors",
        ):
            assert name in handlers, f"Expected '{name}' in TOOL_HANDLERS"

    def test_all_handlers_are_callable(self):
        import tools.memory, tools.knowledge, tools.planner, tools.code, tools.data  # noqa: F401, E401
        handlers = self._get_handlers()
        for name, fn in handlers.items():
            assert callable(fn), f"Handler '{name}' is not callable"
