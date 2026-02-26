"""
inpaint_worker.py
-----------------
ProPainter wrapper for the Video Agent pipeline.

Calls ProPainter's inference_propainter.py as a subprocess.
Writes progress to a JSON status file so Flask can poll it.
Returns path to the inpainted output clip on success.

ProPainter location: D:\\video-agent\\ProPainter\\
Weights location:    D:\\video-agent\\ProPainter\\weights\\

Usage (CLI):
  python -m scripts.inpaint_worker \\
    --video  path/to/source_video.mp4 \\
    --mask   path/to/mask.png \\
    --start  0.0 \\
    --end    3.5 \\
    --job_id abc123

Status file: output/inpaint_jobs/<job_id>.json
  { "status": "running"|"done"|"failed",
    "progress": 0.0-1.0,
    "frames_done": int,
    "frames_total": int,
    "estimated_seconds": int | null,
    "output_path": str | null,
    "error": str | null }
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

import cv2
import numpy as np

BASE_DIR           = Path(__file__).resolve().parent.parent
PROPAINTER_DIR     = BASE_DIR / "ProPainter"
PROPAINTER_SCRIPT  = PROPAINTER_DIR / "inference_propainter.py"
PROPAINTER_WEIGHTS = PROPAINTER_DIR / "weights"

INPAINT_JOBS_DIR  = BASE_DIR / "output" / "inpaint_jobs"
INPAINT_TEMP_DIR  = BASE_DIR / "output" / "inpaint_temp"
INPAINT_OUT_DIR   = BASE_DIR / "output" / "inpainted"

# Matches tqdm lines: "  8%|█    | 4/47 [00:00<00:05, 7.86it/s]"
_TQDM_RE = re.compile(r'(\d+)%\|[^\|]*\|\s*(\d+)/(\d+)')


def _write_status(job_id: str, status: dict) -> None:
    INPAINT_JOBS_DIR.mkdir(parents=True, exist_ok=True)
    (INPAINT_JOBS_DIR / f"{job_id}.json").write_text(json.dumps(status))


def read_status(job_id: str) -> dict | None:
    """Read the status JSON for a job. Returns None if not found."""
    path = INPAINT_JOBS_DIR / f"{job_id}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def run_inpaint_job(
    job_id: str,
    video_path: str,
    mask_b64: str,
    start: float,
    end: float,
) -> str:
    """
    Run a ProPainter inpaint job synchronously.
    Writes progress to output/inpaint_jobs/<job_id>.json throughout.
    Returns the output video path on success.
    Raises on failure (status JSON is updated with error first).
    """
    status: dict = {
        "status": "running",
        "progress": 0.0,
        "frames_done": 0,
        "frames_total": 0,
        "estimated_seconds": None,
        "output_path": None,
        "error": None,
    }
    _write_status(job_id, status)

    if not PROPAINTER_SCRIPT.exists():
        msg = f"ProPainter not found at {PROPAINTER_DIR}"
        status.update({"status": "failed", "error": msg})
        _write_status(job_id, status)
        raise FileNotFoundError(msg)

    temp_dir      = INPAINT_TEMP_DIR / job_id
    segment_video = temp_dir / "segment.mp4"
    mask_png      = temp_dir / "mask.png"
    output_dir    = temp_dir / "out"

    temp_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        # ── 1. Extract segment as a short temp video ──────────────────────────
        duration = max(end - start, 0.1)
        ffmpeg_extract = [
            "ffmpeg", "-y",
            "-ss", str(start),
            "-t",  str(duration),
            "-i",  str(video_path),
            "-c",  "copy",
            str(segment_video),
        ]
        result = subprocess.run(ffmpeg_extract, capture_output=True)
        if result.returncode != 0:
            raise RuntimeError(
                f"ffmpeg segment extract failed: {result.stderr.decode('utf-8', errors='replace')[:500]}"
            )

        # ── 2. Decode and save mask PNG ────────────────────────────────────────
        mask_data = base64.b64decode(mask_b64)
        mask_arr  = np.frombuffer(mask_data, dtype=np.uint8)
        mask_img  = cv2.imdecode(mask_arr, cv2.IMREAD_GRAYSCALE)
        if mask_img is None:
            raise ValueError("Failed to decode mask PNG from base64")
        cv2.imwrite(str(mask_png), mask_img)

        # ── 3. Run ProPainter ──────────────────────────────────────────────────
        # --fp16 is silently ignored on CPU (ProPainter checks device type).
        # No --cpu flag exists; device is auto-selected by ProPainter.
        propainter_cmd = [
            sys.executable,
            str(PROPAINTER_SCRIPT),
            "--video",  str(segment_video),
            "--mask",   str(mask_png),
            "--output", str(output_dir),
            "--width",  "640",
            "--height", "1138",
            "--fp16",
        ]

        start_time = time.time()
        proc = subprocess.Popen(
            propainter_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            cwd=str(PROPAINTER_DIR),
            encoding="utf-8",
            errors="replace",
        )

        for line in proc.stdout:  # type: ignore[union-attr]
            line = line.strip()
            if not line:
                continue
            print(f"[inpaint/{job_id[:8]}] {line}", flush=True)
            m = _TQDM_RE.search(line)
            if m:
                pct   = int(m.group(1))
                done  = int(m.group(2))
                total = int(m.group(3))
                elapsed   = time.time() - start_time
                rate      = done / max(elapsed, 0.01)
                remaining = int((total - done) / max(rate, 0.001))
                status.update({
                    "progress":           pct / 100,
                    "frames_done":        done,
                    "frames_total":       total,
                    "estimated_seconds":  remaining,
                })
                _write_status(job_id, status)

        proc.wait()
        if proc.returncode != 0:
            raise RuntimeError(f"ProPainter exited with code {proc.returncode}")

        # ── 4. Locate inpaint_out.mp4 ─────────────────────────────────────────
        expected = output_dir / segment_video.stem / "inpaint_out.mp4"
        if not expected.exists():
            found = list(output_dir.rglob("inpaint_out.mp4"))
            if not found:
                raise FileNotFoundError(
                    f"inpaint_out.mp4 not found anywhere under {output_dir}"
                )
            expected = found[0]

        # ── 5. Move to permanent location ─────────────────────────────────────
        INPAINT_OUT_DIR.mkdir(parents=True, exist_ok=True)
        final_path = INPAINT_OUT_DIR / f"{job_id}.mp4"
        shutil.move(str(expected), str(final_path))

        status.update({
            "status":      "done",
            "progress":    1.0,
            "output_path": str(final_path),
        })
        _write_status(job_id, status)
        return str(final_path)

    except Exception as exc:
        status.update({"status": "failed", "error": str(exc)})
        _write_status(job_id, status)
        raise

    finally:
        # Clean up temp directory only on success to aid debugging on failure
        if status.get("status") == "done":
            shutil.rmtree(temp_dir, ignore_errors=True)


# ── CLI entry point ────────────────────────────────────────────────────────────

def _main() -> None:
    parser = argparse.ArgumentParser(
        description="ProPainter inpaint worker — run a single inpaint job",
    )
    parser.add_argument("--video",  required=True,  help="Path to the source video file")
    parser.add_argument("--mask",   required=True,  help="Path to the mask PNG (white=remove)")
    parser.add_argument("--start",  type=float, default=0.0, help="Segment start (seconds)")
    parser.add_argument("--end",    type=float, required=True, help="Segment end (seconds)")
    parser.add_argument("--job_id", default=None, help="Job ID (auto-generated if omitted)")
    args = parser.parse_args()

    job_id = args.job_id or str(uuid.uuid4())

    # Mask arg: accept a file path (encodes it) or a raw base64 string
    mask_path = Path(args.mask)
    if mask_path.exists():
        mask_b64 = base64.b64encode(mask_path.read_bytes()).decode()
    else:
        mask_b64 = args.mask  # assume it's already base64

    print(f"[inpaint] Starting job {job_id}")
    try:
        out = run_inpaint_job(job_id, args.video, mask_b64, args.start, args.end)
        print(f"[inpaint] Done — output: {out}")
    except Exception as exc:
        print(f"[inpaint] FAILED: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    _main()
