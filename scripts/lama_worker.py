"""
lama_worker.py
--------------
LaMa-based generative inpainting worker for VideoAgent.

Drop-in parallel to ProPainter's run_inpaint_job() in inpaint_worker.py.
Uses IOPaint's LaMa implementation for per-frame background hallucination —
the correct approach when the background behind a removed subject is never
visible elsewhere in the clip (e.g. removing crowd members from event videos).

Pipeline
--------
  1. Probe video:   ffprobe → rotation angle, dimensions, fps
  2. Extract frames: ffmpeg -noautorotate + transpose filter + scale to
                     MAX_RESOLUTION short side → PNGs in temp dir
  3. Prepare mask:  decode base64 PNG, convert to single-channel uint8,
                    resize to match extracted frame dimensions
  4. Load LaMa:     IOPaint LaMa on CUDA (falls back to CPU if unavailable)
  5. Per-frame run: LaMa(frame_rgb, mask, config) → inpainted frame
  6. Reassemble:    ffmpeg images → MP4 at original fps
  7. Cleanup:       remove temp dir on success

Interface
---------
  run_lama_job(job_id, video_path, mask_b64, start, end) -> str
    Returns the absolute path to the output video on success.
    Raises RuntimeError on failure.
    Writes progress to output/inpaint_jobs/<job_id>.json throughout,
    using the same status format as inpaint_worker.py so app.py can poll
    it transparently.

Status JSON format (matches inpaint_worker.py exactly):
  {
    "status":            "running" | "done" | "failed",
    "progress":          float  0.0–1.0,
    "frames_done":       int,
    "frames_total":      int,
    "estimated_seconds": int | null,
    "output_path":       str | null,
    "error":             str | null
  }

CLI usage (for testing):
  python -m scripts.lama_worker \\
    --video  path/to/clip.mp4 \\
    --mask   path/to/mask.png \\
    --start  0.0 \\
    --end    3.5 \\
    --job_id test-001
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import shutil
import subprocess
import time
from pathlib import Path
from typing import Optional, Tuple

import cv2
import numpy as np
from PIL import Image

# ── Paths (mirror inpaint_worker.py layout exactly) ───────────────────────────
BASE_DIR         = Path(__file__).resolve().parent.parent
INPAINT_JOBS_DIR = BASE_DIR / "output" / "inpaint_jobs"
INPAINT_TEMP_DIR = BASE_DIR / "output" / "inpaint_temp"
INPAINT_OUT_DIR  = BASE_DIR / "output" / "inpainted"

# RTX 3090 24 GB VRAM — 1080p short side is comfortable.
# Lower this if running on a smaller card.
MAX_RESOLUTION = int(__import__("os").environ.get("LAMA_MAX_RESOLUTION", "1080"))


# ── Status helpers (same format as inpaint_worker.py) ─────────────────────────

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


# ── Rotation detection ────────────────────────────────────────────────────────

def _detect_rotation(video_path: str) -> int:
    """
    Return the CW rotation angle stored in the video's metadata (0, 90, 180, 270).
    Checks both the legacy `tags.rotate` field and the newer `side_data_list` format.
    Returns 0 if no rotation metadata is found or ffprobe fails.
    """
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

        # Older format: rotate stored in stream tags (common on iOS/Android).
        rotate_tag = stream.get("tags", {}).get("rotate", "")
        if rotate_tag:
            try:
                return int(rotate_tag) % 360
            except (ValueError, TypeError):
                pass

        # Newer format: rotation in side_data_list (e.g. MOV display matrix).
        # Values here can be negative (e.g. -90 for 270° effective rotation).
        for entry in stream.get("side_data_list", []):
            rotation = entry.get("rotation")
            if rotation is not None:
                try:
                    return int(rotation) % 360
                except (ValueError, TypeError):
                    pass

    return 0


def _transpose_filter(rotation: int) -> Optional[str]:
    """
    Map a CW rotation angle to the ffmpeg vf string that corrects it.

    Rotation tag = "display this frame after rotating N° CW".
    To get display-correct frames from -noautorotate extraction we apply
    the inverse rotation via ffmpeg's transpose filter.

      90°  stored CW   → rotate CCW 90° to display = transpose=2
      180°             → flip both axes              = vflip,hflip
      270° stored CW   → rotate CW 90° to display  = transpose=1
      0°               → no correction needed        = None

    Note: transpose=2 matches the fix already applied in inpaint_worker.py
    for -90° (270°) phone videos.
    """
    _MAP = {
        90:  "transpose=2",
        180: "vflip,hflip",
        270: "transpose=1",
    }
    return _MAP.get(rotation)


# ── Video probe ───────────────────────────────────────────────────────────────

def _probe_video(video_path: str) -> dict:
    """
    Return {width, height, fps, duration} for the first video stream.
    width/height are the stored (pre-rotation) dimensions.
    """
    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_streams",
        video_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"ffprobe failed on {video_path}:\n{result.stderr}"
        )

    data = json.loads(result.stdout)
    for stream in data.get("streams", []):
        if stream.get("codec_type") != "video":
            continue

        w = int(stream["width"])
        h = int(stream["height"])

        # avg_frame_rate is more reliable for VFR content than r_frame_rate.
        fps_str = stream.get("avg_frame_rate") or stream.get("r_frame_rate", "30/1")
        try:
            num, den = fps_str.split("/")
            fps = float(num) / float(den) if float(den) != 0.0 else 30.0
        except (ValueError, AttributeError):
            fps = 30.0

        duration = float(stream.get("duration", 0.0))
        return {"width": w, "height": h, "fps": fps, "duration": duration}

    raise RuntimeError(f"No video stream found in {video_path}")


def _target_dimensions(
    stored_w: int,
    stored_h: int,
    rotation: int,
    max_short_side: int,
) -> Tuple[int, int]:
    """
    Compute the output (width, height) after applying rotation and scaling
    so the short side is at most max_short_side. Both dimensions rounded to
    even numbers (required by libx264).

    stored_w/h are the raw frame dimensions before rotation correction.
    After a 90°/270° rotation the effective display dimensions are swapped.
    """
    if rotation in (90, 270):
        disp_w, disp_h = stored_h, stored_w
    else:
        disp_w, disp_h = stored_w, stored_h

    short = min(disp_w, disp_h)
    if short <= max_short_side:
        # Already within budget — just enforce even numbers.
        return (disp_w // 2) * 2, (disp_h // 2) * 2

    scale   = max_short_side / short
    target_w = (int(disp_w * scale) // 2) * 2
    target_h = (int(disp_h * scale) // 2) * 2
    return target_w, target_h


# ── Frame extraction ──────────────────────────────────────────────────────────

def _extract_frames(
    video_path: str,
    out_dir: Path,
    start: float,
    duration: float,
    rotation: int,
    target_w: int,
    target_h: int,
) -> list:
    """
    Extract the video segment as individual PNG frames.
    Applies -noautorotate + transpose filter for rotation-metadata videos,
    then scales to target dimensions.
    Returns a sorted list of Path objects for the extracted frames.
    """
    out_dir.mkdir(parents=True, exist_ok=True)

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
        # q:v 1 = near-lossless JPEG quality; we write PNG anyway so this
        # controls the internal quantiser used before the PNG encoder.
        "-q:v", "1",
        "-f", "image2",
        str(out_dir / "%06d.png"),
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"ffmpeg frame extraction failed:\n"
            f"{result.stderr[-3000:]}"
        )

    frames = sorted(out_dir.glob("*.png"))
    if not frames:
        raise RuntimeError(
            f"No frames extracted from {video_path} "
            f"(start={start:.3f}, duration={duration:.3f})"
        )
    return frames


# ── Mask preparation ──────────────────────────────────────────────────────────

def _prepare_mask(mask_b64: str, target_w: int, target_h: int) -> np.ndarray:
    """
    Decode the base64 PNG mask and return a single-channel uint8 array
    resized to (target_h, target_w).

    Convention (matches the UI's DrawRegionModal):
      255 = inpaint this pixel
        0 = keep this pixel

    Handles grayscale, RGB, and RGBA input PNGs.
    """
    mask_bytes = base64.b64decode(mask_b64)
    img = Image.open(io.BytesIO(mask_bytes)).convert("L")
    img = img.resize((target_w, target_h), Image.NEAREST)
    arr = np.array(img, dtype=np.uint8)
    # Binarise: anything above mid-grey counts as "inpaint".
    arr = (arr > 127).astype(np.uint8) * 255
    return arr


# ── LaMa model ────────────────────────────────────────────────────────────────

def _load_lama(device_str: str):
    """
    Load the LaMa model from IOPaint onto the given device.
    Raises ImportError if iopaint is not installed.
    Returns (model, config) ready to use with _run_lama_on_frame().
    """
    import torch
    from iopaint.model.lama import LaMa

    device = torch.device(device_str)
    model  = LaMa(device)

    # InpaintRequest: use ORIGINAL strategy so we process frames at
    # full extracted resolution without tiling or internal resizing.
    # IOPaint changed the import path between versions — try both.
    try:
        from iopaint.schema import InpaintRequest, HDStrategy
        config = InpaintRequest(hd_strategy=HDStrategy.ORIGINAL)
    except (ImportError, TypeError):
        try:
            from iopaint.schema import InpaintRequest
            config = InpaintRequest()
        except ImportError as exc:
            raise ImportError(
                "Could not import InpaintRequest from iopaint.schema. "
                "Ensure iopaint >= 1.2 is installed."
            ) from exc

    return model, config


def _run_lama_on_frame(
    model,
    config,
    frame_bgr: np.ndarray,
    mask: np.ndarray,
) -> np.ndarray:
    """
    Run LaMa on a single frame.
    IOPaint's LaMa expects RGB input and returns RGB output.

    Args:
        frame_bgr: HxWx3 uint8 BGR (OpenCV convention)
        mask:      HxW uint8 (0=keep, 255=inpaint)

    Returns: HxWx3 uint8 BGR
    """
    frame_rgb    = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
    result_rgb   = model(frame_rgb, mask, config)
    return cv2.cvtColor(np.asarray(result_rgb, dtype=np.uint8), cv2.COLOR_RGB2BGR)


# ── Frame reassembly ──────────────────────────────────────────────────────────

def _reassemble_video(frames_dir: Path, output_path: Path, fps: float) -> None:
    """
    Reassemble inpainted PNG frames into an H.264 MP4 at the original fps.
    CRF 18 is visually lossless; preset fast keeps encoding time short.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        "ffmpeg", "-y",
        "-framerate", f"{fps:.6f}",
        "-i", str(frames_dir / "%06d.png"),
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-crf", "18",
        "-preset", "fast",
        str(output_path),
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"ffmpeg reassembly failed:\n{result.stderr[-3000:]}"
        )


# ── Main entry point ──────────────────────────────────────────────────────────

def run_lama_job(
    job_id: str,
    video_path: str,
    mask_b64: str,
    start: float,
    end: float,
    keep_frames: bool = False,
) -> str:
    """
    Run LaMa generative inpainting on a video segment.

    Drop-in replacement for run_inpaint_job() in inpaint_worker.py.
    Uses the same status JSON format so app.py can poll it transparently.

    Args:
        job_id:     UUID string — used for temp dir and status file names.
        video_path: Absolute path to the source video.
        mask_b64:   Base64-encoded PNG mask (white=inpaint, black=keep).
        start:       Segment start time in seconds.
        end:         Segment end time in seconds.
        keep_frames: When True, the inpainted frame PNGs are NOT deleted after
                     reassembly. Useful when chaining with e2fgvi_worker, which
                     needs the frames at output/inpaint_temp/<job_id>/inpainted/.

    Returns:
        Absolute path to the inpainted output MP4.

    Raises:
        RuntimeError: On any processing failure.
        The status JSON is updated with {"status": "failed", "error": ...}
        before raising, so Flask can surface the error to the UI.
    """
    import torch

    status = _initial_status()
    _write_status(job_id, status)

    temp_dir      = INPAINT_TEMP_DIR / job_id
    frames_dir    = temp_dir / "frames"
    inpainted_dir = temp_dir / "inpainted"
    output_path   = INPAINT_OUT_DIR / f"{job_id}.mp4"

    INPAINT_OUT_DIR.mkdir(parents=True, exist_ok=True)

    try:
        duration = end - start
        if duration <= 0:
            raise ValueError(f"Invalid segment bounds: start={start}, end={end}")

        # ── 1. Probe video ────────────────────────────────────────────────────
        probe    = _probe_video(video_path)
        rotation = _detect_rotation(video_path)
        fps      = probe["fps"]
        target_w, target_h = _target_dimensions(
            probe["width"], probe["height"], rotation, MAX_RESOLUTION
        )

        print(
            f"[lama] {job_id[:8]}  source={probe['width']}x{probe['height']}"
            f"  rotation={rotation}°  target={target_w}x{target_h}"
            f"  fps={fps:.3f}  duration={duration:.2f}s"
            f"  device={'cuda' if torch.cuda.is_available() else 'cpu'}"
        )

        # ── 2. Prepare mask ───────────────────────────────────────────────────
        mask = _prepare_mask(mask_b64, target_w, target_h)
        inpaint_pixels = int(np.sum(mask > 127))
        inpaint_pct    = 100.0 * inpaint_pixels / (target_w * target_h)
        print(
            f"[lama] {job_id[:8]}  mask: {inpaint_pixels} px inpainted "
            f"({inpaint_pct:.1f}% of frame)"
        )

        # ── 3. Extract frames ─────────────────────────────────────────────────
        print(f"[lama] {job_id[:8]}  extracting frames...")
        frames = _extract_frames(
            video_path, frames_dir,
            start=start, duration=duration,
            rotation=rotation,
            target_w=target_w, target_h=target_h,
        )
        n_frames = len(frames)
        print(f"[lama] {job_id[:8]}  {n_frames} frames extracted")

        status.update({"frames_total": n_frames, "progress": 0.05})
        _write_status(job_id, status)

        # ── 4. Load LaMa ──────────────────────────────────────────────────────
        device_str = "cuda" if torch.cuda.is_available() else "cpu"
        print(f"[lama] {job_id[:8]}  loading LaMa on {device_str}...")
        model, config = _load_lama(device_str)
        print(f"[lama] {job_id[:8]}  LaMa ready — starting per-frame inpainting")

        # ── 5. Per-frame inpainting ───────────────────────────────────────────
        inpainted_dir.mkdir(parents=True, exist_ok=True)
        t_start = time.monotonic()

        for i, frame_path in enumerate(frames):
            frame_bgr = cv2.imread(str(frame_path))
            if frame_bgr is None:
                raise RuntimeError(f"Could not read extracted frame: {frame_path}")

            result_bgr = _run_lama_on_frame(model, config, frame_bgr, mask)

            out_frame_path = inpainted_dir / frame_path.name
            cv2.imwrite(str(out_frame_path), result_bgr)

            frames_done = i + 1
            elapsed     = time.monotonic() - t_start
            rate        = frames_done / elapsed if elapsed > 0 else 0.0
            remaining   = (n_frames - frames_done) / rate if rate > 0 else None

            status.update({
                "frames_done":       frames_done,
                "frames_total":      n_frames,
                # Progress envelope: 5% extraction, 90% inpainting, 5% reassembly.
                "progress":          0.05 + (frames_done / n_frames) * 0.90,
                "estimated_seconds": int(remaining) if remaining is not None else None,
            })
            _write_status(job_id, status)

        elapsed_inpaint = time.monotonic() - t_start
        print(
            f"[lama] {job_id[:8]}  inpainting done — "
            f"{n_frames} frames in {elapsed_inpaint:.1f}s "
            f"({elapsed_inpaint / n_frames:.2f}s/frame)"
        )

        # ── 6. Reassemble video ───────────────────────────────────────────────
        print(f"[lama] {job_id[:8]}  reassembling video at {fps:.3f} fps...")
        _reassemble_video(inpainted_dir, output_path, fps)

        if not output_path.exists():
            raise RuntimeError(
                f"Reassembly produced no output at {output_path}"
            )

        # ── 7. Cleanup temp dir ───────────────────────────────────────────────
        if not keep_frames:
            shutil.rmtree(str(temp_dir), ignore_errors=True)

        status.update({
            "status":            "done",
            "progress":          1.0,
            "estimated_seconds": None,
            "output_path":       str(output_path),
        })
        _write_status(job_id, status)

        total_elapsed = time.monotonic() - t_start
        print(
            f"[lama] {job_id[:8]}  complete — "
            f"total {total_elapsed:.1f}s  output: {output_path}"
        )
        return str(output_path)

    except Exception as exc:
        error_msg = str(exc)
        print(f"[lama] {job_id[:8]}  FAILED: {error_msg}")
        status.update({
            "status": "failed",
            "error":  error_msg,
        })
        _write_status(job_id, status)
        raise RuntimeError(error_msg) from exc


# ── CLI entry point (for testing without Flask) ───────────────────────────────

def _cli():
    parser = argparse.ArgumentParser(
        description="Run LaMa inpainting on a video segment (standalone test)"
    )
    parser.add_argument("--video",  required=True,  help="Path to source video")
    parser.add_argument("--mask",   required=True,  help="Path to mask PNG file")
    parser.add_argument("--start",  type=float, default=0.0)
    parser.add_argument("--end",    type=float, required=True)
    parser.add_argument("--job_id", default="cli-test")
    args = parser.parse_args()

    with open(args.mask, "rb") as f:
        mask_b64 = base64.b64encode(f.read()).decode()

    output = run_lama_job(
        job_id=args.job_id,
        video_path=args.video,
        mask_b64=mask_b64,
        start=args.start,
        end=args.end,
    )
    print(f"\nOutput: {output}")


if __name__ == "__main__":
    _cli()
