"""
sam_helper.py
-------------
SAM ViT-B subject isolation helper.

Loads SAM ViT-B (fits in 4GB VRAM / runs on CPU).
Given a video path, timestamp, and a (point_x, point_y) prompt
as fractions of frame dimensions (0.0–1.0), returns a binary mask
as a base64-encoded PNG (same dimensions as the source frame).

Model checkpoint: automatically downloaded to style/sam_vit_b.pth
on first use (~375MB).

Environment:
  SAM_DEVICE   override device ("cuda"/"cpu"), default auto
"""

from __future__ import annotations

import base64
import os
import urllib.request
from pathlib import Path

import cv2
import numpy as np

BASE_DIR       = Path(__file__).resolve().parent.parent
SAM_CHECKPOINT = Path(
    os.environ.get("SAM_CHECKPOINT", str(BASE_DIR / "style" / "sam_vit_b.pth"))
)
SAM_MODEL_TYPE = "vit_b"
SAM_DOWNLOAD_URL = (
    "https://dl.fbaipublicfiles.com/segment_anything/sam_vit_b_01ec64.pth"
)

# Module-level predictor cache — loaded once, reused across requests
_predictor = None


def _get_predictor():
    global _predictor
    if _predictor is not None:
        return _predictor

    # Auto-download checkpoint on first use
    if not SAM_CHECKPOINT.exists():
        print(f"[sam] Downloading SAM ViT-B checkpoint (~375 MB)…")
        SAM_CHECKPOINT.parent.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(SAM_DOWNLOAD_URL, str(SAM_CHECKPOINT))
        print(f"[sam] Saved to {SAM_CHECKPOINT}")

    try:
        import torch
        from segment_anything import sam_model_registry, SamPredictor
    except ImportError as exc:
        raise ImportError(
            "segment_anything is not installed. "
            "Run: pip install segment-anything"
        ) from exc

    device_env = os.environ.get("SAM_DEVICE", "").strip()
    if device_env:
        device = device_env
    else:
        import torch
        device = "cuda" if torch.cuda.is_available() else "cpu"

    print(f"[sam] Loading SAM ViT-B on {device}…")
    sam = sam_model_registry[SAM_MODEL_TYPE](checkpoint=str(SAM_CHECKPOINT))
    sam.to(device=device)
    _predictor = SamPredictor(sam)
    print("[sam] Ready")
    return _predictor


def run_sam(
    video_path: str,
    timestamp: float,
    point_x: float,
    point_y: float,
) -> tuple[str, int, int]:
    """
    Run SAM ViT-B on a single frame of a video.

    Args:
        video_path: path to video file (absolute or filename in raw_clips/)
        timestamp:  time in seconds of the frame to analyse
        point_x:    subject click X as fraction 0.0–1.0 of frame width
        point_y:    subject click Y as fraction 0.0–1.0 of frame height

    Returns:
        (mask_b64, width, height)
        mask_b64 — base64-encoded PNG mask at source frame resolution
                   white pixels = subject, black = background
    """
    # Extract the frame
    cap = cv2.VideoCapture(str(video_path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    frame_idx = int(timestamp * fps)
    cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
    ret, frame = cap.read()
    cap.release()

    if not ret:
        raise ValueError(
            f"Could not extract frame at {timestamp:.2f}s from {video_path}"
        )

    h, w = frame.shape[:2]
    frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

    # Convert fractional point to pixel coordinates
    px = int(point_x * w)
    py = int(point_y * h)

    predictor = _get_predictor()
    predictor.set_image(frame_rgb)

    masks, scores, _ = predictor.predict(
        point_coords=np.array([[px, py]]),
        point_labels=np.array([1]),   # positive prompt
        multimask_output=True,
    )

    # Select the largest mask (by pixel area)
    best_mask = masks[int(np.argmax([m.sum() for m in masks]))]

    # Convert to uint8 grayscale PNG (white = subject, black = background)
    mask_img = (best_mask.astype(np.uint8) * 255)
    success, buf = cv2.imencode(".png", mask_img)
    if not success:
        raise RuntimeError("Failed to encode SAM mask as PNG")

    mask_b64 = base64.b64encode(buf.tobytes()).decode("utf-8")
    return mask_b64, w, h
