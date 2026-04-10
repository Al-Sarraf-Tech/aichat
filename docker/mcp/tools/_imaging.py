"""
Shared image rendering infrastructure for tool modules.

Provides the ImageRenderer singleton and PIL-related utilities that
image, browser, video, and data tools need for producing inline
MCP image content blocks.

Extracted from app.py to break the singleton dependency barrier.
"""
from __future__ import annotations

import base64
import os
from datetime import datetime
from typing import Any

# ── Conditional PIL imports ──────────────────────────────────────
try:
    from PIL import (
        Image as PilImage,
        ImageEnhance,
        ImageFilter,
        ImageStat,
        ImageChops,
        ImageDraw,
        ImageOps,
    )
    import io as _io
    HAS_PIL = True
except ImportError:
    PilImage = None  # type: ignore[assignment]
    ImageEnhance = None  # type: ignore[assignment]
    ImageFilter = None  # type: ignore[assignment]
    ImageStat = None  # type: ignore[assignment]
    ImageChops = None  # type: ignore[assignment]
    ImageDraw = None  # type: ignore[assignment]
    ImageOps = None  # type: ignore[assignment]
    HAS_PIL = False

try:
    import io as _io  # noqa: F811
except ImportError:
    pass

# ── Conditional OpenCV imports ───────────────────────────────────
try:
    import cv2
    import numpy as np
    HAS_CV2 = True
except ImportError:
    cv2 = None  # type: ignore[assignment]
    np = None   # type: ignore[assignment]
    HAS_CV2 = False

from tools._helpers import BROWSER_WORKSPACE  # type: ignore[import]

# ── Constants ────────────────────────────────────────────────────
MAX_INLINE_BYTES: int = 3_000_000  # 3 MB raw ~ 4 MB base64, safe for LM Studio


# ── ImageRenderer ────────────────────────────────────────────────

class ImageRenderer:
    """Encodes PIL images, workspace paths, and raw bytes as inline MCP image blocks."""

    MAX_BYTES: int = MAX_INLINE_BYTES

    def _compress_to_limit(self, img: "PilImage.Image", min_quality: int = 85) -> bytes:
        _RUNGS = (75, 65, 50)
        ladder = [min_quality] + [q for q in _RUNGS if q < min_quality]
        for q in ladder:
            buf = _io.BytesIO()
            img.convert("RGB").save(buf, format="JPEG", quality=q)
            raw = buf.getvalue()
            if len(raw) <= self.MAX_BYTES:
                return raw
        buf = _io.BytesIO()
        img.convert("RGB").save(buf, format="JPEG", quality=40)
        return buf.getvalue()

    def _fit(self, img: "PilImage.Image", max_w: int = 1280, max_h: int = 1024) -> "PilImage.Image":
        if img.width > max_w or img.height > max_h:
            img = img.copy()
            img.thumbnail((max_w, max_h), PilImage.LANCZOS)
        return img

    def encode(
        self,
        img: "PilImage.Image",
        summary: str,
        save_prefix: str | None = None,
        quality: int = 85,
        max_w: int = 1280,
        max_h: int = 1024,
    ) -> list[dict[str, Any]]:
        """Encode a PIL Image to [text_block, image_block], fitting within MAX_BYTES."""
        img = self._fit(img.convert("RGB"), max_w=max_w, max_h=max_h)
        raw = self._compress_to_limit(img, min_quality=max(40, min(quality, 95)))
        if save_prefix and os.path.isdir(BROWSER_WORKSPACE):
            try:
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                fname = f"{save_prefix}_{ts}.jpg"
                with open(os.path.join(BROWSER_WORKSPACE, fname), "wb") as fh:
                    fh.write(raw)
                summary += f"\n→ Saved as: {fname}  (pass this as 'path' in the next pipeline step)"
            except OSError:
                pass
        b64 = base64.standard_b64encode(raw).decode("ascii")
        return [
            {"type": "text",  "text": summary},
            {"type": "image", "data": b64, "mimeType": "image/jpeg"},
        ]

    def encode_path(self, container_path: str, summary: str) -> list[dict[str, Any]]:
        """Load image from workspace path and return inline MCP blocks."""
        blocks: list[dict[str, Any]] = [{"type": "text", "text": summary}]
        if not container_path:
            return blocks
        fname = os.path.basename(container_path)
        local_path = os.path.join(BROWSER_WORKSPACE, fname)
        if not os.path.isfile(local_path):
            return blocks
        try:
            if HAS_PIL:
                with PilImage.open(local_path) as img:
                    encoded = self.encode(img, "")
                    blocks.extend(b for b in encoded if b.get("type") == "image")
            else:
                with open(local_path, "rb") as fh:
                    raw = fh.read()
                if len(raw) > self.MAX_BYTES:
                    raw = raw[:self.MAX_BYTES]
                b64 = base64.standard_b64encode(raw).decode("ascii")
                blocks.append({"type": "image", "data": b64, "mimeType": "image/png"})
        except Exception:
            pass
        return blocks

    def encode_url_bytes(self, raw: bytes, content_type: str, summary: str) -> list[dict[str, Any]]:
        """Compress raw HTTP image bytes to inline MCP blocks."""
        if HAS_PIL:
            try:
                with PilImage.open(_io.BytesIO(raw)) as img:
                    return self.encode(ImageOps.exif_transpose(img).convert("RGB"), summary)
            except Exception:
                pass
        if len(raw) <= self.MAX_BYTES:
            b64 = base64.standard_b64encode(raw).decode("ascii")
            return [
                {"type": "text",  "text": summary},
                {"type": "image", "data": b64, "mimeType": content_type},
            ]
        return [{"type": "text",
                 "text": summary + "\n⚠ Image too large to render inline (PIL unavailable)."}]


# Module-level singleton
renderer = ImageRenderer()


def image_blocks(container_path: str, summary: str) -> list[dict[str, Any]]:
    """Shorthand for renderer.encode_path()."""
    return renderer.encode_path(container_path, summary)


def pil_to_blocks(
    img: "PilImage.Image",
    summary: str,
    quality: int = 85,
    save_prefix: str | None = None,
) -> list[dict[str, Any]]:
    """Encode a PIL Image as inline MCP blocks (delegates to renderer)."""
    return renderer.encode(img, summary, save_prefix=save_prefix, quality=quality)
