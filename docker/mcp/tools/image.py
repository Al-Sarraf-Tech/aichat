"""
Image tool handlers — portable image operations.

Resolved actions: image_stitch
(dispatched from the 'image' mega-tool via _resolve_mega_tool)

Note: image_crop/zoom/scan/enhance/diff/annotate stay in app.py (depend on GpuImageProcessor).
Note: image_generate/edit/remix/upscale stay in app.py (depend on ModelRegistry, ComfyUI).
Note: image_search stays in app.py (depends on _vision_confirm, VisionCache).
Note: face_recognize stays in app.py (depends on face detection functions).
"""
from __future__ import annotations

from typing import Any

from tools import TOOL_HANDLERS  # type: ignore[import]
from tools._helpers import text, resolve_image_path  # type: ignore[import]
from tools._imaging import (  # type: ignore[import]
    HAS_PIL, PilImage, ImageOps, pil_to_blocks,
)


async def _image_stitch(args: dict[str, Any]) -> list[dict[str, Any]]:
    if not HAS_PIL:
        return text("image_stitch: Pillow is not installed.")
    paths = [str(p) for p in args.get("paths", [])]
    if len(paths) < 2:
        return text("image_stitch: at least 2 paths are required")
    paths = paths[:8]
    direction = str(args.get("direction", "vertical")).lower()
    try:
        gap = max(0, int(args.get("gap", 0)))
    except (ValueError, TypeError):
        gap = 0

    images = []
    for p in paths:
        loc = resolve_image_path(p)
        if not loc:
            return text(f"image_stitch: image not found — '{p}'")
        with PilImage.open(loc) as im:
            images.append(ImageOps.exif_transpose(im.convert("RGB").copy()))

    if direction == "horizontal":
        total_w = sum(im.width for im in images) + gap * (len(images) - 1)
        max_h   = max(im.height for im in images)
        canvas  = PilImage.new("RGB", (total_w, max_h), (255, 255, 255))
        x = 0
        for im in images:
            y_off = (max_h - im.height) // 2
            canvas.paste(im, (x, y_off))
            x += im.width + gap
    else:
        max_w   = max(im.width for im in images)
        total_h = sum(im.height for im in images) + gap * (len(images) - 1)
        canvas  = PilImage.new("RGB", (max_w, total_h), (255, 255, 255))
        y = 0
        for im in images:
            x_off = (max_w - im.width) // 2
            canvas.paste(im, (x_off, y))
            y += im.height + gap

    summary = (
        f"Stitched {len(images)} images ({direction})\n"
        f"Output size: {canvas.width}×{canvas.height}"
    )
    return pil_to_blocks(canvas, summary, save_prefix="stitched")


# Register handlers
TOOL_HANDLERS["image_stitch"] = _image_stitch
