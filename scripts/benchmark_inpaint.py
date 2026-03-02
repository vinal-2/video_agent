#!/usr/bin/env python3
"""
benchmark_inpaint.py
--------------------
Test all three inpainting engines on the same single frame + mask and output
a side-by-side comparison image.

Output layout (horizontal):
  Original | LaMa | LaMa + E2FGVI | ProPainter (Legacy)

Each panel has a label bar showing the engine name and inference time in ms.
Engines that fail or are skipped show an "N/A" error panel instead of crashing.

Usage:
    python3 scripts/benchmark_inpaint.py
    python3 scripts/benchmark_inpaint.py --frame /tmp/frame.jpg --mask /tmp/mask.png
    python3 scripts/benchmark_inpaint.py --skip_propainter
    python3 scripts/benchmark_inpaint.py --device cpu
"""
from __future__ import annotations

import argparse
import base64
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import List, Optional, Tuple

import cv2
import numpy as np

# ── Paths ──────────────────────────────────────────────────────────────────────

# Add repo root to sys.path so "from scripts.xxx import" resolves correctly
# whether this script is run as "python3 scripts/benchmark_inpaint.py" or
# "python -m scripts.benchmark_inpaint".
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

DEFAULT_FRAME  = "/tmp/test_frame.jpg"
DEFAULT_MASK   = "/tmp/test_mask.png"
DEFAULT_OUTPUT = "/tmp/benchmark_comparison.jpg"

LABEL_H   = 50   # height of label bar below each panel (pixels)
PANEL_GAP = 6    # gap between panels (pixels)
OUTER_PAD = 12   # outer border padding (pixels)

# ── Type alias ────────────────────────────────────────────────────────────────

# (display_label, result_frame_bgr | None, elapsed_ms | None, error_msg | None)
EngineResult = Tuple[str, Optional[np.ndarray], Optional[float], Optional[str]]


# ── Input helpers ─────────────────────────────────────────────────────────────

def load_frame(path: str) -> np.ndarray:
    img = cv2.imread(path)
    if img is None:
        raise FileNotFoundError(f"Cannot load frame: {path}")
    return img


def load_mask(path: str, target_h: int, target_w: int) -> np.ndarray:
    """Load mask PNG → uint8 (0 or 255), resized to (target_h, target_w)."""
    img = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise FileNotFoundError(f"Cannot load mask: {path}")
    if img.shape != (target_h, target_w):
        img = cv2.resize(img, (target_w, target_h),
                         interpolation=cv2.INTER_NEAREST)
    return (img > 127).astype(np.uint8) * 255


def mask_to_b64(mask: np.ndarray) -> str:
    ok, buf = cv2.imencode(".png", mask)
    if not ok:
        raise RuntimeError("cv2.imencode failed for mask PNG")
    return base64.b64encode(buf.tobytes()).decode()


# ── Panel rendering ───────────────────────────────────────────────────────────

def _label_bar(w: int, label: str, timing: Optional[str],
               accent: Tuple[int, int, int] = (120, 255, 100)) -> np.ndarray:
    """Return a LABEL_H × w BGR numpy array with engine label + optional timing."""
    bar = np.full((LABEL_H, w, 3), 18, dtype=np.uint8)

    # Engine name
    cv2.putText(
        bar, label, (8, 22),
        cv2.FONT_HERSHEY_SIMPLEX, 0.58, accent, 1, cv2.LINE_AA,
    )
    # Timing (smaller, grey)
    if timing:
        cv2.putText(
            bar, timing, (8, 40),
            cv2.FONT_HERSHEY_SIMPLEX, 0.44, (130, 130, 130), 1, cv2.LINE_AA,
        )
    return bar


def _error_panel(h: int, w: int, message: str) -> np.ndarray:
    """Dark panel with red centred error text."""
    panel = np.full((h, w, 3), 28, dtype=np.uint8)
    lines = (message or "ERROR").splitlines()
    line_h = 22
    y_start = max(10, h // 2 - len(lines) * line_h // 2)
    for i, line in enumerate(lines):
        text = line[:40]  # truncate long messages
        (tw, _), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.50, 1)
        x = max(4, (w - tw) // 2)
        cv2.putText(
            panel, text, (x, y_start + i * line_h),
            cv2.FONT_HERSHEY_SIMPLEX, 0.50, (70, 70, 210), 1, cv2.LINE_AA,
        )
    return panel


def make_panel(frame_or_none: Optional[np.ndarray],
               label: str,
               elapsed_ms: Optional[float],
               error: Optional[str],
               target_h: int,
               target_w: int,
               is_original: bool = False) -> np.ndarray:
    """Assemble a panel: image + label bar."""
    accent = (200, 170, 255) if is_original else (120, 255, 100)

    if error and not is_original:
        img = _error_panel(target_h, target_w, error)
    elif frame_or_none is not None:
        img = frame_or_none
        # Resize if the result frame differs (e.g. ProPainter may change dims)
        if img.shape[:2] != (target_h, target_w):
            img = cv2.resize(img, (target_w, target_h))
    else:
        img = _error_panel(target_h, target_w, "No output")

    timing_str = f"{elapsed_ms:,.0f} ms" if elapsed_ms is not None else None
    bar = _label_bar(target_w, label, timing_str, accent=accent)
    return np.vstack([img, bar])


# ── Synthetic video helper (ProPainter needs a video file) ────────────────────

def _write_synthetic_video(frame_bgr: np.ndarray, out_path: str,
                            n_frames: int = 5, fps: float = 30.0) -> None:
    """Write N identical frames as a short H.264 MP4 using ffmpeg."""
    with tempfile.TemporaryDirectory() as td:
        for i in range(n_frames):
            cv2.imwrite(str(Path(td) / f"{i:06d}.png"), frame_bgr)
        cmd = [
            "ffmpeg", "-y",
            "-framerate", str(fps),
            "-i", str(Path(td) / "%06d.png"),
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "-crf", "18",
            "-preset", "fast",
            out_path,
        ]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(
                f"ffmpeg synthetic video failed:\n{r.stderr[-800:]}"
            )


def _extract_video_frame(video_path: str, frame_idx: int = 2) -> np.ndarray:
    """Extract a single frame (0-indexed) from a video file."""
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open video: {video_path}")
    cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
    ok, frame = cap.read()
    cap.release()
    if not ok:
        raise RuntimeError(
            f"Could not read frame {frame_idx} from {video_path}"
        )
    return frame


# ── Engine runners ────────────────────────────────────────────────────────────

def bench_lama(frame: np.ndarray, mask: np.ndarray,
               device: str) -> EngineResult:
    """Direct _load_lama + _run_lama_on_frame — no video overhead."""
    try:
        from scripts.lama_worker import _load_lama, _run_lama_on_frame  # type: ignore

        print("[benchmark] LaMa: loading model …")
        model, cfg = _load_lama(device)

        print("[benchmark] LaMa: running inference …")
        t0 = time.perf_counter()
        result = _run_lama_on_frame(model, cfg, frame, mask)
        elapsed = (time.perf_counter() - t0) * 1000.0

        print(f"[benchmark] LaMa done — {elapsed:,.0f} ms")
        return ("LaMa", result, elapsed, None)
    except Exception as exc:
        print(f"[benchmark] LaMa FAILED: {exc}")
        return ("LaMa", None, None, type(exc).__name__ + ": " + str(exc)[:80])


def bench_lama_e2fgvi(frame: np.ndarray, mask: np.ndarray,
                      device: str) -> EngineResult:
    """
    LaMa single-frame pass → 3-frame E2FGVI temporal pass → centre frame.
    The 3-frame padding gives E2FGVI a minimal temporal window to work with.
    """
    try:
        import torch
        from scripts.lama_worker   import _load_lama, _run_lama_on_frame   # type: ignore
        from scripts.e2fgvi_worker import (                                 # type: ignore
            _is_available,
            _load_model   as e2_load_model,
            _to_tensor,
            _mask_tensor,
            _frame_to_bgr,
            _infer_sliding_window,
        )

        if not _is_available():
            return (
                "LaMa + E2FGVI", None, None,
                "E2FGVI not available\n(missing dir, weights, or torch)",
            )

        print("[benchmark] LaMa+E2FGVI: loading LaMa …")
        lama_model, lama_cfg = _load_lama(device)
        print("[benchmark] LaMa+E2FGVI: loading E2FGVI …")
        e2_dev   = torch.device(device)
        e2_model = e2_load_model(e2_dev)

        print("[benchmark] LaMa+E2FGVI: running LaMa pass …")
        t0 = time.perf_counter()
        lama_frame = _run_lama_on_frame(lama_model, lama_cfg, frame, mask)

        print("[benchmark] LaMa+E2FGVI: running E2FGVI pass (3 padded frames) …")
        # Pad to 3 frames so E2FGVI has a minimal sliding window
        frames_3  = [lama_frame, lama_frame, lama_frame]
        frames_t  = _to_tensor(frames_3, e2_dev)
        masks_t   = _mask_tensor(mask, T=3, device=e2_dev)
        dummy_status: dict = {
            "frames_done": 0, "frames_total": 3,
            "progress": 0.0, "estimated_seconds": None,
        }
        completed = _infer_sliding_window(
            e2_model, frames_t, masks_t,
            job_id="benchmark-e2fgvi", status=dummy_status,
        )
        # Extract centre frame (index 1)
        result  = _frame_to_bgr(completed[0, 1])
        elapsed = (time.perf_counter() - t0) * 1000.0

        print(f"[benchmark] LaMa+E2FGVI done — {elapsed:,.0f} ms")
        return ("LaMa + E2FGVI", result, elapsed, None)
    except Exception as exc:
        print(f"[benchmark] LaMa+E2FGVI FAILED: {exc}")
        return ("LaMa + E2FGVI", None, None,
                type(exc).__name__ + ": " + str(exc)[:80])


def bench_propainter(frame: np.ndarray, mask: np.ndarray) -> EngineResult:
    """
    Build a synthetic 5-frame video → run_inpaint_job → extract centre frame.
    Cleans up temp files on exit.
    """
    job_id   = f"bench-pp-{uuid.uuid4().hex[:8]}"
    mask_b64 = mask_to_b64(mask)

    try:
        from scripts.inpaint_worker import run_inpaint_job  # type: ignore

        with tempfile.TemporaryDirectory() as td:
            video_in = str(Path(td) / "bench_input.mp4")
            print("[benchmark] ProPainter: building synthetic 5-frame video …")
            _write_synthetic_video(frame, video_in, n_frames=5, fps=30.0)

            # 5 frames @ 30 fps = 0.167 s duration → inpaint 0.0–0.167 s
            duration = 5 / 30.0

            print("[benchmark] ProPainter: running inpaint job (this is slow) …")
            t0 = time.perf_counter()
            output_path = run_inpaint_job(job_id, video_in, mask_b64,
                                          0.0, duration)
            elapsed = (time.perf_counter() - t0) * 1000.0

            print("[benchmark] ProPainter: extracting centre frame …")
            result = _extract_video_frame(output_path, frame_idx=2)

        print(f"[benchmark] ProPainter done — {elapsed:,.0f} ms")
        return ("ProPainter", result, elapsed, None)
    except Exception as exc:
        print(f"[benchmark] ProPainter FAILED: {exc}")
        return ("ProPainter", None, None,
                type(exc).__name__ + ": " + str(exc)[:80])


# ── Composition ───────────────────────────────────────────────────────────────

def compose_and_save(
    original: np.ndarray,
    results: List[EngineResult],
    output_path: str,
) -> None:
    h, w = original.shape[:2]

    # Build panels list: Original first, then each engine result
    panels = [
        make_panel(original, "Original", None, None, h, w, is_original=True)
    ]
    for label, result_frame, elapsed_ms, error in results:
        panels.append(make_panel(result_frame, label, elapsed_ms, error, h, w))

    # Horizontal concatenation with gaps
    gap = np.full((h + LABEL_H, PANEL_GAP, 3), 10, dtype=np.uint8)
    row = panels[0]
    for p in panels[1:]:
        row = np.hstack([row, gap, p])

    # Outer padding
    top_bot = np.full((OUTER_PAD, row.shape[1], 3), 10, dtype=np.uint8)
    sides   = np.full((row.shape[0] + 2 * OUTER_PAD, OUTER_PAD, 3), 10,
                      dtype=np.uint8)
    final   = np.hstack([sides,
                         np.vstack([top_bot, row, top_bot]),
                         sides])

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(output_path, final, [cv2.IMWRITE_JPEG_QUALITY, 92])
    print(f"\n[benchmark] Saved comparison: {output_path}")
    print(f"            Size: {final.shape[1]}×{final.shape[0]} px")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(
        description="Benchmark LaMa / LaMa+E2FGVI / ProPainter on one frame.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--frame",           default=DEFAULT_FRAME,
                    help="Input frame JPEG  (default: %(default)s)")
    ap.add_argument("--mask",            default=DEFAULT_MASK,
                    help="Input mask PNG    (default: %(default)s)")
    ap.add_argument("--output",          default=DEFAULT_OUTPUT,
                    help="Output JPEG path (default: %(default)s)")
    ap.add_argument("--skip_propainter", action="store_true",
                    help="Skip ProPainter (~25 min/clip) for faster iteration")
    ap.add_argument("--device",          default="cuda",
                    help="PyTorch device for LaMa + E2FGVI  (default: %(default)s)")
    args = ap.parse_args()

    # ── Load inputs ────────────────────────────────────────────────────────────
    print(f"[benchmark] Frame : {args.frame}")
    print(f"[benchmark] Mask  : {args.mask}")
    print(f"[benchmark] Output: {args.output}")
    print(f"[benchmark] Device: {args.device}")
    print()

    frame = load_frame(args.frame)
    h, w  = frame.shape[:2]
    print(f"[benchmark] Frame size: {w}×{h} px")

    mask = load_mask(args.mask, h, w)
    nonzero = int(np.count_nonzero(mask))
    print(f"[benchmark] Mask  size: {w}×{h} px  ({nonzero} inpaint pixels, "
          f"{100.0*nonzero/(h*w):.1f}%)")
    print()

    # ── Run engines ────────────────────────────────────────────────────────────
    results: List[EngineResult] = []

    results.append(bench_lama(frame, mask, args.device))
    print()

    results.append(bench_lama_e2fgvi(frame, mask, args.device))
    print()

    if args.skip_propainter:
        print("[benchmark] ProPainter: skipped (--skip_propainter)")
        results.append(("ProPainter", None, None, "Skipped\n(--skip_propainter)"))
    else:
        results.append(bench_propainter(frame, mask))
    print()

    # ── Summary table ──────────────────────────────────────────────────────────
    print("  Engine           Result     Time")
    print("  ─────────────────────────────────────")
    for label, _, elapsed_ms, error in results:
        status = f"{elapsed_ms:>10,.0f} ms" if elapsed_ms is not None else "     FAILED"
        note   = ""
        if error:
            note = f"  ({error[:30]})"
        print(f"  {label:<20} {'OK' if not error else 'FAIL':<8} {status}{note}")
    print()

    # ── Compose output ─────────────────────────────────────────────────────────
    compose_and_save(frame, results, args.output)


if __name__ == "__main__":
    main()
