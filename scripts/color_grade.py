import json
import os
from pathlib import Path

import cv2
import numpy as np
from moviepy import VideoFileClip

BASE_DIR = Path(__file__).resolve().parent.parent
STYLE_DIR = BASE_DIR / "style"
STYLE_PATH = STYLE_DIR / "style_profile.json"

_style_profile = None


def load_style_profile():
    global _style_profile
    if _style_profile is None:
        if STYLE_PATH.exists():
            with open(STYLE_PATH, "r") as f:
                _style_profile = json.load(f)
        else:
            _style_profile = {
                "color": {
                    "brightness": 0.0,
                    "contrast": 1.0,
                    "saturation": 1.0,
                    "warmth": 0.0,
                    "shadow_lift": 0.0,
                    "highlight_rolloff": 0.0,
                }
            }
    return _style_profile


def _safe_get(d, k, default):
    v = d.get(k)
    return default if v is None else v


def _build_color_params():
    profile = load_style_profile()
    color = profile.get("color", {})

    # Use the Hero-driven values directly; clamp to reasonable ranges.
    brightness = np.clip(_safe_get(color, "brightness", 0.0) - 0.5, -0.25, 0.25)
    contrast = np.clip(0.8 + _safe_get(color, "contrast", 1.0), 0.8, 1.4)
    saturation = np.clip(0.8 + _safe_get(color, "saturation", 1.0), 0.8, 1.5)
    warmth = np.clip(_safe_get(color, "warmth", 0.0), -0.3, 0.3)
    shadow_lift = np.clip(_safe_get(color, "shadow_lift", 0.0) * 0.5, 0.0, 0.2)
    highlight_rolloff = np.clip(_safe_get(color, "highlight_rolloff", 0.0) * 0.5, 0.0, 0.3)

    return {
        "brightness": float(brightness),
        "contrast": float(contrast),
        "saturation": float(saturation),
        "warmth": float(warmth),
        "shadow_lift": float(shadow_lift),
        "highlight_rolloff": float(highlight_rolloff),
    }


_COLOR_PARAMS = _build_color_params()
WORK_DTYPE = np.float32 if os.environ.get("COLOR_GRADE_FP16", "1") == "0" else np.float16
CHUNK_ROWS = max(64, int(os.environ.get("COLOR_GRADE_CHUNK_ROWS", "240")))


def _process_chunk(chunk: np.ndarray) -> np.ndarray:
    """
    Apply grading to a contiguous chunk of rows.
    Keeping the chunk small avoids large temporary allocations.
    """
    dtype = WORK_DTYPE
    inv_255 = dtype(1.0 / 255.0)

    p = _COLOR_PARAMS
    b = p["brightness"]
    c = p["contrast"]
    s_boost = p["saturation"]
    w_shift = p["warmth"]
    shadow_lift = p["shadow_lift"]
    highlight_rolloff = p["highlight_rolloff"]

    hsv = cv2.cvtColor(chunk, cv2.COLOR_RGB2HSV).astype(dtype)
    s = hsv[:, :, 1] * inv_255
    v = hsv[:, :, 2] * inv_255

    v = np.clip((v - 0.5) * c + 0.5 + b, 0.0, 1.0)
    s = np.clip(s * s_boost, 0.0, 1.0)

    hsv[:, :, 1] = np.clip(s * 255.0, 0.0, 255.0)
    hsv[:, :, 2] = np.clip(v * 255.0, 0.0, 255.0)
    img_rgb = cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2RGB).astype(dtype) * inv_255

    # Warmth shift in place
    img_rgb[:, :, 0] = np.clip(img_rgb[:, :, 0] + w_shift * 0.2, 0.0, 1.0)
    img_rgb[:, :, 2] = np.clip(img_rgb[:, :, 2] - w_shift * 0.2, 0.0, 1.0)

    ycrcb = cv2.cvtColor((img_rgb * 255.0).astype(np.uint8), cv2.COLOR_RGB2YCrCb).astype(dtype)
    y = ycrcb[:, :, 0] * inv_255
    y = y + shadow_lift * (0.25 - y)
    y = y - highlight_rolloff * np.clip(y - 0.75, 0.0, 1.0)
    ycrcb[:, :, 0] = np.clip(y * 255.0, 0.0, 255.0)

    return cv2.cvtColor(ycrcb.astype(np.uint8), cv2.COLOR_YCrCb2RGB)


def _grade_frame(frame):
    """
    Apply grading in manageable row chunks to avoid large allocations.
    """
    h = frame.shape[0]
    output = np.empty_like(frame)
    for y in range(0, h, CHUNK_ROWS):
        y_end = min(h, y + CHUNK_ROWS)
        output[y:y_end] = _process_chunk(frame[y:y_end])
    return output

def apply_color_grade(clip: VideoFileClip) -> VideoFileClip:
    """
    Apply Hero-driven color grading to a MoviePy clip.
    """
    # moviepy>=2.0 replaced `fl_image` with `image_transform`
    return clip.image_transform(_grade_frame)
