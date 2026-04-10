"""
Code execution tool handlers — Python, JavaScript, Jupyter.

Resolved actions: code_run, run_javascript, jupyter_exec
(dispatched from the 'code' mega-tool via _resolve_mega_tool)

Note: All subprocess calls use create_subprocess_exec (not shell=True)
      to prevent command injection. User code runs in isolated tempfiles.
"""
from __future__ import annotations

import asyncio
import base64
import os
import shutil
import sys
import tempfile
import textwrap
import time
from typing import Any

from tools import TOOL_HANDLERS  # type: ignore[import]
from tools._helpers import text, get_client, JUPYTER_URL  # type: ignore[import]


# ── GPU Code Runtime ─────────────────────────────────────────────

class _GpuCodeRuntime:
    """Prepend GPU device detection preamble when code references torch/cuda."""

    _GPU_TRIGGERS = frozenset({"torch", "tensorflow", "tf.", "cuda", ".to(", "device"})
    _PREAMBLE = textwrap.dedent("""\
        # ── GPU device auto-detection (injected by aichat) ──────
        _device = "cpu"
        try:
            import torch as _torch
            if _torch.cuda.is_available():
                _device = "cuda"
            elif getattr(getattr(_torch, "backends", None), "mps", None) and _torch.backends.mps.is_available():
                _device = "mps"
        except ImportError:
            pass
        DEVICE = _device   # use this in your code: model.to(DEVICE)
        # ────────────────────────────────────────────────────────
    """)

    @classmethod
    def prepare(cls, code: str) -> str:
        if any(kw in code for kw in cls._GPU_TRIGGERS):
            return cls._PREAMBLE + "\n" + code
        return code


# ── Python Execution ─────────────────────────────────────────────

async def _code_run(args: dict[str, Any]) -> list[dict[str, Any]]:
    code    = str(args.get("code", "")).strip()
    pkgs    = args.get("packages") or []
    timeout = max(1, min(120, int(args.get("timeout", 30))))
    if not code:
        return text("code_run: 'code' is required")

    code = _GpuCodeRuntime.prepare(code)

    # Install requested packages
    install_log: list[str] = []
    if pkgs:
        for pkg in pkgs:
            pkg = str(pkg).strip()
            if not pkg:
                continue
            try:
                proc = await asyncio.create_subprocess_exec(
                    "python3", "-m", "pip", "install", "--quiet", pkg,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                )
                await asyncio.wait_for(proc.communicate(), timeout=15.0)
                install_log.append(f"pip install {pkg}: exit {proc.returncode}")
            except asyncio.TimeoutError:
                install_log.append(f"pip install {pkg}: timed out")
            except Exception as exc:
                install_log.append(f"pip install {pkg}: {exc}")

    # Write code to tempfile and execute
    with tempfile.NamedTemporaryFile(
        suffix=".py", mode="w", encoding="utf-8", delete=False
    ) as tf:
        tf.write(code)
        tmp_path = tf.name

    t0 = time.monotonic()
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, tmp_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(), timeout=float(timeout)
            )
            exit_code = proc.returncode
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            msg = f"code_run: timed out after {timeout}s"
            if install_log:
                msg += "\n\nInstall log:\n" + "\n".join(install_log)
            return text(msg)

        duration_ms = int((time.monotonic() - t0) * 1000)
        stdout_s = stdout_b.decode(errors="replace")
        stderr_s = stderr_b.decode(errors="replace")

        parts = [f"code_run: exit={exit_code} duration={duration_ms}ms"]
        if stdout_s.strip():
            parts.append(f"stdout:\n{stdout_s.strip()}")
        if stderr_s.strip():
            parts.append(f"stderr:\n{stderr_s.strip()}")
        if install_log:
            parts.append("install_log:\n" + "\n".join(install_log))
        return text("\n\n".join(parts))
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


# ── JavaScript Execution ─────────────────────────────────────────

async def _run_javascript(args: dict[str, Any]) -> list[dict[str, Any]]:
    code    = str(args.get("code", "")).strip()
    timeout = max(1, min(120, int(args.get("timeout", 30))))
    if not code:
        return text("run_javascript: 'code' is required")

    node_bin = shutil.which("node") or shutil.which("nodejs")
    if not node_bin:
        return text(
            "run_javascript: Node.js is not installed. "
            "Use code_run (Python) as an alternative."
        )

    workspace = "/workspace" if os.path.isdir("/workspace") else None
    with tempfile.NamedTemporaryFile(
        suffix=".cjs", mode="w", encoding="utf-8", delete=False, dir=workspace,
    ) as tf:
        tf.write(code)
        tmp_path = tf.name

    t0 = time.monotonic()
    try:
        proc = await asyncio.create_subprocess_exec(
            node_bin, tmp_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=workspace,
        )
        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(), timeout=float(timeout)
            )
            exit_code = proc.returncode
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return text(f"run_javascript: timed out after {timeout}s")

        duration_ms = int((time.monotonic() - t0) * 1000)
        stdout_s = stdout_b.decode(errors="replace")
        stderr_s = stderr_b.decode(errors="replace")

        parts = [f"run_javascript: exit={exit_code} duration={duration_ms}ms"]
        if stdout_s.strip():
            parts.append(f"stdout:\n{stdout_s.strip()}")
        if stderr_s.strip():
            parts.append(f"stderr:\n{stderr_s.strip()}")
        return text("\n\n".join(parts))
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


# ── Jupyter Execution ────────────────────────────────────────────

async def _jupyter_exec(args: dict[str, Any]) -> list[dict[str, Any]]:
    code       = str(args.get("code", "")).strip()
    session_id = str(args.get("session_id", "default")).strip() or "default"
    timeout    = max(1, min(300, int(args.get("timeout", 60))))
    reset      = bool(args.get("reset", False))
    if not code:
        return text("jupyter_exec: 'code' is required")

    try:
        async with get_client() as c:
            r = await c.post(
                f"{JUPYTER_URL}/exec",
                json={
                    "code": code, "session_id": session_id,
                    "timeout": timeout, "reset": reset,
                },
                timeout=float(timeout) + 10,
            )
            if r.status_code != 200:
                return text(
                    f"jupyter_exec: error {r.status_code} — {r.text[:300]}"
                )
            data = r.json()

        blocks: list[dict[str, Any]] = []
        parts: list[str] = []
        if data.get("error"):
            parts.append(f"Error:\n{data['error']}")
        if data.get("stdout", "").strip():
            parts.append(f"stdout:\n{data['stdout'].strip()}")
        if data.get("stderr", "").strip():
            parts.append(f"stderr:\n{data['stderr'].strip()}")
        for out in data.get("outputs", []):
            if str(out).strip():
                parts.append(f"Out:\n{out}")

        if parts:
            blocks.append({"type": "text", "text": "\n\n".join(parts)})
        elif not data.get("images"):
            blocks.append({"type": "text", "text": "(no output)"})

        for img_b64 in data.get("images", [])[:4]:
            try:
                raw = base64.b64decode(img_b64)
                encoded = base64.b64encode(raw).decode()
                blocks.append({
                    "type": "image",
                    "data": encoded,
                    "mimeType": "image/png",
                })
            except Exception:
                pass

        return blocks

    except Exception as exc:
        return text(f"jupyter_exec: {exc}")


# Register handlers
TOOL_HANDLERS["code_run"]       = _code_run
TOOL_HANDLERS["run_javascript"] = _run_javascript
TOOL_HANDLERS["jupyter_exec"]   = _jupyter_exec
