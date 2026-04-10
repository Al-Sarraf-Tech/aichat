"""
Media tool handlers — video analysis and transcoding.

Resolved actions: video_info, video_frames, video_transcode
(dispatched from the 'media' mega-tool via _resolve_mega_tool)

Note: video_thumbnail stays in app.py (depends on _renderer).
Note: detect_objects, detect_humans stay in app.py (depend on _get_gpu_ttl).
Note: tts stays in app.py (depends on ModelRegistry).
"""
from __future__ import annotations

from typing import Any

from tools import TOOL_HANDLERS  # type: ignore[import]
from tools._helpers import text, get_client, VIDEO_URL  # type: ignore[import]


async def _video_info(args: dict[str, Any]) -> list[dict[str, Any]]:
    url = str(args.get("url", "")).strip()
    if not url:
        return text("video_info: 'url' is required")
    try:
        async with get_client() as c:
            r = await c.post(f"{VIDEO_URL}/info", json={"url": url}, timeout=90)
            r.raise_for_status()
            d = r.json()
    except Exception as exc:
        return text(f"video_info: failed — {exc}")
    return text(
        f"Video info for {url}:\n"
        f"  Duration: {d.get('duration_s')}s  FPS: {d.get('fps')}\n"
        f"  Resolution: {d.get('width')}×{d.get('height')}  Codec: {d.get('codec')}\n"
        f"  Format: {d.get('format')}  Size: {d.get('size_mb')} MB"
    )


async def _video_frames(args: dict[str, Any]) -> list[dict[str, Any]]:
    url          = str(args.get("url", "")).strip()
    interval_sec = float(args.get("interval_sec", 5.0))
    max_frames   = int(args.get("max_frames", 20))
    if not url:
        return text("video_frames: 'url' is required")
    try:
        async with get_client() as c:
            r = await c.post(
                f"{VIDEO_URL}/frames",
                json={"url": url, "interval_sec": interval_sec, "max_frames": max_frames},
                timeout=180,
            )
            r.raise_for_status()
            d = r.json()
    except Exception as exc:
        return text(f"video_frames: failed — {exc}")
    frames = d.get("frames", [])
    lines = [f"Extracted {len(frames)} frames from {url}:"]
    for fr in frames:
        lines.append(f"  [{fr.get('timestamp_s')}s] {fr.get('path')}")
    return text("\n".join(lines))


async def _video_transcode(args: dict[str, Any]) -> list[dict[str, Any]]:
    url      = str(args.get("url", "")).strip()
    codec    = str(args.get("codec", "h264")).strip()
    bitrate  = str(args.get("bitrate", "5M")).strip()
    width    = int(args.get("width", 0))
    height   = int(args.get("height", 0))
    filename = str(args.get("filename", "")).strip()
    if not url:
        return text("video_transcode: 'url' is required")
    try:
        async with get_client() as c:
            r = await c.post(
                f"{VIDEO_URL}/transcode",
                json={
                    "url": url, "codec": codec, "bitrate": bitrate,
                    "width": width, "height": height, "filename": filename,
                },
                timeout=600,
            )
            r.raise_for_status()
            d = r.json()
    except Exception as exc:
        return text(f"video_transcode: failed — {exc}")
    gpu_tag = "GPU (VA-API)" if d.get("gpu_accelerated") else "CPU"
    return text(
        f"Transcoded {url} → {d.get('path')}\n"
        f"  Codec: {d.get('codec')}  Encoder: {gpu_tag}\n"
        f"  Resolution: {d.get('width')}×{d.get('height')}\n"
        f"  Duration: {d.get('duration_s')}s  Size: {d.get('size_mb')} MB"
    )


# Register handlers (no schema — mega-tool schema is in app.py)
TOOL_HANDLERS["video_info"]      = _video_info
TOOL_HANDLERS["video_frames"]    = _video_frames
TOOL_HANDLERS["video_transcode"] = _video_transcode
