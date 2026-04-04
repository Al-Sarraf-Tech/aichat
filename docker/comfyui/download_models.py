#!/usr/bin/env python3
"""Download all required ComfyUI models from HuggingFace.

Idempotent — skips files that already exist at the correct size.
Requires: HF_TOKEN env var (or public-access models only).

Directory layout matches ComfyUI's expected paths:
  /models/checkpoints/  — full SD/SDXL checkpoints
  /models/unet/         — standalone UNet weights (FLUX, Lightning)
  /models/clip/         — text encoder weights
  /models/vae/          — VAE weights
  /models/upscale_models/ — RealESRGAN etc.
"""
import os, sys, time, hashlib
from pathlib import Path
from huggingface_hub import hf_hub_download

MODELS_ROOT = Path(os.environ.get("COMFYUI_MODELS_DIR", "/models"))
HF_TOKEN = os.environ.get("HF_TOKEN", "")

# ── Model manifest ────────────────────────────────────────────────
# Each entry: (hf_repo, hf_filename, local_subdir, local_filename)
MANIFEST = [
    # --- FLUX Schnell (fast, 4-step) ---
    ("black-forest-labs/FLUX.1-schnell", "flux1-schnell.safetensors",
     "unet", "flux1-schnell.safetensors"),

    # --- FLUX Dev (quality, 25-step) ---
    ("black-forest-labs/FLUX.1-dev", "flux1-dev.safetensors",
     "unet", "flux1-dev.safetensors"),

    # --- FLUX shared: CLIP encoders + VAE ---
    ("comfyanonymous/flux_text_encoders", "clip_l.safetensors",
     "clip", "clip_l.safetensors"),
    ("comfyanonymous/flux_text_encoders", "t5xxl_fp16.safetensors",
     "clip", "t5xxl_fp16.safetensors"),
    ("black-forest-labs/FLUX.1-schnell", "ae.safetensors",
     "vae", "ae.safetensors"),

    # --- SDXL Lightning (4-step fast) ---
    ("stabilityai/stable-diffusion-xl-base-1.0", "sd_xl_base_1.0.safetensors",
     "checkpoints", "sd_xl_base_1.0.safetensors"),
    ("ByteDance/SDXL-Lightning", "sdxl_lightning_4step_unet.safetensors",
     "unet", "sdxl_lightning_4step.safetensors"),

    # --- SDXL Turbo (1-step) ---
    ("stabilityai/sdxl-turbo", "sd_xl_turbo_1.0_fp16.safetensors",
     "checkpoints", "sdxl_turbo.safetensors"),

    # --- SD 1.5 community models ---
    ("Lykon/DreamShaper", "DreamShaper_8_pruned.safetensors",
     "checkpoints", "dreamshaper_8.safetensors"),
    ("SG161222/Realistic_Vision_V5.1_noVAE", "Realistic_Vision_V5.1_fp16-no-ema.safetensors",
     "checkpoints", "realistic_vision_v5.safetensors"),
    ("XpucT/Deliberate", "Deliberate_v3.safetensors",
     "checkpoints", "deliberate_v3.safetensors"),

    # --- Upscaler ---
    ("ai-forever/Real-ESRGAN", "RealESRGAN_x4.pth",
     "upscale_models", "RealESRGAN_x4plus.pth"),
]


def download_model(repo: str, filename: str, subdir: str, local_name: str) -> bool:
    """Download one model file. Returns True if downloaded, False if skipped."""
    dest_dir = MODELS_ROOT / subdir
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / local_name

    if dest.exists() and dest.stat().st_size > 1_000_000:
        print(f"  SKIP {subdir}/{local_name} (already exists, {dest.stat().st_size / 1e9:.1f} GB)")
        return False

    print(f"  DOWNLOADING {repo} / {filename} -> {subdir}/{local_name} ...")
    t0 = time.time()
    try:
        cached = hf_hub_download(
            repo_id=repo,
            filename=filename,
            token=HF_TOKEN or None,
            local_dir=str(dest_dir),
            local_dir_use_symlinks=False,
        )
        # hf_hub_download saves with the original filename; rename if different
        cached_path = Path(cached)
        if cached_path.name != local_name:
            final = dest_dir / local_name
            cached_path.rename(final)

        elapsed = time.time() - t0
        size_gb = dest.stat().st_size / 1e9 if dest.exists() else 0
        print(f"  OK   {subdir}/{local_name} ({size_gb:.1f} GB, {elapsed:.0f}s)")
        return True
    except Exception as e:
        print(f"  FAIL {subdir}/{local_name}: {e}", file=sys.stderr)
        return False


def main():
    print(f"ComfyUI Model Downloader")
    print(f"Models root: {MODELS_ROOT}")
    print(f"HF token: {'set' if HF_TOKEN else 'NOT SET (public models only)'}")
    print(f"Models to check: {len(MANIFEST)}")
    print()

    downloaded = 0
    skipped = 0
    failed = 0

    for repo, filename, subdir, local_name in MANIFEST:
        try:
            if download_model(repo, filename, subdir, local_name):
                downloaded += 1
            else:
                skipped += 1
        except Exception as e:
            print(f"  FAIL {subdir}/{local_name}: {e}", file=sys.stderr)
            failed += 1

    print()
    print(f"Done: {downloaded} downloaded, {skipped} already present, {failed} failed")

    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
