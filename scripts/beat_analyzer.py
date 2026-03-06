"""
beat_analyzer.py
----------------
Beat detection and music structure analysis for VideoAgent.

Primary analyzer: All-In-One Music Structure Analyzer (GPU, ~2GB VRAM)
Fallback:         librosa (CPU, less accurate structure detection)

Isolated module — no imports from other VideoAgent scripts.

Environment variables:
    BEAT_CACHE_DIR  directory for beat map JSON cache (default: /tmp/videoagent_beat_cache)
"""

import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

# ── Constants ──────────────────────────────────────────────────────────────────

BEAT_CACHE_DIR = Path(os.environ.get("BEAT_CACHE_DIR", "/tmp/videoagent_beat_cache"))
BEAT_CACHE_DIR.mkdir(parents=True, exist_ok=True)

SECTION_ENERGY: dict[str, float] = {
    "intro":      0.3,
    "verse":      0.5,
    "chorus":     0.9,
    "bridge":     0.6,
    "outro":      0.4,
    "break":      0.5,
    "solo":       0.7,
    "pre-chorus": 0.65,
}

AUDIO_EXTENSIONS = {".mp3", ".aac", ".wav", ".m4a", ".flac", ".ogg"}

# ── Audio normalisation ────────────────────────────────────────────────────────

def normalize_audio(input_path: str, output_path: str) -> str:
    """
    Convert any audio format to 44100 Hz mono WAV using ffmpeg.
    Returns output_path.
    Raises RuntimeError if ffmpeg fails.
    """
    cmd = [
        "ffmpeg", "-y", "-i", input_path,
        "-ar", "44100", "-ac", "1",
        "-acodec", "pcm_s16le",
        output_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"ffmpeg audio conversion failed:\n{result.stderr[-2000:]}"
        )
    return output_path


# ── Beat map cache ─────────────────────────────────────────────────────────────

def _cache_key(audio_path: str) -> str:
    """MD5 of file contents — cache invalidates automatically when file changes."""
    with open(audio_path, "rb") as f:
        return hashlib.md5(f.read()).hexdigest()


def _cache_path(key: str) -> Path:
    return BEAT_CACHE_DIR / f"{key}.json"


def load_cached_beat_map(audio_path: str) -> Optional[dict]:
    """Return cached beat map if available, else None."""
    key = _cache_key(audio_path)
    p = _cache_path(key)
    if p.exists():
        try:
            with open(p) as f:
                return json.load(f)
        except Exception:
            return None
    return None


def save_beat_map_cache(audio_path: str, beat_map: dict) -> None:
    key = _cache_key(audio_path)
    with open(_cache_path(key), "w") as f:
        json.dump(beat_map, f)


# ── Beat smoothing (FIX C) ─────────────────────────────────────────────────────

def smooth_beats(beats: list, bpm: float) -> list:
    """
    Remove outlier beats that deviate >20% from the expected interval,
    filling in missed beats where a double-length gap is detected.

    Improves snap accuracy when allin1 produces occasional jitter.
    """
    if len(beats) < 4 or bpm <= 0:
        return beats

    import numpy as _np
    expected_interval = 60.0 / bpm
    smoothed = [beats[0]]

    for i in range(1, len(beats)):
        interval = beats[i] - smoothed[-1]
        ratio = interval / expected_interval

        if 0.8 <= ratio <= 1.2:
            # Normal beat — accept as-is
            smoothed.append(beats[i])
        elif 1.8 <= ratio <= 2.2:
            # Double gap — a beat was missed; interpolate then accept
            smoothed.append(smoothed[-1] + expected_interval)
            smoothed.append(beats[i])
        # else: discard the outlier beat

    return [round(float(b), 4) for b in smoothed]


# ── Primary analysis: All-In-One ───────────────────────────────────────────────

def analyze_with_allinone(wav_path: str) -> dict:
    """
    Run All-In-One Music Structure Analyzer.
    Returns raw beat map dict.
    Requires GPU. Raises ImportError if allin1 not installed.
    """
    import allin1
    result = allin1.analyze(wav_path)

    raw_beats = [round(float(b), 4) for b in result.beats]
    bpm       = round(float(result.bpm), 2)
    smoothed  = smooth_beats(raw_beats, bpm)

    return {
        "bpm":       bpm,
        "beats":     smoothed,
        "downbeats": [round(float(d), 4) for d in result.downbeats],
        "segments":  [
            {
                "start":  round(float(s.start), 4),
                "end":    round(float(s.end), 4),
                "label":  s.label,
                "energy": SECTION_ENERGY.get(s.label, 0.5),
            }
            for s in result.segments
        ],
        "duration":  round(float(result.beats[-1]) if result.beats else 0.0, 4),
        "analyzer":  "allin1",
    }


# ── Fallback analysis: librosa ─────────────────────────────────────────────────

def analyze_with_fallback(wav_path: str) -> dict:
    """
    CPU fallback using librosa for beats + basic structure estimation.
    No GPU required. Less accurate structure detection.
    """
    import librosa
    import numpy as np

    y, sr = librosa.load(wav_path, sr=44100, mono=True)
    tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr)
    beat_times = librosa.frames_to_time(beat_frames, sr=sr).tolist()

    downbeats = beat_times[::4]

    duration = librosa.get_duration(y=y, sr=sr)
    quarter = duration / 4
    segments = [
        {"start": 0.0,               "end": round(quarter, 4),   "label": "intro",  "energy": 0.3},
        {"start": round(quarter, 4),  "end": round(quarter*2, 4), "label": "verse",  "energy": 0.5},
        {"start": round(quarter*2, 4),"end": round(quarter*3, 4), "label": "chorus", "energy": 0.9},
        {"start": round(quarter*3, 4),"end": round(duration, 4),  "label": "outro",  "energy": 0.4},
    ]

    return {
        "bpm":       round(float(np.squeeze(tempo)), 2),
        "beats":     [round(float(b), 4) for b in beat_times],
        "downbeats": [round(float(d), 4) for d in downbeats],
        "segments":  segments,
        "duration":  round(float(duration), 4),
        "analyzer":  "librosa_fallback",
    }


# ── Main entry point ───────────────────────────────────────────────────────────

def analyze_music_track(audio_path: str, use_cache: bool = True) -> dict:
    """
    Analyze a music track and return a beat map.

    Returns:
    {
        "bpm":       float,
        "beats":     [float, ...],      # beat timestamps in seconds
        "downbeats": [float, ...],      # downbeat timestamps in seconds
        "segments":  [                  # structural sections
            {"start": float, "end": float, "label": str, "energy": float}
        ],
        "duration":  float,
        "analyzer":  str,               # "allin1" or "librosa_fallback"
    }
    """
    audio_path = str(audio_path)

    if use_cache:
        cached = load_cached_beat_map(audio_path)
        if cached:
            print(f"[beat_analyzer] Cache hit for {Path(audio_path).name}")
            return cached

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        wav_path = tmp.name

    try:
        normalize_audio(audio_path, wav_path)
        print(f"[beat_analyzer] Normalized audio → {wav_path}")

        beat_map: Optional[dict] = None
        allinone_error: Optional[Exception] = None

        try:
            print("[beat_analyzer] Running All-In-One analyzer...")
            beat_map = analyze_with_allinone(wav_path)
            print(
                f"[beat_analyzer] All-In-One: {beat_map['bpm']:.1f} BPM, "
                f"{len(beat_map['beats'])} beats, {len(beat_map['segments'])} segments"
            )
        except Exception as exc:
            allinone_error = exc
            print(f"[beat_analyzer] All-In-One failed ({exc}), using librosa fallback")
            try:
                beat_map = analyze_with_fallback(wav_path)
                print(f"[beat_analyzer] Fallback: {beat_map['bpm']:.1f} BPM")
            except Exception as exc2:
                raise RuntimeError(
                    f"Both analyzers failed. All-In-One: {allinone_error}. Fallback: {exc2}"
                )
    finally:
        try:
            os.unlink(wav_path)
        except Exception:
            pass

    if use_cache and beat_map is not None:
        save_beat_map_cache(audio_path, beat_map)

    return beat_map  # type: ignore[return-value]


# ── Cut point snapping ─────────────────────────────────────────────────────────

def snap_cuts_to_beats(
    segments: list,
    beat_map: dict,
    snap_window: float = 0.75,   # FIX B: widened from 0.5 → 0.75
    prefer_downbeats: bool = True,
) -> list:
    """
    Adjust segment cut points to align with musical beats.

    Priority order (FIX E — half-bar preference):
      1. Nearest downbeat within snap_window
      2. Nearest half-bar beat (every 2nd beat) within snap_window
      3. Nearest single beat within snap_window
      4. No snap — leave cut where it falls

    Segments shorter than 1.5× beat interval are skipped (FIX D).

    Args:
        segments:         List of dicts with 'start', 'end', 'duration' keys.
        beat_map:         From analyze_music_track().
        snap_window:      Max seconds to shift a cut point (default ±0.75s).
        prefer_downbeats: Try downbeats before half-bar and single beats.

    Returns:
        New list of segments with adjusted start/end times.
        Original segment dicts are not mutated.
    """
    import numpy as np

    beats          = np.array(beat_map["beats"])
    downbeats      = np.array(beat_map["downbeats"]) if beat_map.get("downbeats") else beats
    half_bar_beats = beats[::2]                          # FIX E: every 2nd beat
    track_duration = float(beat_map["duration"])
    bpm            = float(beat_map.get("bpm", 120))
    beat_interval  = 60.0 / bpm if bpm > 0 else 0.5
    min_snap_dur   = beat_interval * 1.5                 # FIX D: min duration to snap

    snapped: list = []
    current_time  = 0.0

    for seg in segments:
        duration      = seg.get("duration") or (seg["end"] - seg["start"])
        snapped_start = current_time

        # FIX D — skip snapping for segments too short to align meaningfully
        if duration >= min_snap_dur:
            if prefer_downbeats and len(downbeats) > 0:
                db_dists   = np.abs(downbeats - current_time)
                nearest_db = int(np.argmin(db_dists))
                if db_dists[nearest_db] <= snap_window:
                    snapped_start = float(downbeats[nearest_db])

            if snapped_start == current_time and len(half_bar_beats) > 0:
                # FIX E — try half-bar before single beat
                hb_dists   = np.abs(half_bar_beats - current_time)
                nearest_hb = int(np.argmin(hb_dists))
                if hb_dists[nearest_hb] <= snap_window:
                    snapped_start = float(half_bar_beats[nearest_hb])

            if snapped_start == current_time and len(beats) > 0:
                b_dists   = np.abs(beats - current_time)
                nearest_b = int(np.argmin(b_dists))
                if b_dists[nearest_b] <= snap_window:
                    snapped_start = float(beats[nearest_b])

        snapped_start = min(snapped_start, max(0.0, track_duration - duration))

        snapped_end = snapped_start + duration
        new_seg = dict(seg)
        new_seg["start_original"] = seg["start"]
        new_seg["end_original"]   = seg["end"]
        new_seg["start"]          = round(snapped_start, 4)
        new_seg["end"]            = round(snapped_end, 4)
        new_seg["beat_snapped"]   = snapped_start != current_time
        snapped.append(new_seg)

        current_time = snapped_end

    return snapped


# ── Duration targeting ────────────────────────────────────────────────────────

def target_segment_durations(
    segments: list,
    target_total_seconds: float,
    beat_interval: Optional[float] = None,
    min_segment_seconds: float = 1.5,
    max_segment_seconds: float = 6.0,
) -> list:
    """
    Adjust segment durations to hit a target total and optionally align
    each clip's length to the nearest multiple of the beat interval.

    Args:
        segments:             Selected segments with start/end keys.
        target_total_seconds: Desired total output duration (e.g. 58.0).
        beat_interval:        Seconds per beat (60/bpm). If None, skip beat
                              alignment and only scale to target.
        min_segment_seconds:  Minimum allowed clip duration after adjustment.
        max_segment_seconds:  Maximum allowed clip duration after adjustment.

    Returns:
        New list of segments with updated 'end' and 'duration' keys.
        Original dicts are not mutated.
    """
    if not segments:
        return segments

    current_total = sum(
        float(s.get("duration") or (s["end"] - s["start"])) for s in segments
    )
    scale = target_total_seconds / current_total if current_total > 0 else 1.0

    adjusted: list = []
    for seg in segments:
        raw_dur = float(seg.get("duration") or (seg["end"] - seg["start"])) * scale

        if beat_interval and beat_interval > 0:
            # Round to nearest beat multiple, clamped to min/max
            beats_min = max(1, round(min_segment_seconds / beat_interval))
            beats_max = max(beats_min, round(max_segment_seconds / beat_interval))
            beats_n   = round(raw_dur / beat_interval)
            beats_n   = max(beats_min, min(beats_max, beats_n))
            final_dur = round(beats_n * beat_interval, 4)
        else:
            final_dur = round(
                max(min_segment_seconds, min(max_segment_seconds, raw_dur)), 4
            )

        new_seg = dict(seg)
        new_seg["duration"] = final_dur
        new_seg["end"]      = round(float(seg["start"]) + final_dur, 4)
        adjusted.append(new_seg)

    return adjusted


# ── Energy matching ────────────────────────────────────────────────────────────

def match_clips_to_sections(
    scored_clips: list,
    beat_map: dict,
) -> list:
    """
    Reorder/assign clips so high-energy clips align with chorus sections.

    Args:
        scored_clips: List of dicts with 'combined_score' or 'template_score'.
        beat_map:     From analyze_music_track().

    Returns:
        Reordered list of clips matched to music section energy.
        Original list is not mutated.
    """
    sections = beat_map.get("segments", [])
    if not sections or not scored_clips:
        return list(scored_clips)

    energy_key = (
        "combined_score" if scored_clips[0].get("combined_score") is not None
        else "template_score"
    )
    sorted_clips   = sorted(scored_clips, key=lambda c: c.get(energy_key, 0), reverse=True)
    sorted_sections = sorted(sections,   key=lambda s: s.get("energy", 0.5),  reverse=True)

    assignments: list = []
    available = list(sorted_clips)

    for section in sorted_sections:
        if not available:
            break
        target_energy = section.get("energy", 0.5)
        best = min(available, key=lambda c: abs(c.get(energy_key, 0) - target_energy))
        assignments.append({
            **best,
            "assigned_section": section["label"],
            "section_start":    section["start"],
            "section_energy":   target_energy,
        })
        available.remove(best)

    assignments.extend(available)
    return assignments
