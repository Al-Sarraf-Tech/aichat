"""
Shared helpers for extracted tool modules.

These are thin wrappers that every tool needs for producing MCP content
blocks and making HTTP requests to backend services.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any

import httpx


def text(s: str) -> list[dict[str, Any]]:
    """Return a single MCP text content block."""
    return [{"type": "text", "text": s}]


def json_or_err(r: httpx.Response, tool: str) -> list[dict[str, Any]]:
    """Return .json() as text, or an error message if the status code is not 2xx."""
    if r.status_code >= 400:
        return text(f"{tool}: upstream returned {r.status_code} — {r.text[:300]}")
    try:
        return text(json.dumps(r.json()))
    except Exception:
        return text(f"{tool}: upstream returned {r.status_code} (non-JSON)")


def get_client(timeout: float = 60) -> httpx.AsyncClient:
    """Create an httpx AsyncClient with the standard timeout."""
    return httpx.AsyncClient(timeout=timeout)


# ── Service URLs (read once from environment) ─────────────────────
MEMORY_URL    = os.environ.get("MEMORY_URL",    "http://aichat-data:8091/memory")
GRAPH_URL     = os.environ.get("GRAPH_URL",     "http://aichat-data:8091/graph")
DATABASE_URL  = os.environ.get("DATABASE_URL",  "http://aichat-data:8091")
VECTOR_URL    = os.environ.get("VECTOR_URL",    "http://aichat-vector:6333")
RESEARCH_URL  = os.environ.get("RESEARCH_URL",  "http://aichat-data:8091/research")
PLANNER_URL   = os.environ.get("PLANNER_URL",   "http://aichat-data:8091/planner")
JOB_URL       = os.environ.get("JOB_URL",       "http://aichat-data:8091/jobs")
TOOLKIT_URL   = os.environ.get("TOOLKIT_URL",   "http://aichat-sandbox:8095")
DOCS_URL      = os.environ.get("DOCS_URL",      "http://aichat-docs:8101")
OCR_URL       = os.environ.get("OCR_URL",       "http://aichat-vision:8099/ocr")
PDF_URL       = os.environ.get("PDF_URL",       "http://aichat-docs:8101/pdf")
VIDEO_URL     = os.environ.get("VIDEO_URL",     "http://aichat-vision:8099")
DETECT_URL    = os.environ.get("DETECT_URL",    "http://aichat-vision:8099/detect")
JUPYTER_URL   = os.environ.get("JUPYTER_URL",   "http://aichat-jupyter:8098")
BROWSER_WORKSPACE = os.environ.get("BROWSER_WORKSPACE", "/browser-workspace")
BROWSER_URL       = os.environ.get("BROWSER_URL",       "http://aichat-browser:9222")
BROWSER_AUTO_URL  = os.environ.get("BROWSER_AUTO_URL",  "http://aichat-browser:8104")
IMAGE_GEN_BASE_URL = os.environ.get("IMAGE_GEN_BASE_URL", "http://192.168.50.2:1234")
IMAGE_GEN_MODEL    = os.environ.get("IMAGE_GEN_MODEL",    "")
COMFYUI_URL        = os.environ.get("COMFYUI_URL",        "")


# ── File Resolution ──────────────────────────────────────────────

def resolve_image_path(path: str) -> str | None:
    """Resolve a user-provided image path to a local filesystem path.

    Accepts bare filenames, /workspace/ prefixes, /docker/human_browser/
    prefixes, or absolute paths. Returns a readable local path or None.
    """
    if not path:
        return None
    name = os.path.basename(path)
    image_exts = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff"}

    def _workspace_images() -> list[tuple[float, str]]:
        if not os.path.isdir(BROWSER_WORKSPACE):
            return []
        picks: list[tuple[float, str]] = []
        for fn in os.listdir(BROWSER_WORKSPACE):
            full = os.path.join(BROWSER_WORKSPACE, fn)
            if not os.path.isfile(full):
                continue
            ext = os.path.splitext(fn)[1].lower()
            if ext not in image_exts:
                continue
            try:
                picks.append((os.path.getmtime(full), full))
            except OSError:
                continue
        picks.sort(key=lambda p: p[0], reverse=True)
        return picks

    def _resolve_client_alias() -> str | None:
        if not re.fullmatch(
            r"image-\d{8,}\.(?:png|jpe?g|webp|gif|bmp|tiff?)",
            name, flags=re.IGNORECASE,
        ):
            return None
        picks = _workspace_images()
        return picks[0][1] if picks else None

    def _resolve_workspace_best_effort(prefer_latest: bool = False) -> str | None:
        picks = _workspace_images()
        if not picks:
            return None
        by_name = {os.path.basename(p).lower(): p for _, p in picks}
        lname = name.lower()
        exact = by_name.get(lname)
        if exact:
            return exact
        stem = os.path.splitext(lname)[0]
        if stem:
            stem_matches = [
                p for _, p in picks
                if os.path.splitext(os.path.basename(p).lower())[0].startswith(stem)
            ]
            if stem_matches:
                return stem_matches[0]
        if prefer_latest:
            return picks[0][1]
        return None

    if "/" not in path or path.startswith("/workspace/") or path.startswith("/docker/human_browser/workspace/"):
        candidate = os.path.join(BROWSER_WORKSPACE, name)
        if os.path.isfile(candidate):
            return candidate
        if path.startswith("/workspace/") or path.startswith("/docker/human_browser/workspace/"):
            return _resolve_workspace_best_effort(prefer_latest=True) or _resolve_client_alias()
        return _resolve_workspace_best_effort(prefer_latest=False) or _resolve_client_alias()
    if os.path.isfile(path):
        return path
    return _resolve_client_alias()
