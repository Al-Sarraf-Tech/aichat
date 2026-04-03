"""Agent runners and image pipeline for aichat.

Provides CLI executors (Claude, Codex, Gemini, Qwen) for chat,
plus image generation backends (Arc, ComfyUI, Gemini, OpenAI)
and a multi-backend image creation pipeline.

Chat agents:
  Claude  = sonnet / high effort   (PAID, OAuth via CLI)
  Codex   = gpt-5.4 / medium      (PAID, OAuth via CLI)
  Gemini  = gemini-2.5-flash       (FREE, OAuth via CLI)
  Qwen    = qwen3.5-9b on RTX 3090 (FREE, local HTTP)

Image backends:
  Arc     = SDXL Turbo on Intel Arc (FREE)
  ComfyUI = FLUX.1-dev on RTX 3090  (FREE)
  Gemini  = via OAuth CLI            (FREE)
  OpenAI  = via API key              (PAID)
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from collections.abc import AsyncIterator
from typing import Any

import httpx

log = logging.getLogger("aichat-mcp.agents")

# ---------------------------------------------------------------------------
# Configuration — all from environment
# ---------------------------------------------------------------------------

# SSH to host for CLI agents (Claude, Codex, Gemini)
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
GEMINI_IMAGE_MODEL = os.environ.get("TEAM_GEMINI_IMAGE_MODEL", "gemini-2.0-flash-exp")
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
_MAX_INPUT = 100 * 1024  # 100 KB cap on input message
_MAX_CONTEXT = 50 * 1024  # 50 KB cap on context

# Constraining system prompt for CLI agents — prevents prompt injection
# from causing the agent to access credentials, delete files, or modify system config.
_AGENT_SYSTEM_PROMPT = (
    "You are a helpful coding assistant invoked through aichat. "
    "Answer the user's question accurately and concisely. "
    "NEVER read, output, or modify SSH keys, .env files, credentials, tokens, or secrets. "
    "NEVER execute rm -rf, chmod, chown on system directories, or any command that "
    "modifies system configuration, firewall rules, or network settings. "
    "NEVER access /etc, /root, ~/.ssh, ~/.config, or similar sensitive paths. "
    "If asked to do something destructive or access credentials, refuse and explain why."
)

# Valid values for hardcoded parameters (prevent latent injection)
_VALID_MODELS_CLAUDE = {"haiku", "sonnet", "opus"}
_VALID_EFFORTS = {"low", "medium", "high", "max"}
_VALID_REASONING = {"low", "medium", "high", "xhigh"}
_VALID_AGENTS = {"claude", "codex", "gemini", "qwen"}


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
# Circuit Breaker — fast-fail when SSH host is consistently unreachable
# ---------------------------------------------------------------------------

@dataclass
class _CircuitBreaker:
    failure_threshold: int = 3
    recovery_s: float = 30.0
    _failures: int = field(default=0, init=False, repr=False)
    _open_at: float = field(default=0.0, init=False, repr=False)

    @property
    def is_open(self) -> bool:
        if self._failures >= self.failure_threshold:
            return (time.monotonic() - self._open_at) < self.recovery_s
        return False

    def record_success(self) -> None:
        self._failures = 0

    def record_failure(self) -> None:
        self._failures += 1
        if self._failures >= self.failure_threshold:
            self._open_at = time.monotonic()
            log.warning("ssh_circuit_open failures=%d recovery_s=%.0f",
                        self._failures, self.recovery_s)

_ssh_cb = _CircuitBreaker()

# SSH exit codes that indicate transient connection failure (not CLI errors)
_SSH_TRANSIENT_CODES = {255}
_SSH_TRANSIENT_ERRORS = frozenset({
    "Connection refused", "Network unreachable", "No route to host",
    "Connection reset by peer", "Connection timed out",
})


# ---------------------------------------------------------------------------
# Agent Executors
# ---------------------------------------------------------------------------

async def _run_ssh_cli(agent_name: str, command: str, *, timeout_s: float = 300.0) -> AgentResult:
    """Run a CLI command on the host via SSH."""
    if _ssh_cb.is_open:
        return AgentResult(agent=agent_name, exit_code=1,
                           error="SSH circuit open — host unreachable, retry in a few seconds")
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
    log.info("ssh_start agent=%s timeout=%.0fs", agent_name, timeout_s)
    start = time.monotonic()
    proc = None
    result: AgentResult
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
        result = AgentResult(
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
        result = AgentResult(agent=agent_name, exit_code=124, error=f"Timed out after {timeout_s}s",
                             elapsed_s=round(time.monotonic() - start, 2))
    except asyncio.CancelledError:
        if proc and proc.returncode is None:
            proc.kill()
            await proc.wait()
        raise
    except FileNotFoundError:
        result = AgentResult(agent=agent_name, exit_code=127, error="ssh not found in container")
    except Exception as exc:
        if proc and proc.returncode is None:
            proc.kill()
            await proc.wait()
        result = AgentResult(agent=agent_name, exit_code=1, error=str(exc),
                             elapsed_s=round(time.monotonic() - start, 2))
    # Circuit breaker: track SSH-level failures vs CLI-level failures
    _is_ssh_failure = (
        result.exit_code in _SSH_TRANSIENT_CODES
        or any(frag in result.error for frag in _SSH_TRANSIENT_ERRORS)
    )
    if _is_ssh_failure:
        _ssh_cb.record_failure()
    elif result.success:
        _ssh_cb.record_success()
    log.info("ssh_done agent=%s exit=%d elapsed=%.2fs cb_failures=%d",
             agent_name, result.exit_code, result.elapsed_s, _ssh_cb._failures)
    return result


async def run_claude(prompt: str, *, system: str = "", model: str = "sonnet",
                     effort: str = "high") -> AgentResult:
    """Run Claude Code CLI on the host via SSH."""
    model = model if model in _VALID_MODELS_CLAUDE else "sonnet"
    effort = effort if effort in _VALID_EFFORTS else "high"
    full_system = f"{_AGENT_SYSTEM_PROMPT}\n\n{system}" if system else _AGENT_SYSTEM_PROMPT
    escaped_prompt = prompt[:_MAX_INPUT].replace("'", "'\"'\"'")
    escaped_system = full_system.replace("'", "'\"'\"'")
    cmd_parts = [
        "claude", "--model", model, "--effort", effort,
        "--output-format", "text", "--dangerously-skip-permissions",
        "--append-system-prompt", f"'{escaped_system}'",
    ]
    cmd_parts += ["-p", f"'{escaped_prompt}'"]
    return await _run_ssh_cli("claude", " ".join(cmd_parts), timeout_s=600.0)


async def run_codex(prompt: str, *, reasoning: str = "medium") -> AgentResult:
    """Run Codex CLI on the host via SSH."""
    reasoning = reasoning if reasoning in _VALID_REASONING else "medium"
    escaped = prompt[:_MAX_INPUT].replace("'", "'\"'\"'")
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

    cli_result = await _run_gemini_cli(full_prompt, model=model)
    if cli_result.success:
        return cli_result

    if GEMINI_API_KEY:
        log.info("Gemini CLI failed (%s), falling back to HTTP API", cli_result.error)
        return await _run_gemini_http(prompt, model=model, system=system)

    return cli_result


async def _run_gemini_cli(prompt: str, *, model: str = "",
                          timeout_s: float = 60.0) -> AgentResult:
    """Invoke Gemini CLI on the host via SSH."""
    escaped = prompt.replace("'", "'\"'\"'")
    safe_model = re.sub(r"[^a-zA-Z0-9.\-/]", "", model) if model else ""
    model_flag = f"-m {safe_model}" if safe_model else ""
    cmd = f"timeout {int(timeout_s)} gemini {model_flag} -y -p '{escaped}'"
    return await _run_ssh_cli("gemini", cmd, timeout_s=timeout_s + 10)


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
# chat — direct dispatcher for chat messages
# ---------------------------------------------------------------------------

async def chat(message: str, *, agent: str, model: str = "",
               effort: str = "", context: str = "") -> AgentResult:
    """Send a chat message to a specific agent. No auto-routing."""
    if agent not in _VALID_AGENTS:
        return AgentResult(agent=agent, exit_code=1,
                           error=f"Unknown agent: {agent}. Valid: {', '.join(sorted(_VALID_AGENTS))}")
    if len(message) > _MAX_INPUT:
        message = message[:_MAX_INPUT]
    if len(context) > _MAX_CONTEXT:
        context = context[:_MAX_CONTEXT]

    full_prompt = f"{context}\n\n{message}" if context else message

    if agent == "claude":
        kwargs: dict = {}
        if model: kwargs["model"] = model
        if effort: kwargs["effort"] = effort
        return await run_claude(full_prompt, **kwargs)
    elif agent == "codex":
        kwargs = {}
        if effort: kwargs["reasoning"] = effort
        return await run_codex(full_prompt, **kwargs)
    elif agent == "gemini":
        kwargs = {}
        if model: kwargs["model"] = model
        return await run_gemini(full_prompt, **kwargs)
    else:  # qwen
        return await run_qwen(full_prompt)


# ---------------------------------------------------------------------------
# Streaming variants — async generators yielding text chunks
# ---------------------------------------------------------------------------

async def _stream_ssh_cli(
    agent_name: str, command: str, *, timeout_s: float = 600.0,
) -> AsyncIterator[str]:
    """Stream stdout line-by-line from an SSH CLI process."""
    if _ssh_cb.is_open:
        raise RuntimeError("SSH circuit open — host unreachable")
    ssh_cmd = [
        "ssh", "-o", "StrictHostKeyChecking=no", "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=10", "-i", SSH_KEY, "-p", SSH_PORT,
        f"{SSH_USER}@{SSH_HOST}", command,
    ]
    log.info("ssh_stream_start agent=%s timeout=%.0fs", agent_name, timeout_s)
    proc = await asyncio.create_subprocess_exec(
        *ssh_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    stderr_task = asyncio.create_task(proc.stderr.read())
    try:
        async with asyncio.timeout(timeout_s):
            async for line in proc.stdout:
                decoded = line.decode("utf-8", errors="replace")
                if decoded:
                    yield decoded
        await proc.wait()
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise RuntimeError(f"{agent_name} timed out after {timeout_s}s")
    except asyncio.CancelledError:
        proc.kill()
        await proc.wait()
        raise
    finally:
        stderr_bytes = await stderr_task
    if proc.returncode and proc.returncode != 0:
        stderr_text = stderr_bytes[:4096].decode("utf-8", errors="replace").strip()
        _is_transient = (
            proc.returncode in _SSH_TRANSIENT_CODES
            or any(frag in stderr_text for frag in _SSH_TRANSIENT_ERRORS)
        )
        if _is_transient:
            _ssh_cb.record_failure()
        raise RuntimeError(f"{agent_name} exited {proc.returncode}: {stderr_text[:300]}")
    else:
        _ssh_cb.record_success()
    log.info("ssh_stream_done agent=%s exit=%d", agent_name, proc.returncode or 0)


async def stream_qwen(
    prompt: str, *, system: str = "", max_tokens: int = 4096, temperature: float = 0.3,
) -> AsyncIterator[str]:
    """Stream tokens from Qwen via LM Studio's OpenAI-compatible SSE."""
    url = f"{LMSTUDIO_URL}/v1/chat/completions"
    messages: list[dict] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    payload = {
        "model": QWEN_MODEL, "messages": messages,
        "max_tokens": max_tokens, "temperature": temperature,
        "stream": True,
    }
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(connect=10.0, read=None, write=10.0, pool=5.0)) as client:
            async with client.stream("POST", url, json=payload) as resp:
                if not resp.is_success:
                    raise RuntimeError(f"LM Studio returned {resp.status_code}")
                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data_str = line[6:]
                    if data_str == "[DONE]":
                        break
                    try:
                        obj = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue
                    delta = obj.get("choices", [{}])[0].get("delta", {})
                    text = delta.get("content") or delta.get("reasoning_content") or ""
                    if text:
                        yield text
    except httpx.ConnectError:
        raise RuntimeError(f"Cannot reach LM Studio at {LMSTUDIO_URL}")


async def chat_stream(
    message: str, *, agent: str, model: str = "", effort: str = "", context: str = "",
) -> AsyncIterator[str]:
    """Streaming variant of chat(). Yields text chunks as the agent produces them."""
    if agent not in _VALID_AGENTS:
        raise ValueError(f"Unknown agent: {agent}. Valid: {', '.join(sorted(_VALID_AGENTS))}")
    if len(message) > _MAX_INPUT:
        message = message[:_MAX_INPUT]
    if len(context) > _MAX_CONTEXT:
        context = context[:_MAX_CONTEXT]
    full_prompt = f"{context}\n\n{message}" if context else message

    if agent == "claude":
        m = model if model in _VALID_MODELS_CLAUDE else "sonnet"
        e = effort if effort in _VALID_EFFORTS else "high"
        full_system = _AGENT_SYSTEM_PROMPT
        escaped_prompt = full_prompt[:_MAX_INPUT].replace("'", "'\"'\"'")
        escaped_system = full_system.replace("'", "'\"'\"'")
        cmd = (
            f"claude --model {m} --effort {e} --output-format text "
            f"--dangerously-skip-permissions --append-system-prompt '{escaped_system}' "
            f"-p '{escaped_prompt}'"
        )
        async for chunk in _stream_ssh_cli("claude", cmd, timeout_s=600.0):
            yield chunk

    elif agent == "codex":
        r = effort if effort in _VALID_REASONING else "medium"
        escaped = full_prompt[:_MAX_INPUT].replace("'", "'\"'\"'")
        cmd = (
            f"codex exec -m gpt-5.4 -c reasoning_effort={r} "
            f"--full-auto --skip-git-repo-check '{escaped}'"
        )
        async for chunk in _stream_ssh_cli("codex", cmd, timeout_s=600.0):
            yield chunk

    elif agent == "gemini":
        escaped = full_prompt.replace("'", "'\"'\"'")
        safe_model = re.sub(r"[^a-zA-Z0-9.\-/]", "", model) if model else ""
        model_flag = f"-m {safe_model}" if safe_model else ""
        cmd = f"timeout 60 gemini {model_flag} -y -p '{escaped}'"
        async for chunk in _stream_ssh_cli("gemini", cmd, timeout_s=70.0):
            yield chunk

    else:  # qwen
        async for chunk in stream_qwen(full_prompt):
            yield chunk


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

    model_map = {
        "flux_dev": "flux1-dev.safetensors",
        "flux_schnell": "flux1-schnell.safetensors",
        "sdxl_lightning": "sdxl_lightning_4step.safetensors",
        "sdxl_turbo": "sdxl_turbo.safetensors",
    }
    checkpoint = model_map.get(model, model)

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
            "inputs": {"filename_prefix": "aichat_", "images": ["8", 0]},
        },
    }

    start = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            submit = await client.post(f"{COMFYUI_URL}/prompt", json={"prompt": workflow})
            if not submit.is_success:
                return ImageResult(backend="comfyui",
                                   error=f"ComfyUI submit failed: {submit.status_code}")
            prompt_id = submit.json().get("prompt_id", "")

            for _ in range(300):
                await asyncio.sleep(1.0)
                hist = await client.get(f"{COMFYUI_URL}/history/{prompt_id}", timeout=10.0)
                if not hist.is_success:
                    continue
                hist_data = hist.json()
                if prompt_id not in hist_data:
                    continue
                outputs = hist_data[prompt_id].get("outputs", {})
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


async def _run_openai_image_via_cli(prompt: str, *, size: str = "1024x1024") -> ImageResult:
    """Generate image via Codex CLI (OAuth).

    Asks Codex to write+run a Python script that uses the OpenAI library
    to generate an image with DALL-E, saving it as a file and outputting base64.
    """
    start = time.monotonic()
    escaped = prompt[:2000].replace("'", "'\"'\"'").replace('"', '\\"')
    cmd = (
        f'timeout 90 codex exec -m gpt-5.4 --full-auto --skip-git-repo-check '
        f"'Generate an image with this description: \"{escaped}\". "
        f"Write a Python script that uses the openai library to call the DALL-E image generation API. "
        f"Save the generated image to /tmp/codex_gen.png. "
        f"After saving, run: base64 -w0 /tmp/codex_gen.png'"
    )
    cli_result = await _run_ssh_cli("codex", cmd, timeout_s=100.0)
    elapsed = time.monotonic() - start

    if cli_result.success and cli_result.content:
        content = cli_result.content
        b64_match = re.search(r'([A-Za-z0-9+/]{500,}={0,2})', content)
        if b64_match:
            return ImageResult(backend="openai", image_b64=b64_match.group(1),
                               width=1024, height=1024, elapsed_s=round(elapsed, 2),
                               metadata={"model": "gpt-5.4-dalle", "source": "codex-cli"})
        log.info("Codex CLI: no base64 image in output (%d chars)", len(content))
        snippet = content[:300].replace('\n', ' ')
        return ImageResult(backend="openai",
                           error=f"Codex ran but no image produced: {snippet}",
                           elapsed_s=round(elapsed, 2))

    return ImageResult(backend="openai",
                       error=cli_result.error or "Codex CLI failed",
                       elapsed_s=round(elapsed, 2))


async def run_openai_image(prompt: str, *, model: str = "",
                           size: str = "1024x1024") -> ImageResult:
    """Generate image via OpenAI API."""
    if not OPENAI_API_KEY or OPENAI_API_KEY == "ROTATE_ME":
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
    """Generate image via Gemini CLI (OAuth, no API key needed)."""
    start = time.monotonic()

    cli_result = await _run_gemini_cli(
        f"Generate an image of: {prompt}. "
        "Save the generated image to /tmp/gemini_gen.png. "
        "If you cannot generate images, create a simple Python script using PIL "
        "to render an artistic interpretation and save to /tmp/gemini_gen.png.",
        timeout_s=60.0,
    )
    elapsed = time.monotonic() - start

    if cli_result.success and cli_result.content:
        content = cli_result.content
        m = re.search(r'data:image/[a-z]+;base64,([A-Za-z0-9+/=]{100,})', content)
        if m:
            return ImageResult(backend="gemini", image_b64=m.group(1),
                               width=1024, height=1024, elapsed_s=round(elapsed, 2),
                               metadata={"model": "gemini-cli-oauth", "source": "cli"})
        try:
            file_result = await _run_ssh_cli("gemini",
                "test -f /tmp/gemini_gen.png && base64 -w0 /tmp/gemini_gen.png && rm /tmp/gemini_gen.png",
                timeout_s=15.0)
            if file_result.success and file_result.content and len(file_result.content.strip()) > 100:
                return ImageResult(backend="gemini", image_b64=file_result.content.strip(),
                                   width=1024, height=1024, elapsed_s=round(elapsed, 2),
                                   metadata={"model": "gemini-cli-oauth", "source": "cli-file"})
        except Exception:
            pass
        log.info("Gemini CLI: no image in output (%d chars)", len(content))

    error_text = cli_result.error or "" if cli_result else ""
    content_text = cli_result.content or "" if cli_result else ""
    combined = error_text + content_text
    if "429" in combined or "RESOURCE_EXHAUSTED" in combined or "capacity" in combined.lower():
        return ImageResult(backend="gemini",
                           error="Gemini rate-limited (429) — try again in a few minutes",
                           elapsed_s=round(elapsed, 2))

    return ImageResult(backend="gemini",
                       error=error_text or "Gemini CLI did not produce an image",
                       elapsed_s=round(elapsed, 2))


# ---------------------------------------------------------------------------
# Image Pipeline — multi-backend image creation
# ---------------------------------------------------------------------------

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
) -> list[ImageResult]:
    """Run the multi-backend image creation pipeline.

    Modes:
      draft   — Arc only, 512x512, ~1s
      fast    — Qwen prompt -> ComfyUI schnell, 1024x1024, ~5s
      quality — Full pipeline + 2x upscale -> 2048x2048, ~30-45s
      ultra   — Full pipeline + 4x upscale -> 4096x4096, ~60-90s
      compare — All backends in parallel -> multiple results
    """
    base_w, base_h = ASPECT_RATIOS.get(aspect_ratio, (1024, 1024))
    user_prompt = f"{prompt}. Style: {style}" if style else prompt
    results: list[ImageResult] = []

    # ── Draft mode: Arc only ──────────────────────────────────────────
    if mode == "draft":
        return [await run_arc_draft(user_prompt, width=512, height=512)]

    # ── Direct mode: skip prompt engineering, go straight to backend ─
    if mode == "direct":
        if backend == "gemini":
            return [await run_gemini_image(user_prompt)]
        elif backend == "openai":
            return [await _run_openai_image_via_cli(user_prompt, size=f"{base_w}x{base_h}")]
        elif backend == "comfyui":
            return [await run_comfyui(user_prompt, width=base_w, height=base_h,
                                       model="flux_schnell", steps=4, cfg_scale=1.0)]
        log.warning("Direct mode: unknown backend %r, falling through", backend)

    # ── Step 1: Prompt Engineering (Qwen, FREE) ─────────────────────
    eng_result = await run_qwen(user_prompt, system=PROMPT_ENGINEER_SYSTEM, max_tokens=2048)
    if not eng_result.success:
        eng_result = await run_gemini(user_prompt, system=PROMPT_ENGINEER_SYSTEM)

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
        result = await run_comfyui(art_prompt, negative_prompt=neg_prompt,
                                   model="flux_schnell", width=base_w, height=base_h,
                                   steps=4, cfg_scale=gen_cfg)
        return [result]

    # ── Step 2: Arc Draft (FREE) ─────────────────────────────────────
    draft = await run_arc_draft(art_prompt, negative_prompt=neg_prompt)
    clip_score = 0.0
    if draft.success:
        clip_score = await run_arc_clip_score(draft.image_b64, art_prompt)

    # ── Step 3: Vision Review (Gemini first, Claude fallback) ────────
    review_prompt = (
        f"Original request: {user_prompt}\n"
        f"Engineered prompt: {art_prompt}\n"
        f"CLIP similarity score: {clip_score:.3f}\n"
        f"Review this draft image and decide if the prompt needs refinement."
    )

    review_result: AgentResult | None = None
    if draft.success and draft.image_b64:
        review_result = await run_gemini_vision(
            f"{VISION_REVIEW_SYSTEM}\n\n{review_prompt}", draft.image_b64)
        if not review_result or not review_result.success:
            review_result = await run_claude(
                f"{VISION_REVIEW_SYSTEM}\n\n{review_prompt}\n\n"
                "[Draft image was generated but cannot be shown in CLI. "
                f"CLIP score is {clip_score:.3f}. "
                "A score above 0.25 typically indicates good alignment.]"
            )

    if review_result and review_result.success:
        try:
            review = json.loads(review_result.content)
            if review.get("verdict") == "REFINE" and review.get("refined_prompt"):
                art_prompt = review["refined_prompt"]
                neg_prompt = review.get("refined_negative", neg_prompt)
                log.info("Vision review refined prompt (score: %s)", review.get("score"))
        except (json.JSONDecodeError, TypeError):
            pass

    # ── Step 4: Final Render ─────────────────────────────────────────
    render_tasks: list[tuple[str, Any]] = []

    if backend != "auto" and backend != "all":
        if backend == "gemini":
            render_tasks.append(("gemini", run_gemini_image(art_prompt)))
        elif backend == "openai" and OPENAI_API_KEY:
            render_tasks.append(("openai", run_openai_image(art_prompt, size=f"{base_w}x{base_h}")))
        elif backend == "comfyui":
            render_tasks.append(("comfyui", run_comfyui(
                art_prompt, negative_prompt=neg_prompt, model=gen_model,
                width=base_w, height=base_h, steps=gen_steps, cfg_scale=gen_cfg)))
        elif backend == "arc":
            render_tasks.append(("arc", run_arc_draft(art_prompt, negative_prompt=neg_prompt,
                                                       width=min(base_w, 512), height=min(base_h, 512))))
    else:
        if COMFYUI_URL:
            render_tasks.append(("comfyui", run_comfyui(
                art_prompt, negative_prompt=neg_prompt, model=gen_model,
                width=base_w, height=base_h, steps=gen_steps, cfg_scale=gen_cfg)))
        render_tasks.append(("gemini", run_gemini_image(art_prompt)))
        if OPENAI_API_KEY:
            render_tasks.append(("openai", run_openai_image(art_prompt, size=f"{base_w}x{base_h}")))
        render_tasks.append(("arc", run_arc_draft(art_prompt, negative_prompt=neg_prompt)))

    raw_results = await asyncio.gather(
        *[coro for _, coro in render_tasks], return_exceptions=True,
    )

    rendered: list[ImageResult] = []
    for i, r in enumerate(raw_results):
        if isinstance(r, ImageResult) and r.success:
            rendered.append(r)
        elif isinstance(r, ImageResult) and r.error:
            log.warning("Render failed (%s): %s", render_tasks[i][0], r.error)
        elif isinstance(r, Exception):
            log.warning("Render exception (%s): %s", render_tasks[i][0], r)

    if mode == "compare" or backend == "all":
        return rendered if rendered else [ImageResult(backend="pipeline", error="All backends failed")]

    # quality/ultra — CLIP-score each result and pick the best
    if len(rendered) > 1:
        best_score = -1.0
        best_idx = 0
        for i, img in enumerate(rendered):
            if img.image_b64:
                score = await run_arc_clip_score(img.image_b64, art_prompt)
                img.metadata["clip_score"] = f"{score:.3f}"
                if score > best_score:
                    best_score = score
                    best_idx = i
        best = rendered.pop(best_idx)
        best.metadata["selected"] = "best_clip_score"
        results = [best] + rendered
    elif rendered:
        results = rendered
    else:
        results = [ImageResult(backend="pipeline", error="All image backends failed")]

    # ── Step 5: Upscale (quality/ultra) ──────────────────────────────
    best_result = results[0] if results else None
    if mode in ("quality", "ultra") and best_result and best_result.success:
        scale = 4 if mode == "ultra" else 2
        best_result.metadata["upscale_target"] = f"{base_w * scale}x{base_h * scale}"
        best_result.metadata["upscale_factor"] = str(scale)

    return results
