"""Transition catalog for the video reel compiler.

Phase 1 — concat-safe (insert a solid-colour clip between segments):
    cut         — hard cut, nothing inserted (default)
    jump_cut    — same as cut (deliberate repeat-cut on motion; UI label only)
    flash_white — 2-frame white flash
    flash_black — 2-frame black flash
    dip_black   — ~0.5s fade-to-black hold (~15 frames)

Phase 2 — xfade (re-encodes boundary frames; smooth blends):
    dissolve    — crossfade           (xfade=fade,       0.25 s)
    fade_black  — fade through black  (xfade=fadeblack,  0.40 s)
    wipe_up     — wipe upward         (xfade=wipeup,     0.20 s) — ideal for 9:16
    zoom_in     — zoom into next clip (xfade=zoomin,     0.30 s)
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Literal

# ── Type alias ────────────────────────────────────────────────────────────────

TransitionType = Literal[
    "cut", "jump_cut",
    "flash_white", "flash_black", "dip_black",
    "dissolve", "fade_black", "wipe_up", "zoom_in",
]

ALL_TRANSITIONS: list[str] = [
    "cut", "jump_cut",
    "flash_white", "flash_black", "dip_black",
    "dissolve", "fade_black", "wipe_up", "zoom_in",
]

DEFAULT_TRANSITION: TransitionType = "cut"

# ── Transition sets ────────────────────────────────────────────────────────────

PHASE1_TRANSITIONS: frozenset[str] = frozenset({
    "cut", "jump_cut", "flash_white", "flash_black", "dip_black",
})

PHASE2_TRANSITIONS: frozenset[str] = frozenset({
    "dissolve", "fade_black", "wipe_up", "zoom_in",
})

# xfade filter name + overlap duration (seconds) for Phase 2 transitions
XFADE_MAP: dict[str, tuple[str, float]] = {
    "dissolve":   ("fade",      0.25),
    "fade_black": ("fadeblack", 0.40),
    "wipe_up":    ("wipeup",    0.20),
    "zoom_in":    ("zoomin",    0.30),
}

# ── Phase 1 helpers ────────────────────────────────────────────────────────────

def _render_colour_clip(
    out_path: Path,
    colour: str,
    width: int,
    height: int,
    fps: float,
    n_frames: int,
) -> bool:
    """Encode a short solid-colour MP4 (video + silent audio) to *out_path*."""
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
    """Render a Tier-1 insert clip for *transition* to *out_path*.

    Returns True when a clip was written, False when no clip is needed
    (cut and jump_cut are pure hard-cuts — nothing is inserted).
    """
    if transition in ("cut", "jump_cut"):
        return False
    if transition == "flash_white":
        return _render_colour_clip(out_path, "white", width, height, fps, 2)
    if transition == "flash_black":
        return _render_colour_clip(out_path, "black", width, height, fps, 2)
    if transition == "dip_black":
        return _render_colour_clip(out_path, "black", width, height, fps, 15)
    return False


# ── Phase 2 helpers ────────────────────────────────────────────────────────────

def probe_duration(path: Path) -> float:
    """Return the duration of *path* in seconds via ffprobe. Returns 0.0 on error."""
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
    # Fallback to format-level duration
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
    """Blend the tail of *clip_a* into the head of *clip_b* using ffmpeg xfade.

    Produces a single combined output file containing:
        [full A] ─── xfade overlap ─── [full B]
    Total duration ≈ a_duration + b_duration − xfade_duration.

    *crf*, *preset*, and *audio_br* should match the quality profile used for
    the surrounding segment clips so the concat demuxer can copy streams without
    re-encoding or codec-parameter mismatches.

    Returns True on success.
    """
    offset = max(0.0, a_duration - duration)
    filter_complex = (
        f"[0:v][1:v]xfade=transition={xfade_name}"
        f":duration={duration:.3f}:offset={offset:.3f}[v];"
        f"[0:a][1:a]acrossfade=d={duration:.3f}[a]"
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
        print(f"  [warn] xfade '{xfade_name}' failed: {result.stderr[-400:]}")
    return result.returncode == 0
