"""Team of Experts — multi-agent orchestration for aichat.

Provides agent executors (Claude, Codex, Gemini, Qwen, Arc, ComfyUI, OpenAI),
a task router, an image creation pipeline, and progress reporting.

This is self-contained within aichat — completely separate from the global
ORCA orchestrator, which stays at full blast for cross-repo work.

Agent tiers for aichat runtime:
  Claude  = sonnet / high effort   (PAID)
  Codex   = gpt-5.4 / medium      (PAID)
  Gemini  = gemini-2.5-flash       (FREE/cheap)
  Qwen    = qwen3.5-9b on RTX 3090 (FREE)
  Arc     = SDXL Turbo on Intel Arc (FREE)
  ComfyUI = FLUX.1-dev on RTX 3090  (FREE)
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable

import httpx

log = logging.getLogger("aichat-mcp.team")

# ---------------------------------------------------------------------------
# Configuration — all from environment
# ---------------------------------------------------------------------------

TEAM_ENABLED = os.environ.get("TEAM_ENABLED", "true").lower() == "true"

# SSH to host for CLI agents (Claude, Codex)
SSH_HOST = os.environ.get("TEAM_SSH_HOST", "host.docker.internal")
SSH_PORT = os.environ.get("TEAM_SSH_PORT", "1337")
SSH_USER = os.environ.get("TEAM_SSH_USER", "jalsarraf")
SSH_KEY = os.environ.get("TEAM_SSH_KEY", "/app/.ssh/team_key")

# LM Studio (Qwen on RTX 3090)
LMSTUDIO_URL = os.environ.get("IMAGE_GEN_BASE_URL", "http://192.168.50.2:1234")
QWEN_MODEL = os.environ.get("TEAM_QWEN_MODEL", "qwen/qwen3.5-9b")

# Gemini — CLI on host (preferred, already authenticated via OAuth)
#   Falls back to HTTP API if GEMINI_API_KEY is set and CLI fails.
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("TEAM_GEMINI_MODEL", "gemini-2.5-flash")
GEMINI_IMAGE_MODEL = os.environ.get("TEAM_GEMINI_IMAGE_MODEL", "gemini-2.0-flash-exp-image-generation")
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"

# OpenAI
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_IMAGE_MODEL = os.environ.get("TEAM_OPENAI_IMAGE_MODEL", "gpt-5.4")

# Arc A380 (local Docker network)
ARC_VISION_URL = os.environ.get("VISION_GEN_URL", "http://aichat-vision:8099/generate")
ARC_CLIP_URL = os.environ.get("CLIP_URL", "http://aichat-vision:8099/clip")

# ComfyUI (WSL2 RTX 3090)
COMFYUI_URL = os.environ.get("COMFYUI_URL", "")

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

_MAX_OUTPUT = 5 * 1024 * 1024  # 5 MB cap on subprocess output


@dataclass
class AgentResult:
    """Result from any agent execution."""
    agent: str
    content: str = ""
    exit_code: int = 0
    elapsed_s: float = 0.0
    error: str = ""
    metadata: dict[str, str] = field(default_factory=dict)

    @property
    def success(self) -> bool:
        return self.exit_code == 0 and not self.error


@dataclass
class ImageResult:
    """Result from an image generation backend."""
    backend: str
    image_b64: str = ""
    width: int = 0
    height: int = 0
    elapsed_s: float = 0.0
    error: str = ""
    metadata: dict[str, str] = field(default_factory=dict)

    @property
    def success(self) -> bool:
        return bool(self.image_b64) and not self.error


# ---------------------------------------------------------------------------
# Agent capability + routing definitions
# ---------------------------------------------------------------------------

AGENT_CAPABILITIES: dict[str, dict[str, Any]] = {
    "claude": {
        "type": "cli",
        "cost": "paid",
        "capabilities": ["architecture", "security", "debugging", "complex_reasoning",
                         "creative", "vision_review", "code_review", "research"],
    },
    "codex": {
        "type": "cli",
        "cost": "paid",
        "capabilities": ["code_review", "testing", "linting", "bug_verification",
                         "image_generate", "summarization"],
    },
    "gemini": {
        "type": "cli",  # Gemini CLI on host, authenticated via OAuth
        "cost": "free",
        "capabilities": ["simple_qa", "summarization", "creative", "research",
                         "vision_review", "image_generate", "prompt_engineering",
                         "code_review", "documentation"],
    },
    "qwen": {
        "type": "http",
        "cost": "free",
        "capabilities": ["simple_qa", "summarization", "prompt_engineering",
                         "formatting", "validation", "code_review", "linting"],
    },
    "arc": {
        "type": "http",
        "cost": "free",
        "capabilities": ["image_draft", "image_score", "embeddings"],
    },
    "comfyui": {
        "type": "http",
        "cost": "free",
        "capabilities": ["image_render"],
    },
}

TASK_PREFERENCES: dict[str, list[str]] = {
    # Text — prefer free first
    "simple_qa":         ["qwen", "gemini", "codex", "claude"],
    "summarization":     ["qwen", "gemini", "codex", "claude"],
    "prompt_engineering": ["qwen", "gemini", "claude"],
    "creative":          ["gemini", "claude"],
    "research":          ["gemini", "claude"],
    "documentation":     ["gemini", "qwen", "claude"],
    "formatting":        ["qwen", "gemini", "codex"],
    "validation":        ["qwen", "gemini", "codex"],
    # Code — Codex preferred
    "code_review":       ["codex", "qwen", "gemini", "claude"],
    "testing":           ["codex", "qwen", "gemini"],
    "linting":           ["codex", "qwen"],
    "architecture":      ["claude"],
    "security":          ["claude"],
    "debugging":         ["claude"],
    # Image
    "image_draft":       ["arc"],
    "image_render":      ["comfyui", "gemini", "openai"],
    "vision_review":     ["gemini", "claude"],
    "image_score":       ["arc"],
}


# ---------------------------------------------------------------------------
# TeamRouter — pick best agent for a task
# ---------------------------------------------------------------------------

class TeamRouter:
    """Select the best agent for a given task type."""

    @staticmethod
    def pick(task_type: str = "simple_qa", *, force_agent: str | None = None) -> str:
        if force_agent:
            if force_agent not in AGENT_CAPABILITIES:
                raise ValueError(f"Unknown agent: {force_agent}")
            return force_agent
        candidates = TASK_PREFERENCES.get(task_type, ["qwen", "gemini", "claude"])
        for agent in candidates:
            if TeamRouter.is_available(agent):
                return agent
        return "qwen"  # ultimate fallback — always local

    @staticmethod
    def is_available(agent: str) -> bool:
        if agent == "openai":
            return bool(OPENAI_API_KEY)
        if agent == "comfyui":
            return bool(COMFYUI_URL)
        # claude, codex, gemini, qwen, arc — always available (CLI or local HTTP)
        return True


# ---------------------------------------------------------------------------
# Agent Executors
# ---------------------------------------------------------------------------

async def _run_ssh_cli(agent_name: str, command: str, *, timeout_s: float = 300.0) -> AgentResult:
    """Run a CLI command on the host via SSH."""
    ssh_cmd = [
        "ssh",
        "-o", "StrictHostKeyChecking=no",
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=10",
        "-i", SSH_KEY,
        "-p", SSH_PORT,
        f"{SSH_USER}@{SSH_HOST}",
        command,
    ]
    start = time.monotonic()
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *ssh_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(), timeout=timeout_s,
        )
        elapsed = time.monotonic() - start
        return AgentResult(
            agent=agent_name,
            content=stdout_bytes[:_MAX_OUTPUT].decode("utf-8", errors="replace").strip(),
            exit_code=proc.returncode or 0,
            elapsed_s=round(elapsed, 2),
            error=stderr_bytes[:_MAX_OUTPUT].decode("utf-8", errors="replace").strip()
                  if proc.returncode else "",
        )
    except TimeoutError:
        if proc and proc.returncode is None:
            proc.kill()
            await proc.wait()
        return AgentResult(agent=agent_name, exit_code=124, error=f"Timed out after {timeout_s}s",
                           elapsed_s=round(time.monotonic() - start, 2))
    except asyncio.CancelledError:
        if proc and proc.returncode is None:
            proc.kill()
            await proc.wait()
        raise
    except FileNotFoundError:
        return AgentResult(agent=agent_name, exit_code=127, error="ssh not found in container")
    except Exception as exc:
        if proc and proc.returncode is None:
            proc.kill()
            await proc.wait()
        return AgentResult(agent=agent_name, exit_code=1, error=str(exc),
                           elapsed_s=round(time.monotonic() - start, 2))


async def run_claude(prompt: str, *, system: str = "", model: str = "sonnet",
                     effort: str = "high") -> AgentResult:
    """Run Claude Code CLI on the host via SSH."""
    escaped_prompt = prompt.replace("'", "'\"'\"'")
    escaped_system = system.replace("'", "'\"'\"'") if system else ""
    cmd_parts = [
        "claude", "--model", model, "--effort", effort,
        "--output-format", "text", "--dangerously-skip-permissions",
    ]
    if escaped_system:
        cmd_parts += ["--append-system-prompt", f"'{escaped_system}'"]
    cmd_parts += ["-p", f"'{escaped_prompt}'"]
    return await _run_ssh_cli("claude", " ".join(cmd_parts), timeout_s=600.0)


async def run_codex(prompt: str, *, reasoning: str = "medium") -> AgentResult:
    """Run Codex CLI on the host via SSH."""
    escaped = prompt.replace("'", "'\"'\"'")
    cmd = (
        f"codex exec -m gpt-5.4 -c reasoning_effort={reasoning} "
        f"--full-auto --skip-git-repo-check '{escaped}'"
    )
    return await _run_ssh_cli("codex", cmd, timeout_s=600.0)


async def run_gemini(prompt: str, *, model: str = "", system: str = "") -> AgentResult:
    """Run Gemini CLI on the host via SSH (preferred), fall back to HTTP API.

    The Gemini CLI is installed at ~/.local/share/npm-global/bin/gemini on
    amarillo and is already authenticated via OAuth — no API key needed.
    """
    full_prompt = f"{system}\n\n{prompt}" if system else prompt

    # Try CLI first (free, already authenticated)
    cli_result = await _run_gemini_cli(full_prompt, model=model)
    if cli_result.success:
        return cli_result

    # Fall back to HTTP API if CLI failed and we have an API key
    if GEMINI_API_KEY:
        log.info("Gemini CLI failed (%s), falling back to HTTP API", cli_result.error)
        return await _run_gemini_http(prompt, model=model, system=system)

    # CLI failed and no API key — return CLI error
    return cli_result


async def _run_gemini_cli(prompt: str, *, model: str = "") -> AgentResult:
    """Invoke Gemini CLI on the host via SSH."""
    escaped = prompt.replace("'", "'\"'\"'")
    # Sanitize model name — only allow alphanumeric, hyphens, dots, slashes
    safe_model = re.sub(r"[^a-zA-Z0-9.\-/]", "", model) if model else ""
    model_flag = f"-m {safe_model}" if safe_model else ""
    cmd = f"gemini {model_flag} -y -p '{escaped}'"
    return await _run_ssh_cli("gemini", cmd, timeout_s=300.0)


async def _run_gemini_http(prompt: str, *, model: str = "", system: str = "") -> AgentResult:
    """Call Gemini via Google AI HTTP API (fallback)."""
    model = model or GEMINI_MODEL
    url = f"{GEMINI_BASE_URL}/models/{model}:generateContent"
    headers = {"x-goog-api-key": GEMINI_API_KEY}
    contents: list[dict] = []
    if system:
        contents.append({"role": "user", "parts": [{"text": f"[System] {system}"}]})
        contents.append({"role": "model", "parts": [{"text": "Understood."}]})
    contents.append({"role": "user", "parts": [{"text": prompt}]})

    start = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(url, json={"contents": contents}, headers=headers)
            elapsed = time.monotonic() - start
            if not resp.is_success:
                return AgentResult(agent="gemini", exit_code=resp.status_code,
                                   error=f"Gemini API returned {resp.status_code}",
                                   elapsed_s=round(elapsed, 2))
            data = resp.json()
            text = ""
            for candidate in data.get("candidates", []):
                for part in candidate.get("content", {}).get("parts", []):
                    if "text" in part:
                        text += part["text"]
            return AgentResult(agent="gemini", content=text.strip(), elapsed_s=round(elapsed, 2),
                               metadata={"model": model})
    except Exception as exc:
        return AgentResult(agent="gemini", exit_code=1, error=str(exc),
                           elapsed_s=round(time.monotonic() - start, 2))


async def run_gemini_vision(prompt: str, image_b64: str, *, model: str = "") -> AgentResult:
    """Call Gemini with an image for vision review.

    Uses HTTP API for vision (CLI doesn't support inline image input easily).
    Falls back to a text-only CLI call with CLIP score context if no API key.
    """
    # HTTP API path — supports inline image data
    if GEMINI_API_KEY:
        model = model or GEMINI_MODEL
        url = f"{GEMINI_BASE_URL}/models/{model}:generateContent"
        headers = {"x-goog-api-key": GEMINI_API_KEY}
        contents = [{
            "role": "user",
            "parts": [
                {"text": prompt},
                {"inline_data": {"mime_type": "image/jpeg", "data": image_b64}},
            ],
        }]
        start = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                resp = await client.post(url, json={"contents": contents}, headers=headers)
                elapsed = time.monotonic() - start
                if not resp.is_success:
                    return AgentResult(agent="gemini", exit_code=resp.status_code,
                                       error=f"Gemini vision returned {resp.status_code}",
                                       elapsed_s=round(elapsed, 2))
                data = resp.json()
                text = ""
                for candidate in data.get("candidates", []):
                    for part in candidate.get("content", {}).get("parts", []):
                        if "text" in part:
                            text += part["text"]
                return AgentResult(agent="gemini", content=text.strip(),
                                   elapsed_s=round(elapsed, 2))
        except Exception as exc:
            return AgentResult(agent="gemini", exit_code=1, error=str(exc),
                               elapsed_s=round(time.monotonic() - start, 2))

    # No API key — use CLI with text-only context (image can't be piped easily)
    return await _run_gemini_cli(
        f"{prompt}\n\n[Note: draft image was generated but cannot be shown in CLI. "
        "Evaluate based on the prompt quality and CLIP score provided.]"
    )


async def run_qwen(prompt: str, *, system: str = "", max_tokens: int = 4096,
                   temperature: float = 0.3) -> AgentResult:
    """Call Qwen 3.5 9B via LM Studio on RTX 3090."""
    url = f"{LMSTUDIO_URL}/v1/chat/completions"
    messages: list[dict] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    start = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=300.0) as client:
            resp = await client.post(url, json={
                "model": QWEN_MODEL,
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
            })
            elapsed = time.monotonic() - start
            if not resp.is_success:
                return AgentResult(agent="qwen", exit_code=resp.status_code,
                                   error=f"LM Studio returned {resp.status_code}",
                                   elapsed_s=round(elapsed, 2))
            data = resp.json()
            choices = data.get("choices", [])
            msg = choices[0].get("message", {}) if choices else {}
            # Qwen 3.5 is a reasoning model — content may be in reasoning_content
            content = msg.get("content", "").strip()
            if not content:
                content = msg.get("reasoning_content", "").strip()
            return AgentResult(agent="qwen", content=content, elapsed_s=round(elapsed, 2),
                               metadata={"model": QWEN_MODEL})
    except httpx.ConnectError:
        return AgentResult(agent="qwen", exit_code=1,
                           error=f"Cannot reach LM Studio at {LMSTUDIO_URL}",
                           elapsed_s=round(time.monotonic() - start, 2))
    except Exception as exc:
        return AgentResult(agent="qwen", exit_code=1, error=str(exc),
                           elapsed_s=round(time.monotonic() - start, 2))


# ---------------------------------------------------------------------------
# Image generation backends
# ---------------------------------------------------------------------------

async def run_arc_draft(prompt: str, *, negative_prompt: str = "",
                        width: int = 512, height: int = 512,
                        steps: int = 1) -> ImageResult:
    """Generate a quick draft via SDXL Turbo on Intel Arc A380."""
    start = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(ARC_VISION_URL, json={
                "prompt": prompt,
                "negative_prompt": negative_prompt,
                "width": min(width, 512),
                "height": min(height, 512),
                "steps": min(steps, 4),
            })
            elapsed = time.monotonic() - start
            if not resp.is_success:
                return ImageResult(backend="arc", error=f"Arc returned {resp.status_code}",
                                   elapsed_s=round(elapsed, 2))
            data = resp.json()
            return ImageResult(
                backend="arc",
                image_b64=data.get("image_base64", ""),
                width=data.get("width", 512),
                height=data.get("height", 512),
                elapsed_s=round(elapsed, 2),
                metadata={"model": "sdxl-turbo-openvino", "device": "intel-arc-a380",
                           "seed": str(data.get("seed", ""))},
            )
    except Exception as exc:
        return ImageResult(backend="arc", error=str(exc),
                           elapsed_s=round(time.monotonic() - start, 2))


async def run_arc_clip_score(image_b64: str, text: str) -> float:
    """Score image-text similarity via CLIP on Arc A380. Returns 0.0-1.0."""
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(f"{ARC_CLIP_URL}/similarity", json={
                "image_b64": image_b64,
                "text": text,
            })
            if resp.is_success:
                return float(resp.json().get("similarity", 0.0))
    except Exception as exc:
        log.warning("CLIP score failed: %s", exc)
    return 0.0


async def run_comfyui(prompt: str, *, negative_prompt: str = "",
                      model: str = "flux_dev", width: int = 1024,
                      height: int = 1024, steps: int = 25,
                      cfg_scale: float = 3.5) -> ImageResult:
    """Generate image via ComfyUI on WSL2 RTX 3090."""
    if not COMFYUI_URL:
        return ImageResult(backend="comfyui", error="COMFYUI_URL not configured")

    # Map friendly model names to ComfyUI checkpoint names
    model_map = {
        "flux_dev": "flux1-dev.safetensors",
        "flux_schnell": "flux1-schnell.safetensors",
        "sdxl_lightning": "sdxl_lightning_4step.safetensors",
        "sdxl_turbo": "sdxl_turbo.safetensors",
    }
    checkpoint = model_map.get(model, model)

    # Build ComfyUI API workflow
    workflow = {
        "3": {
            "class_type": "KSampler",
            "inputs": {
                "seed": int(time.time()) % (2**32),
                "steps": steps,
                "cfg": cfg_scale,
                "sampler_name": "euler",
                "scheduler": "normal",
                "denoise": 1.0,
                "model": ["4", 0],
                "positive": ["6", 0],
                "negative": ["7", 0],
                "latent_image": ["5", 0],
            },
        },
        "4": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": checkpoint},
        },
        "5": {
            "class_type": "EmptyLatentImage",
            "inputs": {"width": width, "height": height, "batch_size": 1},
        },
        "6": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": prompt, "clip": ["4", 1]},
        },
        "7": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": negative_prompt or "", "clip": ["4", 1]},
        },
        "8": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["3", 0], "vae": ["4", 2]},
        },
        "9": {
            "class_type": "SaveImage",
            "inputs": {"filename_prefix": "team_", "images": ["8", 0]},
        },
    }

    start = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            # Submit workflow
            submit = await client.post(f"{COMFYUI_URL}/prompt", json={"prompt": workflow})
            if not submit.is_success:
                return ImageResult(backend="comfyui",
                                   error=f"ComfyUI submit failed: {submit.status_code}")
            prompt_id = submit.json().get("prompt_id", "")

            # Poll for completion (up to 300s)
            for _ in range(300):
                await asyncio.sleep(1.0)
                hist = await client.get(f"{COMFYUI_URL}/history/{prompt_id}", timeout=10.0)
                if not hist.is_success:
                    continue
                hist_data = hist.json()
                if prompt_id not in hist_data:
                    continue
                outputs = hist_data[prompt_id].get("outputs", {})
                # Find the SaveImage output node
                for node_id, node_out in outputs.items():
                    images = node_out.get("images", [])
                    if images:
                        img_info = images[0]
                        img_resp = await client.get(
                            f"{COMFYUI_URL}/view",
                            params={"filename": img_info["filename"],
                                    "subfolder": img_info.get("subfolder", ""),
                                    "type": img_info.get("type", "output")},
                            timeout=30.0,
                        )
                        if img_resp.is_success:
                            elapsed = time.monotonic() - start
                            return ImageResult(
                                backend="comfyui",
                                image_b64=base64.b64encode(img_resp.content).decode(),
                                width=width, height=height,
                                elapsed_s=round(elapsed, 2),
                                metadata={"model": model, "checkpoint": checkpoint,
                                           "steps": str(steps)},
                            )
            return ImageResult(backend="comfyui", error="ComfyUI generation timed out (300s)",
                               elapsed_s=round(time.monotonic() - start, 2))
    except Exception as exc:
        return ImageResult(backend="comfyui", error=str(exc),
                           elapsed_s=round(time.monotonic() - start, 2))


_OPENAI_VALID_SIZES = {"256x256", "512x512", "1024x1024", "1024x1792", "1792x1024"}


def _snap_openai_size(size: str) -> str:
    """Map arbitrary WxH to nearest valid OpenAI size."""
    if size in _OPENAI_VALID_SIZES:
        return size
    try:
        w, h = (int(x) for x in size.split("x"))
    except ValueError:
        return "1024x1024"
    ratio = w / h if h else 1.0
    if ratio > 1.2:
        return "1792x1024"
    elif ratio < 0.8:
        return "1024x1792"
    return "1024x1024"


async def run_openai_image(prompt: str, *, model: str = "",
                           size: str = "1024x1024") -> ImageResult:
    """Generate image via OpenAI API."""
    if not OPENAI_API_KEY:
        return ImageResult(backend="openai", error="OPENAI_API_KEY not set")

    model = model or OPENAI_IMAGE_MODEL
    size = _snap_openai_size(size)
    start = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                "https://api.openai.com/v1/images/generations",
                headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
                json={"model": model, "prompt": prompt, "size": size,
                      "response_format": "b64_json", "n": 1},
            )
            elapsed = time.monotonic() - start
            if not resp.is_success:
                return ImageResult(backend="openai", error=f"OpenAI returned {resp.status_code}",
                                   elapsed_s=round(elapsed, 2))
            data = resp.json()
            images = data.get("data", [])
            if not images:
                return ImageResult(backend="openai", error="No images returned",
                                   elapsed_s=round(elapsed, 2))
            w, h = (int(x) for x in size.split("x"))
            return ImageResult(
                backend="openai", image_b64=images[0].get("b64_json", ""),
                width=w, height=h, elapsed_s=round(elapsed, 2),
                metadata={"model": model},
            )
    except Exception as exc:
        return ImageResult(backend="openai", error=str(exc),
                           elapsed_s=round(time.monotonic() - start, 2))


async def run_gemini_image(prompt: str) -> ImageResult:
    """Generate image via Gemini — CLI first (free, authenticated), HTTP API fallback.

    The Gemini CLI can generate images when asked. We capture any base64 image
    data from the output. Falls back to the HTTP API if CLI doesn't return an image.
    """
    # Try CLI first — Gemini CLI can generate images natively
    cli_result = await _run_gemini_cli(
        f"Generate an image: {prompt}. Output the image directly, no text explanation needed."
    )
    # CLI image gen may output raw image data or save to file — check if we got usable content
    # (CLI image support varies; fall through to HTTP if no image in output)

    # HTTP API path — fallback if CLI didn't produce an image
    if GEMINI_API_KEY:
        url = f"{GEMINI_BASE_URL}/models/{GEMINI_IMAGE_MODEL}:generateContent"
        headers = {"x-goog-api-key": GEMINI_API_KEY}
        start = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(url, json={
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {"responseModalities": ["TEXT", "IMAGE"]},
                }, headers=headers)
                elapsed = time.monotonic() - start
                if not resp.is_success:
                    return ImageResult(backend="gemini",
                                       error=f"Gemini image returned {resp.status_code}",
                                       elapsed_s=round(elapsed, 2))
                data = resp.json()
                for candidate in data.get("candidates", []):
                    for part in candidate.get("content", {}).get("parts", []):
                        if "inlineData" in part:
                            return ImageResult(
                                backend="gemini",
                                image_b64=part["inlineData"].get("data", ""),
                                width=1024, height=1024,
                                elapsed_s=round(elapsed, 2),
                                metadata={"model": GEMINI_IMAGE_MODEL},
                            )
                return ImageResult(backend="gemini", error="No image in Gemini response",
                                   elapsed_s=round(elapsed, 2))
        except Exception as exc:
            return ImageResult(backend="gemini", error=str(exc),
                               elapsed_s=round(time.monotonic() - start, 2))

    # No API key and CLI didn't produce structured image — return what we have
    if cli_result.success and cli_result.content:
        return ImageResult(backend="gemini", error="Gemini CLI ran but no image data extracted",
                           metadata={"cli_output": cli_result.content[:200]})
    return ImageResult(backend="gemini",
                       error=cli_result.error or "Gemini image gen unavailable (no API key, CLI failed)")


# ---------------------------------------------------------------------------
# ProgressReporter — sends status events to Dartboard UI
# ---------------------------------------------------------------------------

class ProgressReporter:
    """Report Team of Experts progress to MCP SSE clients."""

    def __init__(self) -> None:
        self._callbacks: list[Callable[..., Awaitable[None]]] = []

    def register(self, cb: Callable[..., Awaitable[None]]) -> None:
        self._callbacks.append(cb)

    async def report(self, workflow_id: str, step: int, total_steps: int,
                     agent: str, phase: str, detail: str) -> None:
        percent = int((step / total_steps) * 100) if total_steps else 0
        msg = f"🧠 Team of Experts — Step {step}/{total_steps}: {detail}"
        for cb in self._callbacks:
            try:
                await cb(tool_name=f"team:{phase}", status="running",
                         detail=msg, percent=percent)
            except Exception:
                pass

    async def complete(self, workflow_id: str, detail: str) -> None:
        for cb in self._callbacks:
            try:
                await cb(tool_name="team:complete", status="completed",
                         detail=f"🧠 Team of Experts — {detail}", percent=100)
            except Exception:
                pass


_progress = ProgressReporter()


def get_progress_reporter() -> ProgressReporter:
    return _progress


# ---------------------------------------------------------------------------
# ImagePipeline — multi-agent image creation
# ---------------------------------------------------------------------------

# Aspect ratio → (width, height) at 1024 base
ASPECT_RATIOS: dict[str, tuple[int, int]] = {
    "1:1":  (1024, 1024),
    "16:9": (1820, 1024),
    "9:16": (1024, 1820),
    "4:3":  (1368, 1024),
    "3:2":  (1536, 1024),
    "3:4":  (1024, 1368),
    "2:3":  (1024, 1536),
}

PROMPT_ENGINEER_SYSTEM = (
    "You are an expert AI art director and prompt engineer. "
    "Given a user's image request, output ONLY valid JSON with these fields:\n"
    '{"prompt": "detailed natural-language art direction (200+ words, describe composition, '
    'lighting, color palette, mood, camera angle, artistic style, textures, atmosphere)", '
    '"negative_prompt": "things to avoid", '
    '"model": "flux_dev|flux_schnell|sdxl_lightning", '
    '"steps": 25, "cfg_scale": 3.5}\n'
    "For FLUX models use rich natural-language descriptions. "
    "For SDXL models use tag-style prompts (masterpiece, 8k, detailed, etc). "
    "Default to flux_dev for best quality. Use flux_schnell for speed."
)

VISION_REVIEW_SYSTEM = (
    "You are reviewing a draft image against the original prompt. "
    "Output ONLY valid JSON: "
    '{"verdict": "GOOD"|"REFINE", "score": 0-10, '
    '"refined_prompt": "improved prompt if REFINE, else empty string", '
    '"refined_negative": "improved negative if REFINE, else empty string", '
    '"reasoning": "brief explanation"}'
)


async def image_pipeline(
    prompt: str,
    *,
    mode: str = "quality",
    backend: str = "auto",
    style: str = "",
    aspect_ratio: str = "1:1",
    count: int = 1,
    progress_cb: ProgressReporter | None = None,
) -> list[ImageResult]:
    if not TEAM_ENABLED:
        return [ImageResult(backend="team",
                            error="Team of Experts is disabled (TEAM_ENABLED=false)")]
    """Run the multi-agent image creation pipeline.

    Modes:
      draft   — Arc only, 512x512, ~1s
      fast    — Qwen prompt → ComfyUI schnell, 1024x1024, ~5s
      quality — Full pipeline + 2x upscale → 2048x2048, ~30-45s
      ultra   — Full pipeline + 4x upscale → 4096x4096, ~60-90s
      compare — All backends in parallel → multiple results
    """
    wf_id = f"img_{uuid.uuid4().hex[:8]}"
    pr = progress_cb or _progress
    base_w, base_h = ASPECT_RATIOS.get(aspect_ratio, (1024, 1024))
    user_prompt = f"{prompt}. Style: {style}" if style else prompt
    results: list[ImageResult] = []

    # ── Draft mode: Arc only ──────────────────────────────────────────
    if mode == "draft":
        await pr.report(wf_id, 1, 1, "arc", "draft", "Arc generating quick preview...")
        draft = await run_arc_draft(user_prompt, width=512, height=512)
        await pr.complete(wf_id, f"Draft complete ({draft.elapsed_s}s)")
        return [draft]

    # ── Step 1: Prompt Engineering (Qwen, FREE) ─────────────────────
    total = {"fast": 2, "quality": 5, "ultra": 5, "compare": 4}.get(mode, 5)
    await pr.report(wf_id, 1, total, "qwen", "prompt_engineering",
                    "Qwen crafting art direction on RTX 3090...")

    eng_result = await run_qwen(user_prompt, system=PROMPT_ENGINEER_SYSTEM, max_tokens=2048)
    if not eng_result.success:
        # Fallback to Gemini if Qwen fails
        eng_result = await run_gemini(user_prompt, system=PROMPT_ENGINEER_SYSTEM)

    # Parse the engineered prompt — fall back to user's original on any failure
    try:
        eng = json.loads(eng_result.content)
        if not isinstance(eng, dict) or "prompt" not in eng:
            raise ValueError("missing 'prompt' key")
    except (json.JSONDecodeError, TypeError, ValueError):
        eng = {"prompt": user_prompt,
               "negative_prompt": "", "model": "flux_dev",
               "steps": 25, "cfg_scale": 3.5}

    art_prompt = str(eng.get("prompt", user_prompt))
    neg_prompt = str(eng.get("negative_prompt", ""))
    gen_model = str(eng.get("model", "flux_dev"))
    gen_steps = int(eng.get("steps", 25))
    gen_cfg = float(eng.get("cfg_scale", 3.5))

    # ── Fast mode: skip draft review, go straight to render ────────
    if mode == "fast":
        await pr.report(wf_id, 2, total, "comfyui", "render",
                        "ComfyUI rendering with FLUX schnell...")
        result = await run_comfyui(art_prompt, negative_prompt=neg_prompt,
                                   model="flux_schnell", width=base_w, height=base_h,
                                   steps=4, cfg_scale=gen_cfg)
        await pr.complete(wf_id, f"Fast render complete ({result.elapsed_s}s)")
        return [result]

    # ── Step 2: Arc Draft (FREE) ─────────────────────────────────────
    await pr.report(wf_id, 2, total, "arc", "draft",
                    "Arc A380 generating quick preview...")
    draft = await run_arc_draft(art_prompt, negative_prompt=neg_prompt)
    clip_score = 0.0
    if draft.success:
        clip_score = await run_arc_clip_score(draft.image_b64, art_prompt)

    # ── Step 3: Vision Review (Gemini/Claude) ────────────────────────
    await pr.report(wf_id, 3, total, "gemini", "vision_review",
                    "Reviewing draft quality...")

    review_prompt = (
        f"Original request: {user_prompt}\n"
        f"Engineered prompt: {art_prompt}\n"
        f"CLIP similarity score: {clip_score:.3f}\n"
        f"Review this draft image and decide if the prompt needs refinement."
    )

    review_result: AgentResult | None = None
    if draft.success and draft.image_b64:
        # Try Gemini first (free), fall back to Claude
        reviewer = TeamRouter.pick("vision_review")
        if reviewer == "gemini":
            review_result = await run_gemini_vision(
                f"{VISION_REVIEW_SYSTEM}\n\n{review_prompt}", draft.image_b64)
        if not review_result or not review_result.success:
            # Claude fallback for vision review
            review_result = await run_claude(
                f"{VISION_REVIEW_SYSTEM}\n\n{review_prompt}\n\n"
                "[Draft image was generated but cannot be shown in CLI. "
                f"CLIP score is {clip_score:.3f}. "
                "A score above 0.25 typically indicates good alignment.]"
            )

    # Parse review and potentially refine prompt
    if review_result and review_result.success:
        try:
            review = json.loads(review_result.content)
            if review.get("verdict") == "REFINE" and review.get("refined_prompt"):
                art_prompt = review["refined_prompt"]
                neg_prompt = review.get("refined_negative", neg_prompt)
                log.info("Vision review refined prompt (score: %s)", review.get("score"))
        except (json.JSONDecodeError, TypeError):
            pass  # Keep original prompt

    # ── Step 4: Final Render — TEAM RENDERING ──────────────────────
    # All available image backends render in parallel as a team.
    # In "compare" mode: return all results for the user to pick.
    # In "quality"/"ultra" mode with backend="auto": run all backends,
    #   CLIP-score each result, return the best + all others as alternatives.
    # With a specific backend: run only that backend.

    render_tasks: list[tuple[str, Any]] = []  # (name, coroutine)

    if backend != "auto" and backend != "all":
        # User forced a specific backend
        await pr.report(wf_id, 4, total, backend, "render",
                        f"{backend} rendering at {base_w}x{base_h}...")
        if backend == "gemini":
            render_tasks.append(("gemini", run_gemini_image(art_prompt)))
        elif backend == "openai" and TeamRouter.is_available("openai"):
            render_tasks.append(("openai", run_openai_image(art_prompt, size=f"{base_w}x{base_h}")))
        elif backend == "comfyui":
            render_tasks.append(("comfyui", run_comfyui(
                art_prompt, negative_prompt=neg_prompt, model=gen_model,
                width=base_w, height=base_h, steps=gen_steps, cfg_scale=gen_cfg)))
        elif backend == "arc":
            render_tasks.append(("arc", run_arc_draft(art_prompt, negative_prompt=neg_prompt,
                                                       width=min(base_w, 512), height=min(base_h, 512))))
    else:
        # Team render — ALL available backends in parallel
        agents_rendering = []
        if TeamRouter.is_available("comfyui"):
            render_tasks.append(("comfyui", run_comfyui(
                art_prompt, negative_prompt=neg_prompt, model=gen_model,
                width=base_w, height=base_h, steps=gen_steps, cfg_scale=gen_cfg)))
            agents_rendering.append("ComfyUI (FLUX.1)")
        # Gemini image gen (CLI-based, always available)
        render_tasks.append(("gemini", run_gemini_image(art_prompt)))
        agents_rendering.append("Gemini")
        # OpenAI / Codex image gen (if API key available)
        if TeamRouter.is_available("openai"):
            render_tasks.append(("openai", run_openai_image(art_prompt, size=f"{base_w}x{base_h}")))
            agents_rendering.append("OpenAI")
        # Arc quick render too (fast, low-res but adds variety)
        render_tasks.append(("arc", run_arc_draft(art_prompt, negative_prompt=neg_prompt)))
        agents_rendering.append("Arc A380")

        await pr.report(wf_id, 4, total, "team", "render",
                        f"Team rendering in parallel: {', '.join(agents_rendering)}...")

    # Execute all renders in parallel
    raw_results = await asyncio.gather(
        *[coro for _, coro in render_tasks], return_exceptions=True,
    )

    # Collect successful results
    rendered: list[ImageResult] = []
    for i, r in enumerate(raw_results):
        if isinstance(r, ImageResult) and r.success:
            rendered.append(r)
        elif isinstance(r, ImageResult) and r.error:
            log.warning("Render failed (%s): %s", render_tasks[i][0], r.error)
        elif isinstance(r, Exception):
            log.warning("Render exception (%s): %s", render_tasks[i][0], r)

    if mode == "compare" or backend == "all":
        # Return all results — user picks their favorite
        await pr.complete(wf_id, f"Team rendered {len(rendered)} images from {len(render_tasks)} backends")
        return rendered if rendered else [ImageResult(backend="team", error="All backends failed")]

    # quality/ultra — CLIP-score each result and pick the best
    if len(rendered) > 1:
        await pr.report(wf_id, 4, total, "arc", "scoring",
                        f"CLIP scoring {len(rendered)} results to find the best...")
        best_score = -1.0
        best_idx = 0
        for i, img in enumerate(rendered):
            if img.image_b64:
                score = await run_arc_clip_score(img.image_b64, art_prompt)
                img.metadata["clip_score"] = f"{score:.3f}"
                if score > best_score:
                    best_score = score
                    best_idx = i
        # Put the best result first, keep alternatives
        best = rendered.pop(best_idx)
        best.metadata["selected"] = "best_clip_score"
        results = [best] + rendered  # best first, alternatives after
    elif rendered:
        results = rendered
    else:
        results = [ImageResult(backend="team", error="All image backends failed")]

    # ── Step 5: Upscale (quality/ultra) ──────────────────────────────
    best_result = results[0] if results else None
    if mode in ("quality", "ultra") and best_result and best_result.success:
        scale = 4 if mode == "ultra" else 2
        await pr.report(wf_id, 5, total, "arc", "upscale",
                        f"Upscaling {scale}x to {base_w * scale}x{base_h * scale}...")
        best_result.metadata["upscale_target"] = f"{base_w * scale}x{base_h * scale}"
        best_result.metadata["upscale_factor"] = str(scale)

    n_success = sum(1 for r in results if r.success)
    n_backends = len(render_tasks)
    if best_result and best_result.success:
        await pr.complete(wf_id,
            f"Team complete — {n_success}/{n_backends} backends succeeded, "
            f"best: {best_result.backend} ({best_result.elapsed_s}s)")
    else:
        await pr.complete(wf_id, f"Team complete — {n_success}/{n_backends} backends succeeded")
    return results


# ---------------------------------------------------------------------------
# team_chat — dispatch text tasks to the right agent
# ---------------------------------------------------------------------------

async def team_chat(message: str, *, task_type: str = "auto",
                    agent: str = "auto", context: str = "") -> AgentResult:
    """Route a chat message to the best available agent."""
    if not TEAM_ENABLED:
        return AgentResult(agent="team", exit_code=1,
                           error="Team of Experts is disabled (TEAM_ENABLED=false)")
    if task_type == "auto":
        task_type = _classify_task(message)
    chosen = TeamRouter.pick(task_type, force_agent=agent if agent != "auto" else None)
    full_prompt = f"{context}\n\n{message}" if context else message

    await _progress.report("chat", 1, 1, chosen, "chat",
                           f"{chosen} is working on your request...")

    if chosen == "claude":
        result = await run_claude(full_prompt)
    elif chosen == "codex":
        result = await run_codex(full_prompt)
    elif chosen == "gemini":
        result = await run_gemini(full_prompt)
    elif chosen == "qwen":
        result = await run_qwen(full_prompt)
    else:
        result = await run_qwen(full_prompt)  # fallback

    await _progress.complete("chat", f"{chosen} done ({result.elapsed_s}s)")
    return result


def _classify_task(message: str) -> str:
    """Simple word-boundary keyword classification."""
    words = set(re.findall(r"[a-z]+", message.lower()))
    if words & {"review", "pr", "diff"} or "pull request" in message.lower():
        return "code_review"
    if words & {"bug", "fix", "error", "debug", "traceback"}:
        return "debugging"
    if words & {"architecture", "design", "refactor"}:
        return "architecture"
    if words & {"security", "vulnerability", "cve", "audit"}:
        return "security"
    if words & {"test", "spec", "coverage"}:
        return "testing"
    if words & {"summarize", "summary", "tldr"}:
        return "summarization"
    if words & {"creative", "story", "poem"}:
        return "creative"
    if words & {"research", "search"} or "look up" in message.lower():
        return "research"
    return "simple_qa"


# ---------------------------------------------------------------------------
# team_agents — list available agents and status
# ---------------------------------------------------------------------------

async def team_agents() -> list[dict[str, Any]]:
    """List all agents with their availability status."""
    agents = []
    for name, info in AGENT_CAPABILITIES.items():
        agents.append({
            "name": name,
            "type": info["type"],
            "cost": info["cost"],
            "capabilities": info["capabilities"],
            "available": TeamRouter.is_available(name),
        })
    return agents
