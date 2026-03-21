"""GPU idle TTL watcher — auto-unloads models after configurable idle period.

Monitors three GPU systems:
- ComfyUI (image generation, WSL2 RTX 3090)
- LM Studio (LLM chat, WSL2 RTX 3090)
- aichat-vision (CLIP/OCR, amarillo Intel Arc A380)

Runs as an asyncio background task inside the MCP service.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time

import httpx

log = logging.getLogger("aichat-mcp.gpu_ttl")

_DEFAULT_TTL = 600       # 10 minutes
_DEFAULT_POLL = 60       # check every 60s


class GpuIdleTtlWatcher:
    """Background task that unloads GPU models after idle timeout."""

    def __init__(
        self,
        comfyui_url: str,
        lm_studio_url: str,
        vision_url: str,
        ttl: float | None = None,
        poll_interval: float | None = None,
    ) -> None:
        self._comfyui_url = comfyui_url
        self._lm_studio_url = lm_studio_url
        self._vision_url = vision_url
        self._ttl = ttl or float(os.environ.get("GPU_IDLE_TTL", _DEFAULT_TTL))
        self._poll = poll_interval or float(os.environ.get("GPU_TTL_POLL_INTERVAL", _DEFAULT_POLL))

        # Idle trackers (monotonic timestamps)
        self._last_comfyui_use = time.monotonic()
        self._last_lm_studio_use: dict[str, float] = {}  # model_id -> last_use
        self._last_vision_use = time.monotonic()

        # State flags to avoid repeated unload calls
        self._comfyui_unloaded = False
        self._vision_unloaded = False

        self._running = False
        self._task: asyncio.Task | None = None

    # ── Public touch methods (called by tool handlers) ──────────────

    def touch_comfyui(self) -> None:
        """Reset ComfyUI idle timer."""
        self._last_comfyui_use = time.monotonic()
        self._comfyui_unloaded = False

    def touch_lm_studio(self, model_id: str) -> None:
        """Reset LM Studio idle timer for a specific model."""
        self._last_lm_studio_use[model_id] = time.monotonic()

    def touch_vision(self) -> None:
        """Reset vision service idle timer."""
        self._last_vision_use = time.monotonic()
        self._vision_unloaded = False

    # ── Status (for /health endpoint) ───────────────────────────────

    def status(self) -> dict:
        now = time.monotonic()
        return {
            "enabled": self._running,
            "ttl_seconds": int(self._ttl),
            "comfyui_idle_seconds": int(now - self._last_comfyui_use),
            "comfyui_unloaded": self._comfyui_unloaded,
            "lm_studio_models_idle": {
                mid: int(now - ts) for mid, ts in self._last_lm_studio_use.items()
            },
            "vision_idle_seconds": int(now - self._last_vision_use),
            "vision_unloaded": self._vision_unloaded,
        }

    # ── Lifecycle ───────────────────────────────────────────────────

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        log.info("GPU idle TTL watcher started (TTL=%ds, poll=%ds)", self._ttl, self._poll)

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    # ── Main loop ───────────────────────────────────────────────────

    async def _loop(self) -> None:
        # Give services time to start up before first check
        await asyncio.sleep(30)
        async with httpx.AsyncClient(timeout=10.0) as client:
            while self._running:
                try:
                    await self._check_comfyui(client)
                except Exception as exc:
                    log.debug("ComfyUI TTL check error: %s", exc)
                try:
                    await self._check_lm_studio(client)
                except Exception as exc:
                    log.debug("LM Studio TTL check error: %s", exc)
                try:
                    await self._check_vision(client)
                except Exception as exc:
                    log.debug("Vision TTL check error: %s", exc)
                await asyncio.sleep(self._poll)

    # ── ComfyUI ─────────────────────────────────────────────────────

    async def _check_comfyui(self, client: httpx.AsyncClient) -> None:
        if not self._comfyui_url or self._comfyui_unloaded:
            return

        # 1. Check queue — if anything is running/pending, reset timer
        try:
            qr = await client.get(f"{self._comfyui_url}/queue", timeout=5.0)
            if qr.status_code == 200:
                q = qr.json()
                if q.get("queue_running") or q.get("queue_pending"):
                    self._last_comfyui_use = time.monotonic()
                    return
        except Exception:
            return  # fail-open: if unreachable, don't unload

        # 2. Check if models are even loaded
        try:
            sr = await client.get(f"{self._comfyui_url}/system_stats", timeout=5.0)
            if sr.status_code != 200:
                return
            devs = sr.json().get("devices", [])
            if not devs:
                # F7: empty devices = no GPU / already unloaded
                self._comfyui_unloaded = True
                return
            vram_used = devs[0].get("torch_vram_total", 0)
            if vram_used == 0:
                self._comfyui_unloaded = True
                return  # already unloaded
        except Exception:
            return

        # 3. Check idle duration
        idle = time.monotonic() - self._last_comfyui_use
        if idle < self._ttl:
            return

        # F4: re-check idle right before unloading (touch may have reset timer)
        if time.monotonic() - self._last_comfyui_use < self._ttl:
            return

        # 4. Unload
        try:
            r = await client.post(
                f"{self._comfyui_url}/free",
                json={"unload_models": True, "free_memory": True},
                timeout=10.0,
            )
            if r.status_code == 200:
                self._comfyui_unloaded = True
                freed_mb = vram_used / 1e6
                log.info("ComfyUI idle TTL expired (%.0fm), unloaded models (freed %.0f MB VRAM)",
                         idle / 60, freed_mb)
            else:
                log.warning("ComfyUI /free returned %d", r.status_code)
        except Exception as exc:
            log.warning("Failed to unload ComfyUI models: %s", exc)

    # ── LM Studio ───────────────────────────────────────────────────

    async def _check_lm_studio(self, client: httpx.AsyncClient) -> None:
        if not self._lm_studio_url:
            return

        # 1. Get loaded models
        try:
            r = await client.get(f"{self._lm_studio_url}/api/v0/models", timeout=5.0)
            if r.status_code != 200:
                return
        except Exception:
            return

        # F3: validate response is a list before iteration
        data = r.json()
        if not isinstance(data, list):
            return

        loaded = []
        for m in data:
            if isinstance(m, dict) and m.get("state") == "loaded":
                loaded.append(m.get("id", ""))

        now = time.monotonic()

        # 2. Register newly-seen models
        for mid in loaded:
            if mid and mid not in self._last_lm_studio_use:
                self._last_lm_studio_use[mid] = now

        # 3. Check idle and unload
        to_remove = []
        for mid, last_use in list(self._last_lm_studio_use.items()):
            if mid not in loaded:
                to_remove.append(mid)
                continue
            idle = now - last_use
            if idle < self._ttl:
                continue
            # F4: re-check idle right before unloading (touch may have reset timer)
            if time.monotonic() - self._last_lm_studio_use.get(mid, 0) < self._ttl:
                continue
            # Unload this model
            try:
                ur = await client.post(
                    f"{self._lm_studio_url}/api/v0/models/unload",
                    json={"id": mid},
                    timeout=15.0,
                )
                if ur.status_code in (200, 204):
                    log.info("LM Studio model '%s' idle TTL expired (%.0fm), unloaded", mid, idle / 60)
                    to_remove.append(mid)
                else:
                    log.warning("LM Studio /unload for '%s' returned %d", mid, ur.status_code)
            except Exception as exc:
                log.warning("Failed to unload LM Studio model '%s': %s", mid, exc)

        # 4. Cleanup tracker
        for mid in to_remove:
            self._last_lm_studio_use.pop(mid, None)

        # 5. Invalidate ModelRegistry cache if we unloaded anything
        if to_remove:
            try:
                from app import ModelRegistry
                ModelRegistry.get().invalidate()
            except Exception:
                pass

    # ── aichat-vision ───────────────────────────────────────────────

    async def _check_vision(self, client: httpx.AsyncClient) -> None:
        if not self._vision_url or self._vision_unloaded:
            return

        idle = time.monotonic() - self._last_vision_use
        if idle < self._ttl:
            return

        # Call the /unload endpoint on the vision service
        try:
            r = await client.post(f"{self._vision_url}/unload", timeout=10.0)
            if r.status_code == 200:
                data = r.json()
                self._vision_unloaded = True
                log.info("Vision models idle TTL expired (%.0fm), unloaded: %s",
                         idle / 60, data.get("unloaded", []))
            else:
                log.warning("Vision /unload returned %d", r.status_code)
        except Exception as exc:
            log.debug("Vision /unload unavailable: %s", exc)


# ── Module-level singleton ──────────────────────────────────────────

_instance: GpuIdleTtlWatcher | None = None


def get_watcher() -> GpuIdleTtlWatcher | None:
    """Return the singleton (None if TTL is disabled)."""
    return _instance


def init_watcher(
    comfyui_url: str,
    lm_studio_url: str,
    vision_url: str,
) -> GpuIdleTtlWatcher | None:
    """Create and start the singleton watcher. Returns None if disabled."""
    global _instance
    if _instance is not None:
        return _instance

    enabled = os.environ.get("GPU_TTL_ENABLED", "true").lower()
    if enabled in ("false", "0", "no", "off"):
        log.info("GPU idle TTL watcher disabled (GPU_TTL_ENABLED=%s)", enabled)
        return None

    _instance = GpuIdleTtlWatcher(
        comfyui_url=comfyui_url,
        lm_studio_url=lm_studio_url,
        vision_url=vision_url,
    )
    _instance.start()
    return _instance
