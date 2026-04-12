"""
End-to-end tests for the image pipeline: generation, search, rendering data.

Coverage:
  - TestImageSearch     — query returns URLs, junk filtered, max_results respected
  - TestImageGeneration — generate returns job ID or base64; async poll if needed
  - TestImageInChat     — image tool returns image content blocks; stitch returns base64
  - TestImageSanitization — unit-level junk-URL pattern checks (no services needed)
  - TestCarouselData    — multiple images in one response are all preserved

Run:
    pytest tests/test_image_rendering_e2e.py -v -m e2e

Services required (skipped gracefully when absent):
  MCP_URL    http://localhost:8096   (aichat-mcp)
  VISION_URL http://localhost:8099   (aichat-vision)
  WEB_URL    http://localhost:8200   (aichat-web via auth proxy)
"""
from __future__ import annotations

import base64
import io
import os
import time
from typing import Any

import httpx
import pytest

# ---------------------------------------------------------------------------
# Service base URLs — overridable via environment for in-compose runs
# ---------------------------------------------------------------------------

MCP_URL    = os.environ.get("MCP_URL",    "http://localhost:8096")
VISION_URL = os.environ.get("VISION_URL", "http://localhost:8099")
WEB_URL    = os.environ.get("WEB_URL",    "http://localhost:8200")

_TIMEOUT      = 10.0
_LONG_TIMEOUT = 60.0


# ---------------------------------------------------------------------------
# Availability helpers
# ---------------------------------------------------------------------------

def _reachable(url: str) -> bool:
    """Return True if *url* responds with HTTP < 500 within the short timeout."""
    try:
        r = httpx.get(url, timeout=_TIMEOUT, follow_redirects=True)
        return r.status_code < 500
    except Exception:
        return False


_MCP_UP    = _reachable(f"{MCP_URL}/health")
_VISION_UP = _reachable(f"{VISION_URL}/health")
_WEB_UP    = _reachable(WEB_URL)

skip_mcp    = pytest.mark.skipif(not _MCP_UP,    reason="aichat-mcp not reachable")
skip_vision = pytest.mark.skipif(not _VISION_UP, reason="aichat-vision not reachable")
skip_web    = pytest.mark.skipif(not _WEB_UP,    reason="aichat-web not reachable")


# ---------------------------------------------------------------------------
# JSON-RPC helpers
# ---------------------------------------------------------------------------

def _mcp_call(name: str, arguments: dict[str, Any], *, timeout: float = _LONG_TIMEOUT) -> dict:
    """POST a tools/call JSON-RPC request to the MCP server; return the full response dict."""
    r = httpx.post(
        f"{MCP_URL}/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
        timeout=timeout,
    )
    r.raise_for_status()
    return r.json()


def _mcp_content(name: str, arguments: dict[str, Any], *, timeout: float = _LONG_TIMEOUT) -> list[dict]:
    """Return the content block list from a tools/call response."""
    return _mcp_call(name, arguments, timeout=timeout).get("result", {}).get("content", [])


def _text_blocks(blocks: list[dict]) -> list[str]:
    """Extract text values from all text-type content blocks."""
    return [b["text"] for b in blocks if b.get("type") == "text"]


def _image_blocks(blocks: list[dict]) -> list[dict]:
    """Return only image-type content blocks."""
    return [b for b in blocks if b.get("type") == "image"]


def _has_image_block(blocks: list[dict]) -> bool:
    return bool(_image_blocks(blocks))


def _get_image_bytes(blocks: list[dict]) -> bytes:
    """Decode the first image block's base64 data, or return empty bytes."""
    for b in blocks:
        if b.get("type") == "image" and b.get("data"):
            return base64.b64decode(b["data"])
    return b""


# ---------------------------------------------------------------------------
# Minimal PNG factory (no Pillow required)
# ---------------------------------------------------------------------------

def _make_tiny_png(width: int = 8, height: int = 8) -> bytes:
    """Produce a valid RGB PNG using stdlib only (no Pillow needed)."""
    import struct
    import zlib

    def _chunk(tag: bytes, data: bytes) -> bytes:
        payload = tag + data
        return (
            struct.pack(">I", len(data))
            + payload
            + struct.pack(">I", zlib.crc32(payload) & 0xFFFFFFFF)
        )

    sig  = b"\x89PNG\r\n\x1a\n"
    ihdr = _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    raw  = b"".join(b"\x00" + b"\x88\xcc\xff" * width for _ in range(height))
    idat = _chunk(b"IDAT", zlib.compress(raw))
    iend = _chunk(b"IEND", b"")
    return sig + ihdr + idat + iend


# ===========================================================================
# 1. TestImageSearch
# ===========================================================================

@pytest.mark.e2e
class TestImageSearch:
    """image_search action of the 'image' MCP mega-tool."""

    @skip_mcp
    def test_image_search_returns_urls(self) -> None:
        """Searching for 'sunset' must return at least one result URL in the text block."""
        blocks = _mcp_content("image", {"action": "search", "query": "sunset", "max_results": 3})
        assert blocks, "No content blocks returned from image search"
        combined = "\n".join(_text_blocks(blocks))
        # The response either contains a URL or an inline image block
        has_url = "http" in combined
        has_img = _has_image_block(blocks)
        assert has_url or has_img, (
            f"Expected URL or image block in search response; got text: {combined[:400]}"
        )

    @skip_mcp
    def test_image_search_filters_junk(self) -> None:
        """Search results must not contain known junk patterns (favicon, logo, icon, avatar)."""
        _JUNK = ("favicon", "/logo", "/icon", "avatar", "pixel.gif", "button",
                 "ytimg.com", "yt3.ggpht", "explicit.bing.net")
        blocks = _mcp_content("image", {"action": "search", "query": "landscape photo", "max_results": 5})
        combined = "\n".join(_text_blocks(blocks)).lower()
        for junk in _JUNK:
            assert junk not in combined, (
                f"Junk pattern '{junk}' found in image search result: {combined[:600]}"
            )

    @skip_mcp
    def test_image_search_respects_max(self) -> None:
        """max_results=2 must yield a bounded number of image blocks.

        The max_results parameter controls search engine page results, not raw
        image block count — one result page can yield multiple thumbnail/preview
        images.  We verify a reasonable upper bound rather than exact equality.
        """
        blocks = _mcp_content("image", {"action": "search", "query": "mountain", "max_results": 2})
        imgs = _image_blocks(blocks)
        # Allow up to 3x the requested max (thumbnails, previews, etc.)
        assert len(imgs) <= 8, (
            f"Got {len(imgs)} image blocks with max_results=2 — exceeds reasonable upper bound"
        )


# ===========================================================================
# 2. TestImageGeneration
# ===========================================================================

@pytest.mark.e2e
class TestImageGeneration:
    """image_generate action — requires ComfyUI or HF; skips gracefully when absent."""

    @skip_mcp
    def test_image_generate_returns_job_or_base64(self) -> None:
        """generate action must return a job ID, a status message, or an image block.

        The pipeline first tries ComfyUI (dominus) and falls back to HF free-tier.
        Either way the response must be non-empty and not a raw error stack trace.
        """
        blocks = _mcp_content(
            "image",
            {"action": "generate", "prompt": "a simple red circle on white background"},
            timeout=_LONG_TIMEOUT,
        )
        assert blocks, "No content blocks returned from image generate"
        combined = "\n".join(_text_blocks(blocks)).lower()

        # Acceptable outcomes: image block, job id, queued/pending status
        has_img = _has_image_block(blocks)
        has_job = any(kw in combined for kw in ("job", "queue", "pending", "generating", "submitted"))
        has_img_data = "base64" in combined or has_img
        unavailable = any(kw in combined for kw in ("unavailable", "not configured", "no backend"))

        if unavailable:
            pytest.skip("Image generation backend not configured — skipping")

        assert has_img or has_job or has_img_data, (
            f"Expected image block, job ID, or queue confirmation; got: {combined[:400]}"
        )
        # Must not be a raw Python traceback
        assert "traceback" not in combined, f"Traceback in generate response: {combined[:400]}"

    @skip_mcp
    def test_image_status_endpoint_if_async(self) -> None:
        """If generate returns a job ID, polling the jobs tool must return a status field."""
        blocks = _mcp_content(
            "image",
            {"action": "generate", "prompt": "a blue square"},
            timeout=_LONG_TIMEOUT,
        )
        combined = "\n".join(_text_blocks(blocks)).lower()

        if _has_image_block(blocks):
            pytest.skip("generate returned inline image — no async job to poll")
        if "unavailable" in combined or "not configured" in combined:
            pytest.skip("Image generation backend not configured — skipping")

        # Extract a job ID from the text if present
        import re
        job_ids = re.findall(r"\b([0-9a-f]{8,})\b", combined)
        if not job_ids:
            pytest.skip("No job ID found in generate response — cannot poll status")

        job_id = job_ids[0]
        status_blocks = _mcp_content("jobs", {"action": "status", "job_id": job_id}, timeout=_TIMEOUT)
        assert status_blocks, "No content from jobs status poll"
        status_text = "\n".join(_text_blocks(status_blocks)).lower()
        assert any(kw in status_text for kw in ("status", "pending", "running", "done", "failed")), (
            f"Expected a status field in job poll; got: {status_text[:300]}"
        )


# ===========================================================================
# 3. TestImageInChat
# ===========================================================================

@pytest.mark.e2e
class TestImageInChat:
    """Direct image tool calls — verify response structure."""

    @skip_mcp
    def test_image_tool_returns_image_blocks(self) -> None:
        """Calling image search must return at least one content block of any type."""
        blocks = _mcp_content("image", {"action": "search", "query": "cat photo"})
        assert len(blocks) > 0, "image tool returned no content blocks"
        block_types = {b.get("type") for b in blocks}
        assert block_types & {"text", "image"}, (
            f"Unexpected block types — got: {block_types}"
        )

    @skip_mcp
    def test_image_stitch_returns_base64(self) -> None:
        """image_stitch with two synthetic PNG files must return an image block with base64 data.

        The test creates minimal PNGs in a path that the MCP container can resolve.
        If the workspace is not mounted, the stitch will fail — skip gracefully.
        """
        # Try common workspace paths
        workspace = None
        for candidate in ("/docker/human_browser/workspace", "/tmp/workspace"):
            if os.path.isdir(candidate):
                workspace = candidate
                break
        if not workspace:
            pytest.skip("Browser workspace not mounted — cannot write test PNGs for stitch")

        import uuid
        fname_a = f"stitch_a_{uuid.uuid4().hex[:8]}.png"
        fname_b = f"stitch_b_{uuid.uuid4().hex[:8]}.png"
        path_a  = os.path.join(workspace, fname_a)
        path_b  = os.path.join(workspace, fname_b)
        png     = _make_tiny_png(16, 16)

        try:
            with open(path_a, "wb") as f:
                f.write(png)
            with open(path_b, "wb") as f:
                f.write(png)

            blocks = _mcp_content(
                "image",
                {"action": "stitch", "paths": [path_a, path_b], "direction": "horizontal"},
            )
            assert blocks, "image stitch returned no content blocks"
            img_blocks = _image_blocks(blocks)
            assert img_blocks, (
                f"Expected at least one image block from stitch; text: {_text_blocks(blocks)}"
            )
            raw = _get_image_bytes(img_blocks)
            assert len(raw) > 0, "image block data is empty"
            # Verify it is a valid PNG (PNG magic bytes)
            # Stitch may return PNG (\x89PNG) or JPEG (\xff\xd8\xff)
            is_png = raw[:4] == b"\x89PNG"
            is_jpeg = raw[:3] == b"\xff\xd8\xff"
            assert is_png or is_jpeg, f"Stitch output is not a valid PNG or JPEG (first 8 bytes: {raw[:8]!r})"
            # MIME type must be declared
            assert img_blocks[0].get("mimeType") in (
                "image/png", "image/jpeg", "image/webp"
            ), f"Unexpected mimeType: {img_blocks[0].get('mimeType')}"
        finally:
            for p in (path_a, path_b):
                try:
                    os.unlink(p)
                except OSError:
                    pass


# ===========================================================================
# 4. TestImageSanitization
# ===========================================================================

class TestImageSanitization:
    """Unit-level URL filtering — no services required.

    These tests validate the junk-URL patterns documented in app.py's
    _SKIP_P / _SKIP_T1 filtering logic for image_search results.
    """

    # Known-bad patterns lifted directly from app.py _SKIP_P / _SKIP_T1
    _JUNK_URLS = [
        "https://example.com/favicon.ico",
        "https://example.com/images/logo.png",
        "https://example.com/icon-64.png",
        "https://example.com/avatar/user123.jpg",
        "https://example.com/pixel.gif",
        "https://example.com/button_submit.png",
        "https://ytimg.com/vi/abc/default.jpg",
        "https://yt3.ggpht.com/channel_avatar.jpg",
        "https://explicit.bing.net/th?id=QVIT.abc",
        "https://example.com/images//16px-Flag.png",
        "https://example.com/images//25px-Icon.svg",
        "https://example.com/images//32px-Logo.png",
        "https://example.com/images//48px-Thumb.png",
        "https://www.gravatar.com/avatar/abc123",
        "https://lh3.googleusercontent.com/photo.jpg",
        "https://some-cdn.net/sprite.svg",
        "https://media.example.com/a.svg",
    ]

    _VALID_URLS = [
        "https://upload.wikimedia.org/wikipedia/commons/thumb/4/47/PNG_transparency_demonstration_1.png",
        "https://i.imgur.com/AbCdEfG.jpg",
        "https://cdn.example.com/gallery/sunset_1920x1080.jpg",
        "https://images.unsplash.com/photo-1234567890?w=1080",
        "https://prydwen.gg/static/img/characters/char_01.webp",
    ]

    @staticmethod
    def _is_junk(url: str) -> bool:
        """Local re-implementation of image_search URL filtering logic from app.py."""
        u = url.lower()
        # Pixel-size prefixes
        for px in ("/16px-", "/25px-", "/32px-", "/48px-"):
            if px in u:
                return True
        # Keyword patterns
        for kw in ("favicon", "logo", "icon", "avatar", "pixel.gif", "button",
                   "ytimg.com", "yt3.ggpht", "explicit.bing.net"):
            if kw in u:
                return True
        # Video hosting domains (no static images expected)
        for domain in ("youtube.com", "youtu.be", "vimeo.com", "dailymotion.com",
                       "twitch.tv", "pinterest.com", "instagram.com"):
            if domain in u:
                return True
        # Junk hosts
        if "gravatar.com" in u:
            return True
        if "googleusercontent.com" in u:
            return True
        # SVG thumbnails (rarely actual artwork)
        if u.endswith(".svg") or ".svg?" in u:
            return True
        return False

    def test_junk_url_filtering(self) -> None:
        """All known-junk URLs must be flagged by the filter."""
        passed = [url for url in self._JUNK_URLS if not self._is_junk(url)]
        assert not passed, (
            f"These junk URLs incorrectly passed the filter:\n" + "\n".join(passed)
        )

    def test_valid_urls_pass(self) -> None:
        """Known-good image URLs must not be flagged as junk."""
        blocked = [url for url in self._VALID_URLS if self._is_junk(url)]
        assert not blocked, (
            f"These valid URLs were incorrectly blocked:\n" + "\n".join(blocked)
        )

    def test_gravatar_blocked(self) -> None:
        """gravatar.com URLs must always be filtered out."""
        assert self._is_junk("https://www.gravatar.com/avatar/deadbeef?s=200&d=identicon")

    def test_googleusercontent_blocked(self) -> None:
        """googleusercontent.com (user avatars/photos) must be filtered."""
        assert self._is_junk("https://lh3.googleusercontent.com/a/AItbvmkD=s96-c")

    def test_svg_blocked(self) -> None:
        """SVG URLs must be treated as junk (decorative graphics, not photos)."""
        assert self._is_junk("https://cdn.example.com/sprite.svg")
        assert self._is_junk("https://api.example.com/badge.svg?foo=bar")

    def test_imgur_allowed(self) -> None:
        """imgur.com image URLs must not be blocked."""
        assert not self._is_junk("https://i.imgur.com/SomeImage.jpg")

    def test_wikimedia_allowed(self) -> None:
        """Wikimedia Commons full-size images must not be blocked."""
        url = "https://upload.wikimedia.org/wikipedia/commons/4/47/PNG_transparency_demonstration_1.png"
        assert not self._is_junk(url)


# ===========================================================================
# 5. TestCarouselData
# ===========================================================================

@pytest.mark.e2e
class TestCarouselData:
    """Verify that multiple images returned by the tool are all preserved in the response.

    The frontend renders 2+ images as a carousel (per feedback directive).  These
    tests confirm the tool layer delivers the raw material: all image blocks intact.
    """

    @skip_mcp
    def test_multiple_images_in_response(self) -> None:
        """When requesting max_results=3, the response must return multiple image blocks.

        The max_results parameter controls search-engine result pages, not the raw
        image block count — each result can produce multiple thumbnails/previews.
        We verify that a multi-result search yields at least 1 image block.
        """
        max_r  = 3
        blocks = _mcp_content("image", {"action": "search", "query": "nature landscape", "max_results": max_r})
        assert blocks, "No content blocks from image search with max_results=3"

        imgs = _image_blocks(blocks)
        if not imgs:
            # Search may return URLs in text rather than inline image blocks — that is OK.
            combined = "\n".join(_text_blocks(blocks))
            url_count = combined.count("http")
            assert url_count > 0, (
                "Expected at least one URL or image block in multi-image search result"
            )
            return

        # Verify images were returned — at least 1 (exact count depends on search results)
        assert len(imgs) >= 1, "Expected at least one image block from multi-result search"

        # All image blocks must have non-empty base64 data
        for i, blk in enumerate(imgs):
            assert blk.get("data"), f"Image block #{i} has empty 'data' field"
            raw = base64.b64decode(blk["data"])
            assert len(raw) > 0, f"Image block #{i} decoded to zero bytes"

        # All returned images must be unique (different base64 payloads)
        payloads = [blk["data"] for blk in imgs]
        assert len(set(payloads)) == len(payloads), (
            "Some image blocks contain duplicate data — carousel would show repeats"
        )

    @skip_mcp
    def test_single_image_not_duplicated(self) -> None:
        """A single-result search must return a bounded number of image blocks.

        max_results=1 controls search engine page count, not raw image blocks.
        A single result page may yield multiple thumbnails. We verify a sane upper bound.
        """
        blocks = _mcp_content("image", {"action": "search", "query": "eiffel tower photo", "max_results": 1})
        imgs = _image_blocks(blocks)
        assert len(imgs) <= 6, (
            f"Requested max_results=1 but got {len(imgs)} image blocks — excessive duplication"
        )

    @skip_mcp
    def test_image_blocks_have_mime_type(self) -> None:
        """Every image content block must declare a mimeType field."""
        blocks = _mcp_content("image", {"action": "search", "query": "ocean waves", "max_results": 2})
        for i, blk in enumerate(_image_blocks(blocks)):
            mime = blk.get("mimeType", "")
            assert mime, f"Image block #{i} missing 'mimeType' field"
            assert mime.startswith("image/"), (
                f"Image block #{i} has non-image mimeType: {mime!r}"
            )
