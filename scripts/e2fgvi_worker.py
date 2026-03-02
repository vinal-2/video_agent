"""
e2fgvi_worker.py
----------------
Temporal consistency wrapper around E2FGVI for video inpainting.

Accepts a directory of LaMa-inpainted frames (PNG) + the original mask and
applies E2FGVI's flow-guided temporal attention to remove per-frame flickering
introduced by LaMa's independent frame processing.

E2FGVI inference flow
---------------------
  For each frame i:
    1. Collect a sliding window of neighbor frames around i.
    2. Collect a sparse set of long-range reference frames.
    3. Zero out the mask region in each gathered frame.
    4. Feed (masked_frames, masks) through E2FGVI.
    5. Extract the prediction for frame i from the output.

Graceful degradation
--------------------
  If E2FGVI is not installed (E2FGVI_DIR missing or weights absent) the
  worker assembles the LaMa frames into a video without a temporal pass
  and returns normally.  The caller sees a valid output path; the job
  status is "done".  No exception is raised.

Interface
---------
  run_e2fgvi_job(job_id, frames_dir, mask_b64, fps, output_path) -> str
    Returns absolute path to output video.
    Writes progress to output/inpaint_jobs/<job_id>.json (same format as
    lama_worker.py / inpaint_worker.py) throughout.

Chaining with lama_worker
-------------------------
  lama_worker saves its inpainted frames to:
    output/inpaint_temp/<job_id>/inpainted/*.png
  and the fps from ffprobe.  App.py (or a future orchestrator) can:
    1. Call run_lama_job(...) which returns the lama MP4 path.
    2. Call run_e2fgvi_job(job_id, lama_frames_dir, mask_b64, fps)
       before lama cleans up its temp dir, to get the E2FGVI-smoothed result.

Environment variables
---------------------
  E2FGVI_DIR          path to cloned MCG-NKU/E2FGVI repo
                      (default /workspace/E2FGVI)
  E2FGVI_NEIGHBOR_LEN local sliding-window size  (default 10)
  E2FGVI_REF_STRIDE   long-range reference stride (default 10)

CLI usage (standalone test):
  python -m scripts.e2fgvi_worker \\
    --frames_dir output/inpaint_temp/my_job/inpainted \\
    --mask       path/to/mask.png \\
    --fps        29.97 \\
    --job_id     test-001
"""

from __future__ import annotations

import argparse
import base64
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Optional, Tuple
import os

import cv2
import numpy as np

# ── Paths (mirror lama_worker.py layout exactly) ──────────────────────────────
BASE_DIR         = Path(__file__).resolve().parent.parent
INPAINT_JOBS_DIR = BASE_DIR / "output" / "inpaint_jobs"
INPAINT_TEMP_DIR = BASE_DIR / "output" / "inpaint_temp"
INPAINT_OUT_DIR  = BASE_DIR / "output" / "inpainted"

E2FGVI_DIR = Path(os.environ.get("E2FGVI_DIR", "/workspace/E2FGVI"))

# Weights may live in either location depending on setup_inpaint.sh version.
_CKPT_CANDIDATES: List[Path] = [
    E2FGVI_DIR / "release_model" / "E2FGVI-HQ-CVPR22.pth",
    E2FGVI_DIR / "weights"       / "E2FGVI-HQ-CVPR22.pth",
]

# ── Inference knobs (tunable via env) ─────────────────────────────────────────
NEIGHBOR_LEN = int(os.environ.get("E2FGVI_NEIGHBOR_LEN", "10"))
REF_STRIDE   = int(os.environ.get("E2FGVI_REF_STRIDE",   "10"))


# ── Status helpers (same format as lama_worker.py) ────────────────────────────

def _write_status(job_id: str, payload: dict) -> None:
    INPAINT_JOBS_DIR.mkdir(parents=True, exist_ok=True)
    path = INPAINT_JOBS_DIR / f"{job_id}.json"
    tmp  = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    tmp.replace(path)


def _initial_status(frames_total: int = 0) -> dict:
    return {
        "status":            "running",
        "progress":          0.0,
        "frames_done":       0,
        "frames_total":      frames_total,
        "estimated_seconds": None,
        "output_path":       None,
        "error":             None,
    }


# ── E2FGVI availability ────────────────────────────────────────────────────────

def _find_checkpoint() -> Optional[Path]:
    for p in _CKPT_CANDIDATES:
        if p.exists() and p.stat().st_size > 100_000_000:  # expect ~430 MB
            return p
    return None


def _is_available() -> bool:
    if not E2FGVI_DIR.exists():
        return False
    if _find_checkpoint() is None:
        return False
    try:
        import torch  # noqa
        return True
    except ImportError:
        return False


# ── Model loading ──────────────────────────────────────────────────────────────

def _load_model(device):
    """
    Load E2FGVI-HQ InpaintGenerator onto device.
    Inserts E2FGVI repo into sys.path on first call.
    """
    import torch

    ckpt_path = _find_checkpoint()
    if ckpt_path is None:
        raise RuntimeError(
            f"E2FGVI weights not found. Searched:\n"
            + "\n".join(f"  {p}" for p in _CKPT_CANDIDATES)
        )

    repo = str(E2FGVI_DIR)
    if repo not in sys.path:
        sys.path.insert(0, repo)

    import model.e2fgvi_hq as net_module  # E2FGVI repo

    net = net_module.InpaintGenerator().to(device).eval()
    ckpt  = torch.load(str(ckpt_path), map_location=device, weights_only=False)
    state = ckpt.get("netG", ckpt.get("generator", ckpt))
    net.load_state_dict(state, strict=False)
    print(f"[e2fgvi] model loaded from {ckpt_path.name} on {device}")
    return net


# ── Frame / mask I/O ──────────────────────────────────────────────────────────

def _load_frames(frames_dir: Path) -> List[np.ndarray]:
    """Return sorted list of BGR numpy arrays from a PNG directory."""
    paths = sorted(frames_dir.glob("*.png"))
    if not paths:
        raise RuntimeError(f"No PNG frames found in {frames_dir}")
    result = []
    for p in paths:
        img = cv2.imread(str(p))
        if img is None:
            raise RuntimeError(f"Failed to read frame: {p}")
        result.append(img)
    return result


def _decode_mask(mask_b64: str, target_h: int, target_w: int) -> np.ndarray:
    """
    Decode base64 PNG mask → single-channel uint8 (0 or 255).
    Resizes to (target_h, target_w) with nearest-neighbour to preserve edges.
    """
    raw = base64.b64decode(mask_b64)
    arr = np.frombuffer(raw, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise RuntimeError("mask_b64 could not be decoded as an image")
    if img.shape != (target_h, target_w):
        img = cv2.resize(img, (target_w, target_h), interpolation=cv2.INTER_NEAREST)
    return (img > 127).astype(np.uint8) * 255


# ── Tensor helpers ─────────────────────────────────────────────────────────────

def _to_tensor(frames_bgr: List[np.ndarray], device) -> "torch.Tensor":
    """BGR list → [1, T, C, H, W] float32 in [-1, 1] on device."""
    import torch
    tensors = []
    for bgr in frames_bgr:
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
        tensors.append(torch.from_numpy(rgb).permute(2, 0, 1))  # [C, H, W]
    # [T, C, H, W] → [-1,1] → [1, T, C, H, W]
    return (torch.stack(tensors).to(device) * 2.0 - 1.0).unsqueeze(0)


def _mask_tensor(mask_gray: np.ndarray, T: int, device) -> "torch.Tensor":
    """Single mask → [1, T, 1, H, W] float32 (1 = hole to fill)."""
    import torch
    binary = (mask_gray > 127).astype(np.float32)          # [H, W]
    t = torch.from_numpy(binary)[None, None, None, :, :]   # [1, 1, 1, H, W]
    return t.expand(1, T, 1, *binary.shape).to(device)     # [1, T, 1, H, W]


def _frame_to_bgr(t_chw: "torch.Tensor") -> np.ndarray:
    """Single frame [C, H, W] in [-1, 1] → BGR uint8 numpy array."""
    import torch
    rgb = ((t_chw.clamp(-1, 1) + 1.0) / 2.0 * 255).to(dtype=torch.uint8)
    return cv2.cvtColor(rgb.permute(1, 2, 0).cpu().numpy(), cv2.COLOR_RGB2BGR)


# ── Sliding-window inference ──────────────────────────────────────────────────

def _infer_sliding_window(
    model,
    frames_b: "torch.Tensor",    # [1, T, C, H, W] in [-1, 1]
    masks_b:  "torch.Tensor",    # [1, T, 1, H, W]  0/1
    job_id: str,
    status: dict,
) -> "torch.Tensor":
    """
    Process all T frames with E2FGVI's sliding-window inference.

    For each frame i:
      - Gather NEIGHBOR_LEN local frames centred on i.
      - Gather sparse reference frames at REF_STRIDE spacing.
      - Zero out masked regions in the gathered frames.
      - Call model.forward and extract the prediction for frame i.

    Falls back to the original (LaMa) frame if the model call fails for
    a specific frame — ensures the job never crashes mid-stream.

    Returns [1, T, C, H, W] completed frames tensor.
    """
    import torch

    T = frames_b.shape[1]
    completed = frames_b.clone()

    ref_ids = list(range(0, T, REF_STRIDE))
    t_start = time.monotonic()

    for i in range(T):
        half = NEIGHBOR_LEN // 2
        local_ids  = list(range(max(0, i - half), min(T, i + half + 1)))
        extra_refs = [r for r in ref_ids if r not in local_ids]
        all_ids    = local_ids + extra_refs
        center_idx = local_ids.index(i)

        sel_f  = frames_b[:, all_ids]           # [1, N, C, H, W]
        sel_m  = masks_b[:, all_ids]            # [1, N, 1, H, W]
        masked = sel_f * (1.0 - sel_m)          # zero out hole regions

        try:
            with torch.no_grad():
                pred, _ = model(masked, sel_m)
            # pred shape is [1, N, C, H, W] or [1, C, H, W] depending on version
            if pred.dim() == 5:
                completed[0, i] = pred[0, center_idx]
            elif pred.dim() == 4:
                completed[0, i] = pred[0]
            else:
                raise ValueError(f"Unexpected pred shape: {pred.shape}")
        except Exception as exc:
            print(f"[e2fgvi] frame {i} forward error — keeping LaMa output: {exc}")

        # Progress: 5%–95% envelope for the inference phase
        if i % 5 == 0 or i == T - 1:
            elapsed   = time.monotonic() - t_start
            rate      = (i + 1) / elapsed if elapsed > 0 else 0.0
            remaining = (T - i - 1) / rate if rate > 0 else None
            status.update({
                "frames_done":       i + 1,
                "frames_total":      T,
                "progress":          0.05 + 0.90 * (i + 1) / T,
                "estimated_seconds": int(remaining) if remaining is not None else None,
            })
            _write_status(job_id, status)

    return completed


# ── Video reassembly ──────────────────────────────────────────────────────────

def _reassemble_video(
    frames_bgr: List[np.ndarray],
    output_path: Path,
    fps: float,
) -> None:
    """
    Write BGR frame list to a H.264 MP4 at fps.
    Uses cv2.VideoWriter for the raw write, then re-encodes with ffmpeg
    for broad playback compatibility (yuv420p, faststart).
    """
    if not frames_bgr:
        raise RuntimeError("No frames to reassemble")

    H, W = frames_bgr[0].shape[:2]
    output_path.parent.mkdir(parents=True, exist_ok=True)

    raw_path = output_path.with_suffix(".raw.mp4")
    fourcc   = cv2.VideoWriter_fourcc(*"mp4v")
    writer   = cv2.VideoWriter(str(raw_path), fourcc, fps, (W, H))
    try:
        for f in frames_bgr:
            writer.write(f)
    finally:
        writer.release()

    cmd = [
        "ffmpeg", "-y",
        "-i", str(raw_path),
        "-c:v", "libx264", "-crf", "18",
        "-pix_fmt", "yuv420p",
        "-preset", "fast",
        "-movflags", "+faststart",
        str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    raw_path.unlink(missing_ok=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"ffmpeg re-encode failed:\n{result.stderr[-3000:]}"
        )


# ── Public API ─────────────────────────────────────────────────────────────────

def run_e2fgvi_job(
    job_id:      str,
    frames_dir:  "str | Path",
    mask_b64:    str,
    fps:         float,
    output_path: "Optional[str | Path]" = None,
) -> str:
    """
    Apply E2FGVI temporal consistency to a directory of inpainted PNG frames.

    Args:
        job_id:      Shared job ID — updates the same status file as
                     lama_worker so app.py polling works transparently.
        frames_dir:  Directory of PNG frames produced by lama_worker
                     (output/inpaint_temp/<job_id>/inpainted/).
        mask_b64:    Base64-encoded mask PNG (white = region that was inpainted).
        fps:         Frame rate for the output video.
        output_path: Where to write the final MP4.
                     Defaults to output/inpainted/<job_id>_e2fgvi.mp4.

    Returns:
        Absolute path to the output video as a string.

    Raises:
        RuntimeError on failure.  Status JSON is updated with
        {"status": "failed", "error": ...} before raising.
    """
    frames_dir = Path(frames_dir)
    if output_path is None:
        INPAINT_OUT_DIR.mkdir(parents=True, exist_ok=True)
        output_path = INPAINT_OUT_DIR / f"{job_id}_e2fgvi.mp4"
    output_path = Path(output_path)

    # Load frames first — needed even for the degraded (no E2FGVI) path.
    frames_bgr = _load_frames(frames_dir)
    T          = len(frames_bgr)
    H, W       = frames_bgr[0].shape[:2]

    print(
        f"[e2fgvi] {job_id[:8]}  {T} frames  {W}x{H}"
        f"  fps={fps:.3f}  output={output_path.name}"
    )

    status = _initial_status(T)
    _write_status(job_id, status)

    # ── Graceful degradation ───────────────────────────────────────────────────
    if not _is_available():
        print(
            f"[e2fgvi] {job_id[:8]}  E2FGVI not available "
            f"(dir_exists={E2FGVI_DIR.exists()}  ckpt={_find_checkpoint()}). "
            f"Assembling LaMa frames without temporal pass."
        )
        _reassemble_video(frames_bgr, output_path, fps)
        status.update({"status": "done", "progress": 1.0,
                        "frames_done": T, "output_path": str(output_path)})
        _write_status(job_id, status)
        return str(output_path)

    # ── Full E2FGVI path ───────────────────────────────────────────────────────
    import torch

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"[e2fgvi] {job_id[:8]}  device={device}")

    try:
        # 1. Decode mask
        status.update({"progress": 0.02})
        _write_status(job_id, status)
        mask_gray = _decode_mask(mask_b64, H, W)
        print(
            f"[e2fgvi] {job_id[:8]}  mask: "
            f"{int(np.sum(mask_gray > 127))} hole pixels "
            f"({100.0 * np.sum(mask_gray > 127) / (H * W):.1f}% of frame)"
        )

        # 2. Load model
        status.update({"progress": 0.03})
        _write_status(job_id, status)
        model = _load_model(device)

        # 3. Build tensors
        frames_b = _to_tensor(frames_bgr, device)   # [1, T, C, H, W]
        masks_b  = _mask_tensor(mask_gray, T, device)  # [1, T, 1, H, W]
        status.update({"progress": 0.05})
        _write_status(job_id, status)

        # 4. Sliding-window E2FGVI inference
        t_infer = time.monotonic()
        completed_b = _infer_sliding_window(model, frames_b, masks_b, job_id, status)
        print(
            f"[e2fgvi] {job_id[:8]}  inference done — "
            f"{T} frames in {time.monotonic() - t_infer:.1f}s"
        )

        # 5. Free VRAM before writing
        del model, frames_b, masks_b
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

        # 6. Convert to BGR
        status.update({"progress": 0.96})
        _write_status(job_id, status)
        completed_bgr = [_frame_to_bgr(completed_b[0, i]) for i in range(T)]

        # 7. Assemble video
        print(f"[e2fgvi] {job_id[:8]}  reassembling at {fps:.3f} fps...")
        _reassemble_video(completed_bgr, output_path, fps)

        if not output_path.exists():
            raise RuntimeError(f"Reassembly produced no output at {output_path}")

        status.update({
            "status":            "done",
            "progress":          1.0,
            "estimated_seconds": None,
            "output_path":       str(output_path),
        })
        _write_status(job_id, status)
        print(f"[e2fgvi] {job_id[:8]}  complete — output: {output_path}")
        return str(output_path)

    except Exception as exc:
        error_msg = str(exc)
        print(f"[e2fgvi] {job_id[:8]}  FAILED: {error_msg}")
        status.update({"status": "failed", "error": error_msg})
        _write_status(job_id, status)
        raise RuntimeError(error_msg) from exc


# ── CLI entry point (for testing without Flask) ───────────────────────────────

def _cli() -> None:
    parser = argparse.ArgumentParser(
        description="Run E2FGVI temporal consistency pass on LaMa-inpainted frames"
    )
    g = parser.add_mutually_exclusive_group(required=True)
    g.add_argument("--mask",    help="Path to mask PNG file (white = hole)")
    g.add_argument("--mask_b64", help="Base64-encoded mask PNG string")

    parser.add_argument("--frames_dir", required=True,
                        help="Directory of inpainted PNG frames")
    parser.add_argument("--fps",    type=float, default=30.0)
    parser.add_argument("--output", default=None, help="Output video path")
    parser.add_argument("--job_id", default="e2fgvi-cli-test")
    args = parser.parse_args()

    if args.mask:
        with open(args.mask, "rb") as fh:
            mask_b64 = base64.b64encode(fh.read()).decode()
    else:
        mask_b64 = args.mask_b64

    output = run_e2fgvi_job(
        job_id=args.job_id,
        frames_dir=args.frames_dir,
        mask_b64=mask_b64,
        fps=args.fps,
        output_path=args.output,
    )
    print(f"\nOutput: {output}")


if __name__ == "__main__":
    _cli()
