"""
analyze_and_edit.py
-------------------
Main pipeline entry point.

Fixes in this version vs previous run:
  1. Segment generation for scenes=0 clips now divides full clip duration
     into sub-segments rather than capping at one chunk. A 10s clip at
     max_segment=2.3s now produces 4-5 candidates instead of 1.
  2. Scene detection threshold lowered 27→20 for short phone clips.
  3. Elapsed time printed after each step for visibility.
"""

import os
import json
import hashlib
import logging
from pathlib import Path
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Any, List

import numpy as np
import librosa
import cv2
from scenedetect import VideoManager, SceneManager
from scenedetect.detectors import ContentDetector
from scripts.editing_brain import plan_edit
from scripts.semantic_siglip import enrich_segments_with_siglip
from scripts.pipeline_logger import PipelineLogger
from scripts.transitions import (
    PHASE1_TRANSITIONS,
    PHASE2_TRANSITIONS,
    XFADE_MAP,
    render_phase1_transition,
    render_xfade_transition,
    probe_duration,
)
import ffmpeg

log = logging.getLogger(__name__)

ENABLE_SPEECH_ANALYSIS = os.environ.get("ENABLE_SPEECH_ANALYSIS", "0") == "1"
if ENABLE_SPEECH_ANALYSIS:
    try:
        from scripts.whisper_helper import analyze_speech_activity
    except Exception as exc:
        print(f"[warn] Cannot import whisper helper: {exc}")
        ENABLE_SPEECH_ANALYSIS = False
        analyze_speech_activity = None
else:
    analyze_speech_activity = None

BASE_DIR           = Path(__file__).resolve().parent.parent
RAW_DIR            = BASE_DIR / "raw_clips"
ANALYSIS_DIR       = BASE_DIR / "analysis"
OUTPUT_DIR         = BASE_DIR / "output"
LOGS_DIR           = BASE_DIR / "logs"
STYLE_PROFILES_DIR = BASE_DIR / "style_profiles"
CACHE_FILE         = ANALYSIS_DIR / "cache.json"

ANALYSIS_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)

MAX_ANALYSIS_WORKERS = int(os.environ.get("MAX_ANALYSIS_WORKERS", "4"))

VALID_TEMPLATES = [
    "breakfast_food", "event_concert", "grwm_style", "product_style", "travel_reel"
]
_TEMPLATE_NAME = os.environ.get("STYLE_TEMPLATE", "travel_reel").strip().lower()
if _TEMPLATE_NAME not in VALID_TEMPLATES:
    print(f"[warn] Unknown STYLE_TEMPLATE='{_TEMPLATE_NAME}'. Defaulting to 'travel_reel'.")
    _TEMPLATE_NAME = "travel_reel"


def _load_style_profile() -> dict:
    for path in [
        STYLE_PROFILES_DIR / f"{_TEMPLATE_NAME}.json",
        STYLE_PROFILES_DIR / "warm_travel_reels.json",
        BASE_DIR / "style" / "warm_travel_reels.json",
        BASE_DIR / "style" / "style_profile.json",
    ]:
        if not path.exists():
            continue
        try:
            profile = json.loads(path.read_text(encoding="utf-8"))
            print(f"[info] Loaded style profile: {path.name}  (template: {_TEMPLATE_NAME})")
            return profile
        except json.JSONDecodeError as exc:
            print(f"[warn] Style profile invalid at {path}: {exc}")
    print(f"[warn] No style profile found. Using defaults.")
    return {"duration_preferences": {"target_total": 30.0, "min_segment": 0.6, "max_segment": 4.0},
            "pacing": {}, "blur_threshold": 80}


STYLE_PROFILE           = _load_style_profile()
_dur_prefs              = STYLE_PROFILE.get("duration_preferences", {})
TARGET_DURATION_SECONDS = float(_dur_prefs.get("target_total", 30.0))
MIN_SEGMENT_DURATION    = float(_dur_prefs.get("min_segment",   0.6))
MAX_SEGMENT_DURATION    = float(_dur_prefs.get("max_segment",   4.0))
BLUR_THRESHOLD          = int(STYLE_PROFILE.get("blur_threshold", 80))

print(f"[info] Template:        {_TEMPLATE_NAME}")
print(f"[info] Segment range:   {MIN_SEGMENT_DURATION}–{MAX_SEGMENT_DURATION}s")
print(f"[info] Target duration: {TARGET_DURATION_SECONDS}s")
print(f"[info] Blur threshold:  {BLUR_THRESHOLD}")
print(f"[info] Workers:         {MAX_ANALYSIS_WORKERS}")


def _video_hash(path: Path) -> str:
    stat = path.stat()
    return hashlib.md5(f"{path.name}:{stat.st_size}:{stat.st_mtime}".encode()).hexdigest()


def _load_cache() -> Dict[str, Any]:
    if os.environ.get("DISABLE_CACHE") == "1":
        return {}
    if CACHE_FILE.exists():
        try:
            return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_cache(cache: Dict[str, Any]):
    try:
        # Ensure all values are JSON-serialisable (WindowsPath → str)
        def _serialisable(obj):
            if isinstance(obj, dict):
                return {k: _serialisable(v) for k, v in obj.items()}
            if isinstance(obj, list):
                return [_serialisable(v) for v in obj]
            if isinstance(obj, tuple):
                return [_serialisable(v) for v in obj]
            try:
                from pathlib import Path
                if isinstance(obj, Path):
                    return str(obj)
            except Exception:
                pass
            return obj
        CACHE_FILE.write_text(json.dumps(_serialisable(cache), indent=2), encoding="utf-8")
    except Exception as exc:
        print(f"[warn] Could not write cache: {exc}")


def write_log(data, filename=None):
    if filename is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename  = f"log_{_TEMPLATE_NAME}_{timestamp}.json"
    log_path = LOGS_DIR / filename
    with open(log_path, "w") as f:
        json.dump(data, f, indent=4)
    return log_path


def append_to_history(data):
    with open(LOGS_DIR / "history.jsonl", "a") as f:
        f.write(json.dumps(data) + "\n")


def load_feedback_weights():
    return {"audio_weight": 0.5, "motion_weight": 0.5}


def get_video_files() -> List[Path]:
    return [f for f in sorted(RAW_DIR.iterdir())
            if f.suffix.lower() in {".mp4", ".mov", ".mkv"}]


def analyze_audio_energy(video_path: Path) -> float:
    temp = ANALYSIS_DIR / f"{video_path.stem}_temp_audio.wav"
    try:
        (ffmpeg.input(str(video_path))
               .output(str(temp), ac=1, ar=22050, format="wav")
               .overwrite_output().run(quiet=True))
    except Exception as e:
        return 0.0
    try:
        y, sr = librosa.load(str(temp), sr=22050)
    except Exception:
        return 0.0
    finally:
        temp.unlink(missing_ok=True)
    if len(y) == 0:
        return 0.0
    energy = np.array([np.sum(np.abs(y[i:i+2048]**2)) for i in range(0, len(y), 1024)])
    if len(energy) == 0:
        return 0.0
    return float(np.mean((energy - energy.min()) / (energy.max() - energy.min() + 1e-9)))


def analyze_motion(video_path: Path, sample_stride: int = 10) -> float:
    cap = cv2.VideoCapture(str(video_path))
    ret, prev = cap.read()
    if not ret:
        cap.release()
        return 0.0
    prev_gray     = cv2.cvtColor(cv2.resize(prev, (320, 180)), cv2.COLOR_BGR2GRAY)
    motion_values = []
    frame_idx     = 0
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        frame_idx += 1
        if frame_idx % sample_stride != 0:
            continue
        gray = cv2.cvtColor(cv2.resize(frame, (320, 180)), cv2.COLOR_BGR2GRAY)
        motion_values.append(float(cv2.absdiff(prev_gray, gray).mean()))
        prev_gray = gray
    cap.release()
    if not motion_values:
        return 0.0
    arr = np.array(motion_values)
    return float(np.mean((arr - arr.min()) / (arr.max() - arr.min() + 1e-9)))


def detect_scenes(video_path: Path) -> List[tuple]:
    """Threshold=20.0 (was 27.0) catches subtler cuts in phone footage."""
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        vm = VideoManager([str(video_path)])
        sm = SceneManager()
        sm.add_detector(ContentDetector(threshold=20.0))
        vm.start()
        sm.detect_scenes(frame_source=vm)
        scene_list = sm.get_scene_list()
        vm.release()
    return [
        (s.get_seconds(), e.get_seconds())
        for s, e in scene_list
        if e.get_seconds() - s.get_seconds() >= MIN_SEGMENT_DURATION
    ]


def _get_clip_duration(video_path: Path) -> float:
    cap = cv2.VideoCapture(str(video_path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    n   = cap.get(cv2.CAP_PROP_FRAME_COUNT)
    cap.release()
    return n / fps if fps > 0 else 0.0


def analyze_clip(video_path: Path, cache: Dict[str, Any]) -> Dict[str, Any]:
    vid_hash = _video_hash(video_path)
    if vid_hash in cache:
        print(f"  [cache] {video_path.name}")
        cached           = cache[vid_hash]
        cached["path"]   = video_path
        cached["scenes"] = [tuple(s) for s in cached.get("scenes", [])]
        return cached

    print(f"  [analyse] {video_path.name}")
    audio_energy   = analyze_audio_energy(video_path)
    motion_score   = analyze_motion(video_path)
    scenes         = detect_scenes(video_path)
    speech_info    = {"speech_segments": [], "speech_activity_score": 0.0}
    if ENABLE_SPEECH_ANALYSIS and analyze_speech_activity is not None:
        try:
            speech_info = analyze_speech_activity(video_path)
        except Exception as exc:
            print(f"[warn] Speech: {exc}")
    weights        = load_feedback_weights()
    combined_score = weights["audio_weight"] * audio_energy + weights["motion_weight"] * motion_score
    result = {
        "path":                  video_path,
        "audio_energy":          audio_energy,
        "motion_score":          motion_score,
        "speech_activity_score": speech_info["speech_activity_score"],
        "speech_segments":       speech_info["speech_segments"],
        "combined_score":        combined_score,
        "scenes":                scenes,
    }
    cache[vid_hash] = {k: v for k, v in result.items() if k != "path"}
    print(f"    audio={audio_energy:.3f}  motion={motion_score:.3f}  scenes={len(scenes)}")
    return result


def analyze_clips_parallel(video_files: List[Path], cache: Dict[str, Any]) -> List[Dict[str, Any]]:
    results     = [None] * len(video_files)
    futures_map = {}
    with ThreadPoolExecutor(max_workers=MAX_ANALYSIS_WORKERS) as executor:
        for i, vf in enumerate(video_files):
            futures_map[executor.submit(analyze_clip, vf, cache)] = i
        for future in as_completed(futures_map):
            i = futures_map[future]
            try:
                results[i] = future.result()
            except Exception as exc:
                print(f"[error] {video_files[i].name}: {exc}")
                results[i] = {
                    "path": video_files[i], "audio_energy": 0.0, "motion_score": 0.0,
                    "speech_activity_score": 0.0, "speech_segments": [],
                    "combined_score": 0.0, "scenes": [],
                }
    return results


def generate_segments_from_analysis(clip_analysis: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Generate candidate segments.

    KEY FIX: when scenes=0, divide the FULL clip into sub-segments rather
    than producing just one capped chunk. 10s clip @ max_segment=2.3s → 4-5
    candidates. Previously produced 1 candidate per video with scenes=0,
    which meant 10/13 videos contributed only 1 segment each.
    """
    segments   = []
    video_path = clip_analysis["path"]
    base_score = clip_analysis["combined_score"]
    scenes     = clip_analysis["scenes"]

    if not scenes:
        duration = _get_clip_duration(video_path)
        if duration < MIN_SEGMENT_DURATION:
            return segments
        current = 0.0
        while current < duration:
            seg_end = min(current + MAX_SEGMENT_DURATION, duration)
            if seg_end - current >= MIN_SEGMENT_DURATION:
                segments.append({
                    "video_path": str(video_path),
                    "start":      round(current, 3),
                    "end":        round(seg_end, 3),
                    "score":      base_score,
                })
            current = seg_end
        return segments

    for (start, end) in scenes:
        current = start
        while current < end:
            seg_end = min(current + MAX_SEGMENT_DURATION, end)
            if seg_end - current >= MIN_SEGMENT_DURATION:
                segments.append({
                    "video_path": str(video_path),
                    "start":      round(current, 3),
                    "end":        round(seg_end, 3),
                    "score":      base_score,
                })
            current = seg_end

    return segments


# ── Fast ffmpeg render pipeline ──────────────────────────────────────────────
#
# Replaces the MoviePy per-frame pipeline which was taking 162min for 29s
# of 4K source footage. This version calls ffmpeg directly:
#   - Each segment: single ffmpeg pass (seek + scale + color grade + encode)
#   - Concat: ffmpeg concat demuxer (no re-encode)
#   - Flash transitions: 2-frame white clip injected between every 4th cut
#
# Expected render time: 3–8 min for typical 30s reels from 4K source.
#
# Color grade params read from style_profile.json (same values as before,
# now applied via ffmpeg eq + colorbalance filters instead of numpy loops).
#
# ── Quality switch ────────────────────────────────────────────────────────────
#
#   set RENDER_QUALITY=proxy     1080p  CRF 28  veryfast   ~5-10 min   ~8MB
#   set RENDER_QUALITY=normal    1080p  CRF 23  fast      ~10-15 min  ~15MB  (default)
#   set RENDER_QUALITY=high      1080p  CRF 18  slow      ~20-30 min  ~35MB
#   set RENDER_QUALITY=4k        4K     CRF 20  slow      ~30-60 min  ~120MB
#
# Manual overrides (take precedence over RENDER_QUALITY):
#   set RENDER_WIDTH=2160  set RENDER_HEIGHT=3840

# Encoding profiles: (use_source_res, width, height, crf, preset, audio_bitrate)
_QUALITY_PROFILES = {
    "proxy":  (False, 1080, 1920, "28", "veryfast", "96k"),
    "normal": (False, 1080, 1920, "23", "fast",     "128k"),
    "high":   (False, 1080, 1920, "18", "slow",     "192k"),
    "4k":     (True,     0,    0, "20", "slow",     "192k"),
    # use_source_res=True → skip downscale, encode at original 4K resolution
}
_DEFAULT_QUALITY = "proxy"  # fast iteration default; upgrade to normal/high/4k for final publish


# ── LUT preset → ffmpeg filter chain ─────────────────────────────────────────
# These approximate the CSS filter previews shown in the Review UI.
# Applied as additional filter stages after the base colour grade.
LUT_FFMPEG_FILTERS: dict[str, str] = {
    "none":     "",
    "cinema":   "eq=contrast=1.1:saturation=0.85:brightness=-0.025",
    "golden":   "eq=saturation=1.2,colorbalance=rs=0.1:gs=0.05:bs=-0.15",
    "cool":     "eq=saturation=0.9,hue=h=15",
    "fade":     "eq=contrast=0.85:saturation=0.7:brightness=0.05",
    "punch":    "eq=contrast=1.2:saturation=1.3",
    "mono":     "hue=s=0",
    "teal_org": "eq=saturation=1.3,hue=h=-15",
}


def _get_color_grade_ffmpeg_filter(color_overrides: dict | None = None) -> str:
    """
    Translate style_profile color settings into an ffmpeg eq filter string.
    Falls back to neutral values if profile is missing or empty.

    BUG FIX: previously always read from the hardcoded path
    ``style/style_profile.json``, ignoring the active template.  Now uses
    the already-loaded ``STYLE_PROFILE`` dict (which respects STYLE_TEMPLATE)
    and accepts an optional ``color_overrides`` dict so per-segment grade
    values from the review UI can be applied during render.
    """
    # Start from the active template's color section
    c = dict(STYLE_PROFILE.get("color", {}))
    # Apply per-segment overrides from the UI (brightness, contrast, etc.)
    if color_overrides:
        c.update(color_overrides)

    import numpy as _np
    brightness = float(_np.clip(c.get("brightness", 0.0) - 0.5, -0.25, 0.25))
    contrast   = float(_np.clip(0.8 + c.get("contrast",   1.0),  0.8, 1.4))
    saturation = float(_np.clip(0.8 + c.get("saturation", 1.0),  0.8, 1.5))
    warmth     = float(_np.clip(c.get("warmth", 0.0), -0.3, 0.3))

    filters = [f"eq=brightness={brightness:.4f}:contrast={contrast:.4f}:saturation={saturation:.4f}"]
    if abs(warmth) > 0.01:
        r = warmth * 0.2
        b = -warmth * 0.2
        filters.append(f"colorbalance=rs={r:.4f}:gs=0:bs={b:.4f}")
    return ",".join(filters)


def _render_segment_ffmpeg(
    video_path: Path,
    start: float,
    end: float,
    out_path: Path,
    width: int,
    height: int,
    grade_filter: str,
    fps: float = 30.0,
    crf: str = "23",
    preset: str = "fast",
    audio_br: str = "128k",
    use_source_res: bool = False,
) -> bool:
    """Render one segment to a temp file via ffmpeg. Returns True on success."""
    import subprocess
    duration = end - start

    if use_source_res:
        # 4K mode: no scaling, just color grade + ensure even dimensions
        vf = f"crop=trunc(iw/2)*2:trunc(ih/2)*2,setsar=1,{grade_filter}"
    else:
        # Proxy/normal/high: scale + letterbox/pillarbox to target resolution
        vf = (
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,"
            f"setsar=1,"
            f"{grade_filter}"
        )

    cmd = [
        "ffmpeg", "-y",
        "-ss", str(start),
        "-i", str(video_path),
        "-t", str(duration),
        "-vf", vf,
        "-r", str(fps),
        "-c:v", "libx264",
        "-preset", preset,
        "-crf", crf,
        "-c:a", "aac", "-b:a", audio_br,
        "-movflags", "+faststart",
        str(out_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  [warn] ffmpeg failed for {video_path.name}: {result.stderr[-300:]}")
        return False
    return True



def render_compilation(segments: List[Dict[str, Any]], output_path: Path):
    """
    Render the final compilation using direct ffmpeg calls.

    Quality is controlled by RENDER_QUALITY env var:
      proxy   → 1080p fast, smallest file, for quick review
      normal  → 1080p balanced (default)
      high    → 1080p high quality, larger file
      4k      → source resolution, largest file, for final publish
    """
    import subprocess, tempfile, shutil

    if not segments:
        print("[warn] No segments to render.")
        return

    # ── Resolve quality profile ───────────────────────────────────────────
    quality = os.environ.get("RENDER_QUALITY", _DEFAULT_QUALITY).lower().strip()
    if quality not in _QUALITY_PROFILES:
        print(f"  [warn] Unknown RENDER_QUALITY='{quality}', using 'normal'")
        quality = "normal"
    use_source_res, prof_w, prof_h, crf, preset, audio_br = _QUALITY_PROFILES[quality]

    # Manual width/height overrides take precedence
    env_w = int(os.environ.get("RENDER_WIDTH",  "0"))
    env_h = int(os.environ.get("RENDER_HEIGHT", "0"))
    if env_w > 0 and env_h > 0:
        use_source_res = False
        prof_w, prof_h = env_w, env_h

    # ── Detect output resolution ──────────────────────────────────────────
    if use_source_res:
        # 4K mode: probe source to get its actual resolution
        try:
            probe_cmd = [
                "ffprobe", "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=width,height",
                "-of", "csv=p=0",
                str(segments[0]["video_path"])
            ]
            result = subprocess.run(probe_cmd, capture_output=True, text=True)
            src_w, src_h = [int(x) for x in result.stdout.strip().split(",")]
            out_w = src_w + src_w % 2
            out_h = src_h + src_h % 2
        except Exception:
            print("  [warn] Could not probe source resolution, falling back to 1080p")
            use_source_res = False
            out_w, out_h = 1080, 1920
    else:
        out_w, out_h = prof_w + prof_w % 2, prof_h + prof_h % 2

    # Detect source FPS
    try:
        fps_cmd = [
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=r_frame_rate",
            "-of", "csv=p=0",
            str(segments[0]["video_path"])
        ]
        fps_str = subprocess.run(fps_cmd, capture_output=True, text=True).stdout.strip()
        num, den = fps_str.split("/")
        src_fps = round(float(num) / float(den), 3)
        out_fps = min(src_fps, 30.0)
    except Exception:
        out_fps = 30.0

    base_grade_filter = _get_color_grade_ffmpeg_filter()
    print(f"  [render] quality={quality}  {out_w}×{out_h} @ {out_fps}fps  "
          f"CRF={crf}  preset={preset}  base grade: {base_grade_filter}")

    # ── Render segments + assemble transitions ────────────────────────────
    tmp_dir = Path(tempfile.mkdtemp(prefix="reel_render_"))
    try:
        concat_list_path = tmp_dir / "concat.txt"
        n = len(segments)

        # ── Pass 1: encode each segment ───────────────────────────────────
        print(f"  [render] Encoding {n} segments...")
        seg_paths: list[Path | None] = []
        seg_durations: list[float] = []

        for i, seg in enumerate(segments):
            vp    = Path(seg["video_path"])
            # Use UI trim points when available; fall back to original timestamps.
            start = float(seg.get("trimStart", seg["start"]))
            end   = float(seg.get("trimEnd",   seg["end"]))
            seg_path = tmp_dir / f"seg_{i:03d}.mp4"

            print(f"  [{i+1:2d}/{n}] {vp.name}  {start:.2f}–{end:.2f}s  "
                  f"({end - start:.2f}s)  "
                  f"score={seg.get('style_score', seg.get('score', 0)):.3f}")

            # Apply per-segment grade values from the UI review screen.
            # UI sliders are DELTAS (0 = no change), not absolute values.
            # We must ADD them to the style profile's base values, not replace.
            seg_grade = seg.get("grade")
            if seg_grade and any(seg_grade.get(k, 0) != 0
                                 for k in ("brightness", "contrast", "saturation", "temp")):
                template_color = STYLE_PROFILE.get("color", {})
                color_overrides = {
                    "brightness": float(template_color.get("brightness", 0.0)) + seg_grade.get("brightness", 0) / 100.0,
                    "contrast":   float(template_color.get("contrast",   1.0)) + seg_grade.get("contrast",   0) / 50.0,
                    "saturation": float(template_color.get("saturation", 1.0)) + seg_grade.get("saturation", 0) / 50.0,
                    "warmth":     float(template_color.get("warmth",     0.0)) + seg_grade.get("temp",       0) / 100.0,
                }
                grade_filter = _get_color_grade_ffmpeg_filter(color_overrides)
            else:
                grade_filter = base_grade_filter

            # Append LUT filter if the user selected one in the Review UI.
            lut_name = (seg.get("grade") or {}).get("lut", "none")
            lut_vf = LUT_FFMPEG_FILTERS.get(lut_name, "")
            if lut_vf:
                grade_filter = grade_filter + "," + lut_vf

            ok = _render_segment_ffmpeg(
                vp, start, end, seg_path, out_w, out_h, grade_filter, out_fps,
                crf=crf, preset=preset, audio_br=audio_br,
                use_source_res=use_source_res,
            )
            if ok:
                seg_paths.append(seg_path)
                seg_durations.append(end - start)
            else:
                seg_paths.append(None)
                seg_durations.append(0.0)

        # ── Pass 2: apply Phase 2 xfade transitions ───────────────────────
        # For each xfade boundary, merge the previous effective clip + current
        # clip into a single xfade output.  The predecessor is then skipped in
        # the concat list (it is already included in the merged file).
        effective_paths: list[Path | None] = list(seg_paths)
        effective_durations: list[float]   = list(seg_durations)
        merged: set[int] = set()

        for i in range(1, n):
            if seg_paths[i] is None:
                continue
            transition = segments[i].get("transition_in", "cut")
            if transition not in PHASE2_TRANSITIONS:
                continue
            # Walk back to the most-recent unmerged predecessor with a valid clip.
            prev_idx = i - 1
            while prev_idx >= 0 and effective_paths[prev_idx] is None:
                prev_idx -= 1
            if prev_idx < 0 or effective_paths[prev_idx] is None:
                continue

            xfade_name, xfade_dur = XFADE_MAP[transition]
            a_path = effective_paths[prev_idx]
            b_path = seg_paths[i]
            a_dur  = effective_durations[prev_idx]
            xfade_out = tmp_dir / f"xfade_{i:03d}.mp4"

            ok = render_xfade_transition(
                a_path, b_path, xfade_out, xfade_name, xfade_dur, a_dur,
                crf=crf, preset=preset, audio_br=audio_br,
            )
            if ok:
                effective_paths[i]     = xfade_out
                effective_durations[i] = a_dur + effective_durations[i] - xfade_dur
                merged.add(prev_idx)
                print(f"  [transition] {transition}  seg {prev_idx} → {i}")
            else:
                print(f"  [warn] xfade failed for seg {i}; falling back to cut")

        # ── Pass 3: build concat list ─────────────────────────────────────
        # Phase 1 transitions insert a short clip *before* the current segment.
        # Phase 2 transitions were handled above (prev segment merged into xfade).
        concat_lines: list[str] = []

        for i in range(n):
            if i in merged:
                continue
            ep = effective_paths[i]
            if ep is None:
                continue

            if i > 0:
                transition = segments[i].get("transition_in", "cut")
                if transition in PHASE1_TRANSITIONS and transition not in ("cut", "jump_cut"):
                    trans_path = tmp_dir / f"trans_{i:03d}.mp4"
                    if render_phase1_transition(transition, trans_path, out_w, out_h, out_fps):
                        # as_posix() required by ffmpeg concat demuxer on Windows
                        concat_lines.append(f"file '{trans_path.as_posix()}'\n")

            # as_posix() converts Windows backslashes to forward slashes
            concat_lines.append(f"file '{ep.as_posix()}'\n")

        if not concat_lines:
            print("[warn] No segments rendered successfully.")
            return

        # ── Concatenate all segments ──────────────────────────────────────
        # Write with forward-slash paths and explicit UTF-8 encoding
        concat_list_path.write_text("".join(concat_lines), encoding="utf-8")
        # Debug: print first few lines so we can verify paths look right
        preview = "".join(concat_lines[:3])
        print(f"  [render] concat.txt preview: {repr(preview[:120])}")
        print(f"  [render] Concatenating {len(concat_lines)} clips...")

        concat_cmd = [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0",
            "-i", str(concat_list_path),
            "-c", "copy",           # no re-encode — just remux
            "-movflags", "+faststart",
            str(output_path),
        ]
        result = subprocess.run(concat_cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"  [error] Concat failed: {result.stderr[-500:]}")
            return

        size_mb = output_path.stat().st_size / 1_000_000
        print(f"  [render] ✓ {output_path.name}  ({size_mb:.1f} MB)")

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ── LM Studio warmup ──────────────────────────────────────────────────────────

_LLM_AVAILABLE = False   # module-level flag set by warmup, read by plan_edit caller

def warmup_lm_studio(timeout: int = 10) -> bool:
    """
    Send a minimal ping to LM Studio before the pipeline starts.
    Sets the module-level _LLM_AVAILABLE flag so Step 4 can skip
    gracefully if the server is not responding.

    Uses a tiny prompt so the model loads into memory now rather than
    cold-starting during Step 4 after Moondream has been running for minutes.
    """
    global _LLM_AVAILABLE
    if os.environ.get("ENABLE_LLM_PLANNER", "1") != "1":
        print("[llm] LLM planner disabled — skipping warmup")
        _LLM_AVAILABLE = False
        return False

    import requests
    base_url = "http://localhost:1234"

    # BUG FIX: the hardcoded model name "lmstudio-model" is invalid in LM Studio
    # and causes a 400 Bad Request.  Query /v1/models first to discover whatever
    # model is currently loaded, then use that name for the warmup ping.
    # Substrings that identify non-text models (vision, embedding) — must be
    # skipped so the warmup pings the actual text LLM, not Moondream.
    _SKIP = ("moondream", "embed", "nomic", "mmproj", "whisper", "clip")

    try:
        models_resp = requests.get(f"{base_url}/v1/models", timeout=timeout)
        models_resp.raise_for_status()
        models_data = models_resp.json()
        all_models  = models_data.get("data", [])
        # Honour explicit override first
        override = os.environ.get("LLM_PLANNER_MODEL", "").strip()
        if override:
            model_id = override
        else:
            text_models = [m["id"] for m in all_models
                           if not any(p in m.get("id","").lower() for p in _SKIP)]
            model_id = text_models[0] if text_models else (all_models[0]["id"] if all_models else "lmstudio-model")
    except requests.exceptions.ConnectionError:
        print("[llm] LM Studio not reachable on localhost:1234 — LLM planner will be skipped")
        _LLM_AVAILABLE = False
        return False
    except requests.exceptions.Timeout:
        print(f"[llm] LM Studio did not respond within {timeout}s — LLM planner will be skipped")
        _LLM_AVAILABLE = False
        return False
    except Exception as exc:
        print(f"[llm] LM Studio warmup failed: {exc} — LLM planner will be skipped")
        _LLM_AVAILABLE = False
        return False

    try:
        resp = requests.post(f"{base_url}/v1/chat/completions", json={
            "model":       model_id,
            "messages":    [{"role": "user", "content": "hi"}],
            "max_tokens":  1,
            "temperature": 0.0,
        }, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        if "choices" in data and data["choices"]:
            print(f"[llm] LM Studio ready ✓  model={model_id!r}")
            _LLM_AVAILABLE = True
            return True
        else:
            print(f"[llm] LM Studio responded but returned unexpected format: {list(data.keys())}")
            _LLM_AVAILABLE = False
            return False
    except requests.exceptions.ConnectionError:
        print("[llm] LM Studio not reachable on localhost:1234 — LLM planner will be skipped")
        _LLM_AVAILABLE = False
        return False
    except requests.exceptions.Timeout:
        print(f"[llm] LM Studio did not respond within {timeout}s — LLM planner will be skipped")
        _LLM_AVAILABLE = False
        return False
    except Exception as exc:
        print(f"[llm] LM Studio warmup failed: {exc} — LLM planner will be skipped")
        _LLM_AVAILABLE = False
        return False


def main():
    t0          = datetime.now()
    video_files = get_video_files()
    if not video_files:
        print("No video files found in raw_clips/")
        return

    print(f"\n[info] Found {len(video_files)} videos\n")

    # Warm up LM Studio now so it's hot by the time Step 4 runs
    # (avoids cold-start timeout after Moondream has been running for minutes)
    warmup_lm_studio(timeout=15)

    cache        = _load_cache()
    cached_count = sum(1 for vf in video_files if _video_hash(vf) in cache)
    print(f"[info] Cache: {cached_count}/{len(video_files)} already analysed\n")

    print("-- Step 1/4: Video analysis (parallel) --")
    t1       = datetime.now()
    analyses = analyze_clips_parallel(video_files, cache)
    _save_cache(cache)
    print(f"  ✓ {(datetime.now()-t1).total_seconds():.1f}s\n")

    run_log = {
        "timestamp": t0.isoformat(), "template": _TEMPLATE_NAME,
        "settings": {"target_duration": TARGET_DURATION_SECONDS,
                     "min_segment": MIN_SEGMENT_DURATION,
                     "max_segment": MAX_SEGMENT_DURATION,
                     "blur_threshold": BLUR_THRESHOLD},
        "clips": [], "segments_generated": [], "segments_selected": [],
    }

    print("-- Step 2/4: Generating segments --")
    t2           = datetime.now()
    all_segments = []
    for analysis in analyses:
        run_log["clips"].append({
            "file": str(analysis["path"]),
            "audio_energy": analysis["audio_energy"],
            "motion_score": analysis["motion_score"],
            "scenes": analysis["scenes"],
        })
        segs = generate_segments_from_analysis(analysis)
        all_segments.extend(segs)
    print(f"[info] Total segments: {len(all_segments)}")
    print(f"  ✓ {(datetime.now()-t2).total_seconds():.1f}s\n")

    if not all_segments:
        print("No segments generated — aborting.")
        return

    print("-- Step 3/4: Enriching segments (batched GPU) --")
    t3             = datetime.now()
    profile_w_blur = {**STYLE_PROFILE, "blur_threshold": BLUR_THRESHOLD}
    segments       = enrich_segments_with_siglip(all_segments, style_profile=profile_w_blur)
    print(f"  ✓ {(datetime.now()-t3).total_seconds():.1f}s\n")

    # Logging — print score table + write CSV
    logger = PipelineLogger(_TEMPLATE_NAME, LOGS_DIR)
    logger.log_segments_after_enrichment(segments)

    print("-- Step 4/4: Edit planning --")
    t4 = datetime.now()
    # If warmup found LLM unavailable, disable planner for this run
    if not _LLM_AVAILABLE:
        os.environ["ENABLE_LLM_PLANNER"] = "0"
    plan_target       = float(STYLE_PROFILE.get("duration_preferences", {}).get("target_total", TARGET_DURATION_SECONDS))
    selected_segments = plan_edit(segments, style_profile=STYLE_PROFILE, target_duration=plan_target)
    print(f"  ✓ {(datetime.now()-t4).total_seconds():.1f}s\n")

    if not selected_segments:
        print("Editing brain returned no segments — aborting.")
        return

    logger.log_selected_segments(selected_segments, segments)

    total_duration = sum(seg["end"] - seg["start"] for seg in selected_segments)
    elapsed        = (datetime.now() - t0).total_seconds()
    print(f"[info] Selected {len(selected_segments)} segments — {total_duration:.1f}s total")
    print(f"[info] Analysis + planning: {elapsed:.0f}s ({elapsed/60:.1f} min)")

    for seg in selected_segments:
        run_log["segments_selected"].append({
            "video_path":  seg["video_path"],
            "start":       seg["start"], "end": seg["end"],
            "style_score": seg.get("style_score"),
            "tags":        seg.get("tags", []),
        })
    run_log["final_duration"]  = total_duration
    run_log["elapsed_seconds"] = elapsed
    logger.log_run_meta({
        "elapsed_s":       elapsed,
        "segments_total":  len(segments),
        "segments_selected": len(selected_segments),
        "output_duration": total_duration,
        "template":        _TEMPLATE_NAME,
    })
    logger.write_files()

    log_path = write_log(run_log)
    append_to_history({"timestamp": t0.isoformat(), "template": _TEMPLATE_NAME,
                       "final_duration": total_duration, "num_segments": len(selected_segments),
                       "elapsed_s": elapsed})
    print(f"[info] Log: {log_path}\n")

    output_path = OUTPUT_DIR / f"{_TEMPLATE_NAME}_compilation.mp4"
    print(f"-- Rendering -> {output_path} --")
    render_compilation(selected_segments, output_path)

    total_elapsed = (datetime.now() - t0).total_seconds()
    print(f"\n✓ Done in {total_elapsed:.0f}s ({total_elapsed/60:.1f} min) — {output_path}")


def render_only(seg_file: Path):
    """
    --render-only mode: load pre-reviewed segments from JSON and render.
    Called by the Flask UI after the user accepts/rejects in the browser.
    """
    segments = json.loads(seg_file.read_text(encoding="utf-8"))
    if not segments:
        print("[error] No segments in file")
        return
    output_path = OUTPUT_DIR / f"{_TEMPLATE_NAME}_compilation.mp4"
    print(f"-- Rendering {len(segments)} segments -> {output_path} --")
    render_compilation(segments, output_path)
    total = sum(float(s["end"]) - float(s["start"]) for s in segments)
    print(f"✓ Done — {output_path}")
    # Signal output path back to the Flask server
    print(f"<<OUTPUT_PATH>>{output_path}")


def main_ui_mode():
    """
    --ui-mode: run Steps 1–4 then emit selected segments as JSON
    so the Flask UI can present them for review before rendering.
    """
    import io, contextlib

    t0          = datetime.now()
    video_files = get_video_files()
    if not video_files:
        print("No video files found in raw_clips/")
        return

    print(f"\n[info] Found {len(video_files)} videos\n")
    warmup_lm_studio(timeout=15)
    cache        = _load_cache()
    cached_count = sum(1 for vf in video_files if _video_hash(vf) in cache)
    print(f"[info] Cache: {cached_count}/{len(video_files)} already analysed\n")

    print("-- Step 1/4: Video analysis (parallel) --")
    t1       = datetime.now()
    analyses = analyze_clips_parallel(video_files, cache)
    _save_cache(cache)
    print(f"  ✓ {(datetime.now()-t1).total_seconds():.1f}s\n")

    print("-- Step 2/4: Generating segments --")
    t2           = datetime.now()
    all_segments = []
    for analysis in analyses:
        all_segments.extend(generate_segments_from_analysis(analysis))
    print(f"[info] Total segments: {len(all_segments)}")
    print(f"  ✓ {(datetime.now()-t2).total_seconds():.1f}s\n")

    if not all_segments:
        print("No segments generated — aborting.")
        return

    print("-- Step 3/4: Enriching segments (batched GPU) --")
    t3             = datetime.now()
    profile_w_blur = {**STYLE_PROFILE, "blur_threshold": BLUR_THRESHOLD}
    segments       = enrich_segments_with_siglip(all_segments, style_profile=profile_w_blur)
    print(f"  ✓ {(datetime.now()-t3).total_seconds():.1f}s\n")

    logger = PipelineLogger(_TEMPLATE_NAME, LOGS_DIR)
    logger.log_segments_after_enrichment(segments)

    print("-- Step 4/4: Edit planning --")
    t4 = datetime.now()
    if not _LLM_AVAILABLE:
        os.environ["ENABLE_LLM_PLANNER"] = "0"
    plan_target       = float(STYLE_PROFILE.get("duration_preferences", {}).get("target_total", TARGET_DURATION_SECONDS))
    selected_segments = plan_edit(segments, style_profile=STYLE_PROFILE, target_duration=plan_target)
    print(f"  ✓ {(datetime.now()-t4).total_seconds():.1f}s\n")

    if not selected_segments:
        print("Editing brain returned no segments — aborting.")
        return

    total_duration = sum(seg["end"] - seg["start"] for seg in selected_segments)
    elapsed        = (datetime.now() - t0).total_seconds()
    print(f"[info] Selected {len(selected_segments)} segments — {total_duration:.1f}s total")
    print(f"[info] Analysis + planning: {elapsed:.0f}s ({elapsed/60:.1f} min)")

    logger.log_selected_segments(selected_segments, segments)
    logger.log_run_meta({
        "elapsed_s":         elapsed,
        "segments_total":    len(segments),
        "segments_selected": len(selected_segments),
        "output_duration":   total_duration,
        "template":          _TEMPLATE_NAME,
    })
    logger.write_files()

    # Serialise segments for the UI review screen
    # video_path is a Path object — convert to string for JSON
    serialisable = []
    for seg in selected_segments:
        s = {k: (str(v) if isinstance(v, Path) else v) for k, v in seg.items()}
        # BUG FIX: was using dead boolean flags (is_dark, is_outdoor, …) that
        # are never set anywhere in the pipeline, so combined_tags was always [].
        # Use the actual AI vision tags stored in seg["tags"] instead.
        s["combined_tags"] = s.get("tags", [])
        serialisable.append(s)

    try:
        print("<<SEGMENTS_JSON_START>>", flush=True)
        print(json.dumps(serialisable), flush=True)
        print("<<SEGMENTS_JSON_END>>", flush=True)
    except Exception as _e:
        print(f"[error] Failed to serialise segments for UI: {_e}", flush=True)

    # PyTorch / Hugging Face teardown keeps the Python interpreter alive for
    # minutes on Windows after main_ui_mode() returns, holding stdout open.
    # Flask's _run_pipeline reads proc.stdout until EOF — so it never captures
    # the segments and never transitions phase to "reviewing".
    # All important work (logger, cache) is already done above, so force-exit
    # immediately to signal EOF to Flask without waiting for model GC.
    import os as _os
    _os._exit(0)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--ui-mode",     action="store_true",
                        help="Run Steps 1-4 and emit segments JSON for UI review")
    parser.add_argument("--render-only", metavar="SEG_FILE",
                        help="Render pre-reviewed segments from JSON file")
    args = parser.parse_args()

    if args.ui_mode:
        main_ui_mode()
    elif args.render_only:
        render_only(Path(args.render_only))
    else:
        main()
