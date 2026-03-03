"""
diffueraser_worker.py
---------------------
DiffuEraser video inpainting worker for VideoAgent.

Pipeline
--------
  1. Extract frames    ffmpeg -noautorotate + transpose filter → JPEG frames
                       (JPEG required by SAM2 video predictor)
  2. SAM2 propagation  Centroid of mask_b64 → point prompt on frame 0
                       → per-frame PNG masks via SAM2 video predictor
  3. Build videos      Assemble corrected.mp4 + mask.mp4 for DiffuEraser input
  4. DiffuEraser       ProPainter prior pass + diffusion UNet (two-stage)
                       960px default; OOM retry at 640px
  5. Finalise          Copy result, cleanup temp dir

Interface
---------
  run_diffueraser_job(job_id, video_path, mask_b64, start, end) -> str
    Returns absolute path to output MP4 on success.
    Raises RuntimeError on failure.
    Writes progress to output/inpaint_jobs/<job_id>.json throughout.
    Status format matches lama_worker.py exactly.

Status JSON:
  { status, progress, frames_done, frames_total, estimated_seconds,
    output_path, error }

CLI usage (for testing):
  python3 -m scripts.diffueraser_worker \\
    --video  path/to/clip.mp4 \\
    --mask   path/to/mask.png \\
    --start  0.0 \\
    --end    3.5 \\
    --job_id test-001

Environment variables (all have Linux/Vast.ai defaults):
  DIFFUERASER_DIR          DiffuEraser repo root  (/workspace/DiffuEraser)
  DIFFUERASER_WEIGHTS      DiffuEraser weights dir (DIFFUERASER_DIR/weights)
  STABLE_DIFFUSION_DIR     SD 1.5 weights          (/workspace/stable-diffusion-v1-5)
  SAM2_CHECKPOINT          Full path to .pt file   (SAM2_CHECKPOINTS_DIR/sam2_hiera_large.pt)
  SAM2_CHECKPOINTS_DIR     Dir containing the .pt  (/workspace/sam2_checkpoints)
  SAM2_MODEL_CFG           Hydra config name        (configs/sam2/sam2_hiera_l)
  DIFFUERASER_MAX_SIZE     Max short-side px        (960)
  DIFFUERASER_FALLBACK_SIZE  OOM fallback px        (640)
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import math
import os
import shutil
import sys
import time
from pathlib import Path
from typing import List, Optional, Tuple

import cv2
import numpy as np
from PIL import Image

import subprocess

# ── Paths ──────────────────────────────────────────────────────────────────────

BASE_DIR         = Path(__file__).resolve().parent.parent
INPAINT_JOBS_DIR = BASE_DIR / "output" / "inpaint_jobs"
INPAINT_TEMP_DIR = BASE_DIR / "output" / "inpaint_temp"
INPAINT_OUT_DIR  = BASE_DIR / "output" / "inpainted"

DE_DIR     = Path(os.environ.get("DIFFUERASER_DIR",   "/workspace/DiffuEraser"))
DE_WEIGHTS = Path(os.environ.get("DIFFUERASER_WEIGHTS", str(DE_DIR / "weights")))
SD15_DIR   = Path(os.environ.get("STABLE_DIFFUSION_DIR", "/workspace/stable-diffusion-v1-5"))

# SAM2 — support both full-path and directory overrides
SAM2_CHECKPOINT = Path(
    os.environ.get(
        "SAM2_CHECKPOINT",
        str(
            Path(os.environ.get("SAM2_CHECKPOINTS_DIR", "/workspace/sam2_checkpoints"))
            / "sam2_hiera_large.pt"
        ),
    )
)
SAM2_MODEL_CFG = os.environ.get("SAM2_MODEL_CFG", "configs/sam2/sam2_hiera_l")

DE_MAX_SIZE      = int(os.environ.get("DIFFUERASER_MAX_SIZE",      "960"))
DE_FALLBACK_SIZE = int(os.environ.get("DIFFUERASER_FALLBACK_SIZE", "640"))


# ── Status helpers ─────────────────────────────────────────────────────────────

def _write_status(job_id: str, payload: dict) -> None:
    INPAINT_JOBS_DIR.mkdir(parents=True, exist_ok=True)
    (INPAINT_JOBS_DIR / f"{job_id}.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )


def _initial_status() -> dict:
    return {
        "status":            "running",
        "progress":          0.0,
        "frames_done":       0,
        "frames_total":      0,
        "estimated_seconds": None,
        "output_path":       None,
        "error":             None,
    }


# ── Video probe ────────────────────────────────────────────────────────────────

def _probe_video(video_path: str) -> dict:
    """Return {width, height, fps, duration} for the first video stream."""
    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_streams",
        video_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed on {video_path}:\n{result.stderr}")

    data = json.loads(result.stdout)
    for stream in data.get("streams", []):
        if stream.get("codec_type") != "video":
            continue
        w = int(stream["width"])
        h = int(stream["height"])
        fps_str = stream.get("avg_frame_rate") or stream.get("r_frame_rate", "30/1")
        try:
            num, den = fps_str.split("/")
            fps = float(num) / float(den) if float(den) != 0.0 else 30.0
        except (ValueError, AttributeError):
            fps = 30.0
        duration = float(stream.get("duration", 0.0))
        return {"width": w, "height": h, "fps": fps, "duration": duration}

    raise RuntimeError(f"No video stream found in {video_path}")


def _detect_rotation(video_path: str) -> int:
    """Return CW rotation angle from video metadata (0, 90, 180, 270)."""
    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_streams",
        video_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0 or not result.stdout:
        return 0
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return 0

    for stream in data.get("streams", []):
        if stream.get("codec_type") != "video":
            continue
        # Older format: rotate tag
        tag = stream.get("tags", {}).get("rotate", "")
        if tag:
            try:
                return int(tag) % 360
            except (ValueError, TypeError):
                pass
        # Newer format: side_data_list display matrix
        for entry in stream.get("side_data_list", []):
            rot = entry.get("rotation")
            if rot is not None:
                try:
                    return int(rot) % 360
                except (ValueError, TypeError):
                    pass
    return 0


def _transpose_filter(rotation: int) -> Optional[str]:
    """Map a CW rotation angle to the ffmpeg vf string that corrects it."""
    return {90: "transpose=2", 180: "vflip,hflip", 270: "transpose=1"}.get(rotation)


def _target_dimensions(
    stored_w: int,
    stored_h: int,
    rotation: int,
    max_short_side: int,
) -> Tuple[int, int]:
    """Return display-correct (w, h) after rotation and scaling to max_short_side."""
    if rotation in (90, 270):
        disp_w, disp_h = stored_h, stored_w
    else:
        disp_w, disp_h = stored_w, stored_h

    short = min(disp_w, disp_h)
    if short <= max_short_side:
        return (disp_w // 2) * 2, (disp_h // 2) * 2

    scale = max_short_side / short
    return (int(disp_w * scale) // 2) * 2, (int(disp_h * scale) // 2) * 2


# ── Frame extraction ───────────────────────────────────────────────────────────

def _extract_frames(
    video_path: str,
    frames_dir: Path,
    start: float,
    duration: float,
    rotation: int,
    target_w: int,
    target_h: int,
) -> List[Path]:
    """
    Extract rotation-corrected JPEG frames from the video segment.
    JPEG format is required by SAM2's video predictor init_state().
    Returns a sorted list of extracted frame paths.
    """
    frames_dir.mkdir(parents=True, exist_ok=True)

    filters: list = []
    tf = _transpose_filter(rotation)
    if tf:
        filters.append(tf)
    filters.append(f"scale={target_w}:{target_h}")

    cmd = [
        "ffmpeg", "-y",
        "-noautorotate",
        "-ss", f"{start:.6f}",
        "-i", video_path,
        "-t", f"{duration:.6f}",
        "-vf", ",".join(filters),
        "-q:v", "2",          # high-quality JPEG (1=best, 31=worst)
        "-f", "image2",
        str(frames_dir / "%06d.jpg"),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"ffmpeg frame extraction failed:\n{result.stderr[-3000:]}"
        )

    frames = sorted(frames_dir.glob("*.jpg"))
    if not frames:
        raise RuntimeError(
            f"No frames extracted from {video_path} "
            f"(start={start:.3f}, duration={duration:.3f})"
        )
    return frames


# ── Mask centroid ──────────────────────────────────────────────────────────────

def _centroid_pixels(
    mask_b64: str,
    frame_w: int,
    frame_h: int,
) -> Tuple[float, float]:
    """
    Decode the user's drawn mask PNG and return its centroid as pixel
    coordinates in the frame (scaled to frame_w × frame_h).
    Falls back to frame centre if mask is empty.
    """
    mask_bytes = base64.b64decode(mask_b64)
    mask_img   = np.array(Image.open(io.BytesIO(mask_bytes)).convert("L"), dtype=np.uint8)
    ys, xs     = np.where(mask_img > 127)
    if len(xs) == 0:
        return float(frame_w) / 2.0, float(frame_h) / 2.0
    cx_norm = float(xs.mean()) / mask_img.shape[1]
    cy_norm = float(ys.mean()) / mask_img.shape[0]
    return cx_norm * frame_w, cy_norm * frame_h


# ── SAM2 mask propagation ──────────────────────────────────────────────────────

def _propagate_masks_sam2(
    frames_dir: Path,
    masks_dir: Path,
    mask_b64: str,
    frame_w: int,
    frame_h: int,
    status: dict,
    job_id: str,
) -> int:
    """
    Use SAM2 video predictor to propagate the user's single-frame mask to all
    frames (forward then backward).

    Saves per-frame PNG masks to masks_dir as %06d.png.
    Deletes predictor and calls torch.cuda.empty_cache() before returning
    so DiffuEraser can claim VRAM for the next step.
    Returns the number of frames processed.
    """
    import torch
    from sam2.build_sam import build_sam2_video_predictor

    cfg_name = SAM2_MODEL_CFG
    if not cfg_name.endswith(".yaml"):
        cfg_name = f"{cfg_name}.yaml"

    cx_px, cy_px = _centroid_pixels(mask_b64, frame_w, frame_h)
    print(
        f"[diffueraser] {job_id[:8]}  SAM2 centroid "
        f"({cx_px:.1f}, {cy_px:.1f}) in {frame_w}×{frame_h}  "
        f"cfg={cfg_name}  ckpt={SAM2_CHECKPOINT}"
    )

    masks_dir.mkdir(parents=True, exist_ok=True)
    n_frames    = len(sorted(frames_dir.glob("*.jpg")))
    frames_done = 0

    predictor = build_sam2_video_predictor(cfg_name, str(SAM2_CHECKPOINT))

    with torch.inference_mode():
        state = predictor.init_state(video_path=str(frames_dir))

        predictor.add_new_points_or_box(
            inference_state=state,
            frame_idx=0,
            obj_id=1,
            points=np.array([[cx_px, cy_px]], dtype=np.float32),
            labels=np.array([1], dtype=np.int32),  # 1 = foreground
        )

        # Forward pass
        for out_idx, _obj_ids, logits in predictor.propagate_in_video(state):
            mask_u8 = (logits[0] > 0.0).cpu().numpy().squeeze().astype(np.uint8) * 255
            cv2.imwrite(str(masks_dir / f"{out_idx:06d}.png"), mask_u8)
            frames_done += 1
            status.update({
                "frames_done":  frames_done,
                "frames_total": n_frames,
                "progress":     0.10 + (frames_done / max(n_frames, 1)) * 0.30,
            })
            _write_status(job_id, status)

        # Backward pass — fills any frames missed going forward
        for out_idx, _obj_ids, logits in predictor.propagate_in_video(
            state, reverse=True
        ):
            mask_path = masks_dir / f"{out_idx:06d}.png"
            if not mask_path.exists():
                mask_u8 = (
                    (logits[0] > 0.0).cpu().numpy().squeeze().astype(np.uint8) * 255
                )
                cv2.imwrite(str(mask_path), mask_u8)

    # Free VRAM before DiffuEraser loads its models
    del predictor
    torch.cuda.empty_cache()
    print(f"[diffueraser] {job_id[:8]}  SAM2 done — {frames_done} masks in {masks_dir}")
    return frames_done


# ── Video assembly ─────────────────────────────────────────────────────────────

def _assemble_video(frames_pattern: str, output_path: Path, fps: float) -> None:
    """
    Assemble a sequence of image frames into an H.264 MP4 at the given fps.
    CRF 17 is visually lossless for the source video; same settings used for
    the mask video (masks are black/white so CRF 17 is effectively lossless).
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y",
        "-framerate", f"{fps:.6f}",
        "-i", frames_pattern,
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-crf", "17",
        "-preset", "fast",
        str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg video assembly failed:\n{result.stderr[-3000:]}")


# ── DiffuEraser inference ──────────────────────────────────────────────────────

def _run_diffueraser(
    corrected_video: Path,
    mask_video: Path,
    temp_dir: Path,
    video_length_s: int,
    max_img_size: int,
    job_id: str,
    status: dict,
) -> Path:
    """
    Run DiffuEraser's two-stage inpainting pipeline:
      Stage 1 — ProPainter prior (temporal propagation)
      Stage 2 — DiffuEraser diffusion UNet (hallucination + coherence)

    Uses sys.path.insert so DiffuEraser's local package imports resolve.
    Returns path to the result MP4.
    """
    # Prepend DiffuEraser dir so its local imports (diffueraser.*, propainter.*) resolve
    de_dir_str = str(DE_DIR)
    if de_dir_str not in sys.path:
        sys.path.insert(0, de_dir_str)
    # diffueraser.py hardcodes "weights/PCM_Weights" as a relative path inside
    # pipeline.load_lora_weights() — chdir to DE_DIR so it resolves correctly.
    os.chdir(str(DE_DIR))

    import torch

    try:
        from diffueraser.diffueraser import DiffuEraser
        from propainter.inference import Propainter, get_device
    except ImportError as exc:
        raise ImportError(
            f"Cannot import DiffuEraser. "
            f"Ensure DIFFUERASER_DIR={DE_DIR} is correct and requirements installed.\n"
            f"Original error: {exc}"
        ) from exc

    # Weight path validation — give a clear error before CUDA initialisation
    weight_dirs = {
        "SD 1.5 base model (STABLE_DIFFUSION_DIR)":  str(SD15_DIR),
        "VAE (DIFFUERASER_WEIGHTS/sd-vae-ft-mse)":   str(DE_WEIGHTS / "sd-vae-ft-mse"),
        "DiffuEraser (DIFFUERASER_WEIGHTS/diffuEraser)": str(DE_WEIGHTS / "diffuEraser"),
        "ProPainter prior (DIFFUERASER_WEIGHTS/propainter)": str(DE_WEIGHTS / "propainter"),
    }
    for label, path in weight_dirs.items():
        if not Path(path).exists():
            raise RuntimeError(
                f"DiffuEraser weight directory missing — {label}: {path}\n"
                "Run deploy_vastai.sh or set DIFFUERASER_WEIGHTS / STABLE_DIFFUSION_DIR."
            )

    device = get_device()
    print(
        f"[diffueraser] {job_id[:8]}  loading DiffuEraser on {device}, "
        f"max_img_size={max_img_size}"
    )

    priori_path = temp_dir / "priori.mp4"
    result_path = temp_dir / "result.mp4"

    video_inpainting_sd = DiffuEraser(
        device,
        str(SD15_DIR),
        str(DE_WEIGHTS / "sd-vae-ft-mse"),
        str(DE_WEIGHTS / "diffuEraser"),
        ckpt="2-Step",
    )
    propainter_model = Propainter(str(DE_WEIGHTS / "propainter"), device=device)

    # Stage 1 — ProPainter prior
    print(f"[diffueraser] {job_id[:8]}  Stage 1: ProPainter prior…")
    status.update({"progress": 0.55})
    _write_status(job_id, status)

    propainter_model.forward(
        str(corrected_video),
        str(mask_video),
        str(priori_path),
        video_length=video_length_s,
        ref_stride=10,
        neighbor_length=10,
        subvideo_length=50,
        mask_dilation=8,
    )

    if not priori_path.exists():
        raise RuntimeError(f"ProPainter prior produced no output at {priori_path}")

    # Stage 2 — DiffuEraser diffusion
    print(f"[diffueraser] {job_id[:8]}  Stage 2: DiffuEraser diffusion…")
    status.update({"progress": 0.72})
    _write_status(job_id, status)

    video_inpainting_sd.forward(
        str(corrected_video),
        str(mask_video),
        str(priori_path),
        str(result_path),
        max_img_size=max_img_size,
        video_length=video_length_s,
        mask_dilation_iter=8,
        guidance_scale=None,  # default = 0 per DiffuEraser docs
    )

    torch.cuda.empty_cache()

    if not result_path.exists():
        raise RuntimeError(f"DiffuEraser produced no output at {result_path}")

    return result_path


# ── Main entry point ───────────────────────────────────────────────────────────

def run_diffueraser_job(
    job_id: str,
    video_path: str,
    mask_b64: str,
    start: float,
    end: float,
) -> str:
    """
    Run DiffuEraser inpainting on a video segment.

    Drop-in replacement for run_lama_job() in lama_worker.py.
    Uses the same status JSON format so app.py polls transparently.

    Args:
        job_id:     UUID string for temp dir and status file names.
        video_path: Absolute path to source video.
        mask_b64:   Base64-encoded PNG mask drawn by user on one frame.
        start:      Segment start time in seconds.
        end:        Segment end time in seconds.

    Returns:
        Absolute path to inpainted output MP4.

    Raises:
        RuntimeError on any processing failure.
    """
    status = _initial_status()
    _write_status(job_id, status)

    temp_dir    = INPAINT_TEMP_DIR / job_id
    frames_dir  = temp_dir / "frames"
    masks_dir   = temp_dir / "masks"
    output_path = INPAINT_OUT_DIR / f"{job_id}.mp4"
    INPAINT_OUT_DIR.mkdir(parents=True, exist_ok=True)

    try:
        duration = end - start
        if duration <= 0:
            raise ValueError(f"Invalid segment bounds: start={start}, end={end}")

        # ── Step 1: Probe + extract rotation-corrected JPEG frames ───────────
        status.update({"status": "extracting", "progress": 0.02})
        _write_status(job_id, status)

        probe      = _probe_video(video_path)
        rotation   = _detect_rotation(video_path)
        fps        = round(probe["fps"])
        target_w, target_h = _target_dimensions(
            probe["width"], probe["height"], rotation, DE_MAX_SIZE
        )
        video_length_s = math.ceil(duration)

        print(
            f"[diffueraser] {job_id[:8]}  "
            f"source={probe['width']}×{probe['height']}  rotation={rotation}°  "
            f"target={target_w}×{target_h}  fps={fps:.3f}  duration={duration:.2f}s"
        )

        frames = _extract_frames(
            video_path, frames_dir,
            start=start, duration=duration,
            rotation=rotation,
            target_w=target_w, target_h=target_h,
        )
        n_frames = len(frames)
        print(f"[diffueraser] {job_id[:8]}  {n_frames} JPEG frames extracted")

        status.update({"frames_total": n_frames, "progress": 0.10})
        _write_status(job_id, status)

        # ── Step 2: SAM2 mask propagation ────────────────────────────────────
        status.update({"status": "masking", "progress": 0.10})
        _write_status(job_id, status)

        print(f"[diffueraser] {job_id[:8]}  SAM2 mask propagation…")
        _propagate_masks_sam2(
            frames_dir, masks_dir, mask_b64,
            target_w, target_h, status, job_id,
        )

        # ── Step 3: Assemble corrected video + mask video ────────────────────
        status.update({"progress": 0.42})
        _write_status(job_id, status)

        corrected_video = temp_dir / "corrected.mp4"
        mask_video      = temp_dir / "mask.mp4"

        print(f"[diffueraser] {job_id[:8]}  assembling corrected.mp4…")
        _assemble_video(str(frames_dir / "%06d.jpg"), corrected_video, fps)

        print(f"[diffueraser] {job_id[:8]}  assembling mask.mp4…")
        _assemble_video(str(masks_dir / "%06d.png"), mask_video, fps)

        status.update({"progress": 0.50})
        _write_status(job_id, status)

        # ── Step 4: DiffuEraser (with OOM fallback) ──────────────────────────
        status.update({"status": "inpainting", "progress": 0.52})
        _write_status(job_id, status)

        result_mp4: Optional[Path] = None
        for attempt_size in (DE_MAX_SIZE, DE_FALLBACK_SIZE):
            try:
                result_mp4 = _run_diffueraser(
                    corrected_video, mask_video, temp_dir,
                    video_length_s, attempt_size, job_id, status,
                )
                break
            except RuntimeError as exc:
                err_lower = str(exc).lower()
                is_oom    = "out of memory" in err_lower or (
                    "cuda" in err_lower and "memory" in err_lower
                )
                if is_oom and attempt_size != DE_FALLBACK_SIZE:
                    print(
                        f"[diffueraser] {job_id[:8]}  OOM at {attempt_size}px — "
                        f"retrying at {DE_FALLBACK_SIZE}px"
                    )
                    status.update({
                        "error": (
                            f"OOM at {attempt_size}px, retrying at {DE_FALLBACK_SIZE}px"
                        )
                    })
                    _write_status(job_id, status)
                    import torch; torch.cuda.empty_cache()
                    continue
                raise

        if result_mp4 is None or not result_mp4.exists():
            raise RuntimeError("DiffuEraser produced no output")

        # ── Step 5: Finalise ─────────────────────────────────────────────────
        status.update({"progress": 0.95})
        _write_status(job_id, status)

        shutil.copy2(str(result_mp4), str(output_path))
        shutil.rmtree(str(temp_dir), ignore_errors=True)

        status.update({
            "status":            "done",
            "progress":          1.0,
            "frames_done":       n_frames,
            "estimated_seconds": None,
            "output_path":       str(output_path),
            "error":             None,
        })
        _write_status(job_id, status)
        print(f"[diffueraser] {job_id[:8]}  complete → {output_path}")
        return str(output_path)

    except Exception as exc:
        error_msg = str(exc)
        print(f"[diffueraser] {job_id[:8]}  FAILED: {error_msg}")
        status.update({"status": "failed", "error": error_msg})
        _write_status(job_id, status)
        raise RuntimeError(error_msg) from exc


# ── CLI entry point ────────────────────────────────────────────────────────────

def _cli():
    parser = argparse.ArgumentParser(
        description="Run DiffuEraser inpainting on a video segment (standalone test)"
    )
    parser.add_argument("--video",  required=True,  help="Path to source video")
    parser.add_argument("--mask",   required=True,  help="Path to mask PNG file")
    parser.add_argument("--start",  type=float, default=0.0)
    parser.add_argument("--end",    type=float, required=True)
    parser.add_argument("--job_id", default="cli-test")
    args = parser.parse_args()

    with open(args.mask, "rb") as f:
        mask_b64 = base64.b64encode(f.read()).decode()

    output = run_diffueraser_job(
        job_id=args.job_id,
        video_path=args.video,
        mask_b64=mask_b64,
        start=args.start,
        end=args.end,
    )
    print(f"\nOutput: {output}")


if __name__ == "__main__":
    _cli()
