"""
smart_crop.py
-------------
Subject-tracking crop path computation.

Given a video path + time range, detects the dominant subject centroid
per frame (face cascade → body cascade → frame centre fallback),
smooths the crop X position with a rolling average to prevent jitter,
and returns a single representative X offset for the segment midframe.

compute_auto_crop() returns the best static X offset for the whole segment
(used when the user has not manually set a crop).

Output is always: { x: int, y: int, w: int, h: int }
where w = floor(source_height * 9/16), rounded down to nearest even number.
h = source_height, y = 0.  Only X is variable.
"""

from __future__ import annotations

import math
import cv2
import numpy as np
from pathlib import Path
from typing import TypedDict

# Haarcascade paths bundled with OpenCV
_FACE_CASCADE_PATH = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
_BODY_CASCADE_PATH = cv2.data.haarcascades + "haarcascade_upperbody.xml"

_face_cascade: cv2.CascadeClassifier | None = None
_body_cascade: cv2.CascadeClassifier | None = None


def _get_cascades() -> tuple[cv2.CascadeClassifier, cv2.CascadeClassifier]:
    global _face_cascade, _body_cascade
    if _face_cascade is None:
        _face_cascade = cv2.CascadeClassifier(_FACE_CASCADE_PATH)
    if _body_cascade is None:
        _body_cascade = cv2.CascadeClassifier(_BODY_CASCADE_PATH)
    return _face_cascade, _body_cascade


class CropResult(TypedDict):
    x: int
    y: int
    w: int
    h: int
    source_w: int
    source_h: int


def _crop_width(source_h: int) -> int:
    """9:16 crop width, rounded down to nearest even number."""
    return int(math.floor(source_h * 9 / 16 / 2) * 2)


def _clamp_x(x: int, crop_w: int, source_w: int) -> int:
    return max(0, min(x, source_w - crop_w))


def _detect_subject_x(frame_gray: np.ndarray, source_w: int) -> int:
    """Return the X centroid of the dominant subject, or source_w//2 as fallback."""
    face_cas, body_cas = _get_cascades()

    faces = face_cas.detectMultiScale(frame_gray, scaleFactor=1.1, minNeighbors=4, minSize=(40, 40))
    if len(faces):
        # Use the largest face
        x, y, w, h = max(faces, key=lambda f: f[2] * f[3])
        return int(x + w // 2)

    bodies = body_cas.detectMultiScale(frame_gray, scaleFactor=1.05, minNeighbors=3, minSize=(60, 80))
    if len(bodies):
        x, y, w, h = max(bodies, key=lambda b: b[2] * b[3])
        return int(x + w // 2)

    return source_w // 2


def compute_auto_crop(video_path: str, start: float, end: float) -> CropResult:
    """
    Sample every 10th frame in [start, end], detect subject centroid,
    smooth with a rolling average, return the median X as the static
    crop offset for the segment.
    """
    cap = cv2.VideoCapture(str(video_path))
    try:
        fps      = cap.get(cv2.CAP_PROP_FPS) or 30.0
        source_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        source_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        crop_w   = _crop_width(source_h)

        if source_w == 0 or source_h == 0:
            raise ValueError(f"Could not read video dimensions from {video_path}")

        start_frame = int(start * fps)
        end_frame   = int(end   * fps)
        step        = max(1, (end_frame - start_frame) // 10)

        x_positions: list[int] = []

        frame_idx = start_frame
        while frame_idx <= end_frame:
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
            ret, frame = cap.read()
            if not ret:
                break
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            subject_x = _detect_subject_x(gray, source_w)
            # Convert centroid → crop left edge (centre the crop window on subject)
            crop_x = _clamp_x(subject_x - crop_w // 2, crop_w, source_w)
            x_positions.append(crop_x)
            frame_idx += step

        if not x_positions:
            # Fallback: centre crop
            x_positions = [_clamp_x(source_w // 2 - crop_w // 2, crop_w, source_w)]

        # Smooth with rolling average (window=5) then take median
        arr = np.array(x_positions, dtype=float)
        if len(arr) >= 5:
            kernel = np.ones(5) / 5
            arr = np.convolve(arr, kernel, mode="same")
        best_x = int(np.median(arr))
        best_x = _clamp_x(best_x, crop_w, source_w)

        return CropResult(x=best_x, y=0, w=crop_w, h=source_h,
                          source_w=source_w, source_h=source_h)
    finally:
        cap.release()
