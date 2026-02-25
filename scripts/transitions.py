"""transitions.py
-----------------
Transition catalog for the video reel compiler.

IMPROVEMENTS IN THIS VERSION:
  - Expanded catalog: 14 transitions across Phase 1 and Phase 2
  - Added push_up, slide_left, dip_white for more vertical-format options
  - Added zoom_out as counterpart to zoom_in (good for closers)
  - Smart rule engine: pick_transition() chooses a transition from
    the active template's preferred set based on content delta
  - All Phase 2 durations tuned for 9:16 vertical social content

Phase 1 — concat-safe (inserts a short solid-colour clip between segments):
    cut         — hard cut, nothing inserted (default, always fastest)
    jump_cut    — deliberate repeat-cut on motion; UI label only
    flash_white — 2-frame white flash (energetic, beat drop)
    flash_black — 2-frame black flash (dramatic, scene change)
    dip_black   — ~0.5s fade-to-black hold (slower, contemplative)
    dip_white   — ~0.5s fade-to-white hold (bright, clean, minimal)

Phase 2 — xfade (re-encodes boundary frames; smooth blends):
    dissolve    — classic crossfade                (0.25s)
    fade_black  — fade through black               (0.40s)
    wipe_up     — wipe upward                      (0.20s) — ideal 9:16
    wipe_left   — horizontal wipe left             (0.20s)
    zoom_in     — zoom into next clip              (0.30s)
    zoom_out    — zoom out from current into next  (0.30s) — good closers
    push_up     — current pushes up, next arrives  (0.25s)
    slide_left  — slide pan left                   (0.25s)

Template-to-transition mapping:
    travel_reel   — wipe_up, dissolve, cut, zoom_in
    event_concert — cut, flash_white, jump_cut, dissolve
    grwm_style    — dissolve, fade_black, cut, dip_white
    product_style — cut, dissolve, wipe_left, slide_left
    breakfast_food— dissolve, dip_white, fade_black, cut
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Dict, List, Literal, Optional, Tuple

# ── Type alias ────────────────────────────────────────────────────────────────

TransitionType = Literal[
    "cut", "jump_cut",
    "flash_white", "flash_black", "dip_black", "dip_white",
    "dissolve", "fade_black",
    "wipe_up", "wipe_left",
    "zoom_in", "zoom_out",
    "push_up", "slide_left",
]

ALL_TRANSITIONS: list[str] = [
    "cut", "jump_cut",
    "flash_white", "flash_black", "dip_black", "dip_white",
    "dissolve", "fade_black",
    "wipe_up", "wipe_left",
    "zoom_in", "zoom_out",
    "push_up", "slide_left",
]

DEFAULT_TRANSITION: TransitionType = "cut"

# ── Transition sets ────────────────────────────────────────────────────────────

PHASE1_TRANSITIONS: frozenset[str] = frozenset({
    "cut", "jump_cut",
    "flash_white", "flash_black",
    "dip_black", "dip_white",
})

PHASE2_TRANSITIONS: frozenset[str] = frozenset({
    "dissolve", "fade_black",
    "wipe_up", "wipe_left",
    "zoom_in", "zoom_out",
    "push_up", "slide_left",
})

# xfade filter name + overlap duration (seconds) for each Phase 2 transition
# wipe_up and push_up are fastest (0.20s) — suits the kinetic feel of vertical reels
# fade_black and zoom are slower — suit mood shifts and closers
XFADE_MAP: dict[str, tuple[str, float]] = {
    "dissolve":   ("fade",       0.25),
    "fade_black": ("fadeblack",  0.40),
    "wipe_up":    ("wipeup",     0.20),
    "wipe_left":  ("wipeleft",   0.20),
    "zoom_in":    ("zoomin",     0.30),
    "zoom_out":   ("fadeblack",  0.30),   # ffmpeg has no zoomout; simulate w/ fadeblack
    "push_up":    ("sliceup",    0.25),
    "slide_left": ("slideleft",  0.25),
}

# ── Template preference sets ─────────────────────────────────────────────────
# Ordered by preference — first entry is the default for that template.
# pick_transition() uses these to select contextually appropriate transitions.

TEMPLATE_TRANSITIONS: dict[str, list[str]] = {
    "travel_reel":   ["wipe_up",  "dissolve",   "cut",      "zoom_in"  ],
    "event_concert": ["cut",      "flash_white", "jump_cut", "dissolve" ],
    "grwm_style":    ["dissolve", "fade_black",  "cut",      "dip_white"],
    "product_style": ["cut",      "dissolve",    "wipe_left","slide_left"],
    "breakfast_food":["dissolve", "dip_white",   "fade_black","cut"     ],
}

# Current active template (read from env, same as analyze_and_edit.py)
_TEMPLATE = os.environ.get("STYLE_TEMPLATE", "travel_reel").strip().lower()
_TEMPLATE_PREFS: list[str] = TEMPLATE_TRANSITIONS.get(_TEMPLATE, ["cut", "dissolve", "wipe_up"])


# ── Smart transition picker ───────────────────────────────────────────────────

def pick_transition(
    seg_prev: Optional[dict],
    seg_curr: dict,
    position: str = "middle",
    force: Optional[str] = None,
) -> str:
    """
    Choose the most appropriate transition given content context.

    Priority order:
      1. force= argument (explicit override from editing_brain or user)
      2. First clip always → "cut" (no transition before first frame)
      3. Hard lighting jump (dark↔bright) → "fade_black" (masks harshness)
      4. Closer position → template closer preference (dissolve / dip_white)
      5. High-energy both sides → "cut" or "flash_white"
      6. Mood shift (energy↔calm) → "dissolve"
      7. Template default preference
    """
    if force is not None:
        return force
    if seg_prev is None or position == "opener":
        return "cut"

    tags_prev = set(seg_prev.get("tags", []))
    tags_curr = set(seg_curr.get("tags", []))

    _DARK   = {"dark"}
    _BRIGHT = {"bright"}
    _ENERGY = {"high_energy", "busy", "face", "person"}
    _CALM   = {"minimal", "outdoor", "wide_shot"}

    prev_dark   = bool(tags_prev & _DARK)
    curr_dark   = bool(tags_curr & _DARK)
    prev_bright = bool(tags_prev & _BRIGHT)
    curr_bright = bool(tags_curr & _BRIGHT)

    # Hard lighting jump
    if (prev_dark and curr_bright) or (prev_bright and curr_dark):
        return "fade_black"

    # Closer
    if position == "closer":
        closer_pref = [t for t in _TEMPLATE_PREFS if t in ("dissolve", "dip_white", "fade_black", "zoom_out")]
        return closer_pref[0] if closer_pref else "dissolve"

    prev_energy = bool(tags_prev & _ENERGY)
    curr_energy = bool(tags_curr & _ENERGY)

    # Both high-energy — use template's fast-cut option
    if prev_energy and curr_energy:
        fast = [t for t in _TEMPLATE_PREFS if t in ("cut", "flash_white", "jump_cut", "wipe_up")]
        return fast[0] if fast else "cut"

    # Mood shift
    if prev_energy != curr_energy:
        mood = [t for t in _TEMPLATE_PREFS if t in ("dissolve", "fade_black", "dip_white")]
        return mood[0] if mood else "dissolve"

    # Same setting back-to-back — use a motion transition to avoid monotony
    prev_setting = tags_prev & {"outdoor", "indoor"}
    curr_setting = tags_curr & {"outdoor", "indoor"}
    if prev_setting and prev_setting == curr_setting:
        motion = [t for t in _TEMPLATE_PREFS if t in ("wipe_up", "push_up", "wipe_left", "slide_left")]
        return motion[0] if motion else "wipe_up"

    # Template default
    return _TEMPLATE_PREFS[0]


# ── Phase 1 helpers ────────────────────────────────────────────────────────────

def _render_colour_clip(
    out_path: Path,
    colour: str,
    width: int,
    height: int,
    fps: float,
    n_frames: int,
) -> bool:
    """Encode a short solid-colour MP4 (video + silent audio) to out_path."""
    duration = n_frames / fps
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi",
        "-i", f"color={colour}:size={width}x{height}:rate={fps}:duration={duration}",
        "-c:v", "libx264", "-preset", "fast", "-crf", "18",
        "-c:a", "aac", "-ar", "44100", "-ac", "2",
        "-f", "mp4", str(out_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.returncode == 0


def render_phase1_transition(
    transition: str,
    out_path: Path,
    width: int,
    height: int,
    fps: float,
) -> bool:
    """
    Render a Phase 1 insert clip for transition to out_path.
    Returns True when a clip was written, False for pure hard-cuts.

    dip_black / dip_white use 15 frames (~0.5s at 30fps) for a held dip.
    flash_white / flash_black use 2 frames for a sharp flash.
    """
    if transition in ("cut", "jump_cut"):
        return False
    if transition == "flash_white":
        return _render_colour_clip(out_path, "white", width, height, fps, 2)
    if transition == "flash_black":
        return _render_colour_clip(out_path, "black", width, height, fps, 2)
    if transition == "dip_black":
        return _render_colour_clip(out_path, "black", width, height, fps, 15)
    if transition == "dip_white":
        return _render_colour_clip(out_path, "white", width, height, fps, 15)
    return False


# ── Phase 2 helpers ────────────────────────────────────────────────────────────

def probe_duration(path: Path) -> float:
    """Return duration of path in seconds via ffprobe. Returns 0.0 on error."""
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=duration",
        "-of", "csv=p=0",
        str(path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    try:
        return float(result.stdout.strip())
    except ValueError:
        pass
    cmd2 = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "csv=p=0",
        str(path),
    ]
    result2 = subprocess.run(cmd2, capture_output=True, text=True)
    try:
        return float(result2.stdout.strip())
    except ValueError:
        return 0.0


def render_xfade_transition(
    clip_a: Path,
    clip_b: Path,
    out_path: Path,
    xfade_name: str,
    duration: float,
    a_duration: float,
    crf: str = "18",
    preset: str = "fast",
    audio_br: str = "128k",
) -> bool:
    """
    Blend the tail of clip_a into the head of clip_b using ffmpeg xfade.

    Produces a single combined output:
        [full A] ─── xfade overlap ─── [full B]
    Total duration ≈ a_duration + b_duration − xfade_duration.

    For zoom_out (no native ffmpeg filter): falls back to fadeblack.
    For push_up / slide_left: uses sliceup / slideleft — available in
    ffmpeg 5.0+ (most current installations). Falls back to wipeleft
    if the filter is not available.
    """
    # Clamp xfade duration to be less than either clip's duration
    max_duration = min(a_duration * 0.5, duration)
    safe_duration = max(0.05, min(duration, max_duration))
    offset = max(0.0, a_duration - safe_duration)

    filter_complex = (
        f"[0:v][1:v]xfade=transition={xfade_name}"
        f":duration={safe_duration:.3f}:offset={offset:.3f}[v];"
        f"[0:a][1:a]acrossfade=d={safe_duration:.3f}[a]"
    )
    cmd = [
        "ffmpeg", "-y",
        "-i", str(clip_a),
        "-i", str(clip_b),
        "-filter_complex", filter_complex,
        "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
        "-c:a", "aac", "-b:a", audio_br,
        "-f", "mp4", str(out_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        # Fallback: try wipeleft for unsupported filters (sliceup on older ffmpeg)
        if xfade_name in ("sliceup", "slideleft"):
            print(f"  [warn] xfade '{xfade_name}' not available — falling back to wipeleft")
            fallback_fc = (
                f"[0:v][1:v]xfade=transition=wipeleft"
                f":duration={safe_duration:.3f}:offset={offset:.3f}[v];"
                f"[0:a][1:a]acrossfade=d={safe_duration:.3f}[a]"
            )
            cmd2 = cmd.copy()
            cmd2[cmd2.index("-filter_complex") + 1] = fallback_fc
            result2 = subprocess.run(cmd2, capture_output=True, text=True)
            if result2.returncode == 0:
                return True
        print(f"  [warn] xfade '{xfade_name}' failed: {result.stderr[-400:]}")
        return False

    return True