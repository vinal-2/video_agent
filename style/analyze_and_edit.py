import os
from pathlib import Path
from datetime import datetime
import json

import numpy as np
import librosa
import cv2
from scenedetect import VideoManager, SceneManager
from scenedetect.detectors import ContentDetector
from moviepy import VideoFileClip, concatenate_videoclips
from scripts.color_grade import apply_color_grade
from scripts.transitions import insert_transitions_between_clips
from scripts.editing_brain_old import plan_edit
from scripts.semantic_siglip_old import enrich_segments_with_siglip
import ffmpeg
from scripts.archive.semantic_tags import enrich_segments_with_semantics


# Optional Whisper helper (GPU-accelerated faster-whisper).
# Default disabled because faster-whisper currently crashes on this Windows setup.
ENABLE_SPEECH_ANALYSIS = os.environ.get("ENABLE_SPEECH_ANALYSIS", "0") == "1"
if ENABLE_SPEECH_ANALYSIS:
    try:
        from scripts.whisper_helper import analyze_speech_activity
    except ModuleNotFoundError:
        # Fallback for Windows path resolution issues
        import sys
        sys.path.append(str(Path(__file__).resolve().parent))
        from whisper_helper import analyze_speech_activity
    except Exception as exc:
        print(f"[warn] Cannot import whisper helper, disabling speech analysis: {exc}")
        ENABLE_SPEECH_ANALYSIS = False
        analyze_speech_activity = None
else:
    analyze_speech_activity = None

# ---- PATHS ----
BASE_DIR = Path(__file__).resolve().parent.parent
RAW_DIR = BASE_DIR / "raw_clips"
ANALYSIS_DIR = BASE_DIR / "analysis"
OUTPUT_DIR = BASE_DIR / "output"
LOGS_DIR = BASE_DIR / "logs"
STYLE_PROFILES_DIR = BASE_DIR / "style_profiles"
STYLE_PROFILE_FILE = STYLE_PROFILES_DIR / "warm_travel_reels.json"

ANALYSIS_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)

# ---- SETTINGS ----
TARGET_DURATION_SECONDS = 90  # aim for ~1.5 minutes for now
MIN_SEGMENT_DURATION = 1.0    # seconds
MAX_SEGMENT_DURATION = 4.0    # seconds


def _load_style_profile():
    search_paths = [
        STYLE_PROFILE_FILE,                             # style_profiles/warm_travel_reels.json
        BASE_DIR / "style" / "warm_travel_reels.json",  # style/warm_travel_reels.json
        BASE_DIR / "style" / "style_profile.json",      # fallback: legacy style profile
    ]
    for path in search_paths:
        if not path.exists():
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                profile = json.load(f)
        except json.JSONDecodeError as exc:
            print(f"[warn] Style profile invalid at {path} ({exc}); continuing fallback.")
            continue

        emb = profile.get("siglip_style_embedding")
        if emb is None or len(emb) != 768:
            print(f"[warn] Style profile at {path} missing 768-dim SigLIP embedding; continuing fallback.")
            continue

        return profile

    print(f"[warn] Style profile not found; using defaults.")
    return {"duration_preferences": {"target_total": TARGET_DURATION_SECONDS}}


STYLE_PROFILE = _load_style_profile()



# ---------- LOGGING / FEEDBACK ----------

def write_log(data, filename=None):
    """Write a JSON log file for this run."""
    if filename is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"log_{timestamp}.json"
    log_path = LOGS_DIR / filename
    with open(log_path, "w") as f:
        json.dump(data, f, indent=4)
    return log_path


def append_to_history(data):
    """Append summary data to a master history file (JSONL)."""
    history_path = LOGS_DIR / "history.jsonl"
    with open(history_path, "a") as f:
        f.write(json.dumps(data) + "\n")


def load_feedback_weights():
    """
    Load scoring weights from history.
    For now, returns defaults; later you can compute stats from history.jsonl.
    """
    history_path = LOGS_DIR / "history.jsonl"
    if not history_path.exists():
        return {"audio_weight": 0.5, "motion_weight": 0.5}

    # Placeholder for future learning logic
    return {"audio_weight": 0.5, "motion_weight": 0.5}


# ---------- CORE HELPERS ----------

def get_video_files():
    video_files = []
    for f in sorted(RAW_DIR.iterdir()):
        if f.suffix.lower() in [".mp4", ".mov", ".mkv"]:
            video_files.append(f)
    return video_files


# ---------- ANALYSIS FUNCTIONS ----------

def analyze_audio_energy(video_path):
    """
    Extract audio using ffmpeg-python (MoviePy 2.x is unreliable for audio export),
    then compute normalized frame-based energy.
    """
    temp_audio_path = ANALYSIS_DIR / f"{video_path.stem}_temp_audio.wav"

    # Extract audio using ffmpeg
    try:
        (
            ffmpeg
            .input(str(video_path))
            .output(str(temp_audio_path), ac=1, ar=44100, format='wav')
            .overwrite_output()
            .run(quiet=True)
        )
    except Exception as e:
        print(f"FFmpeg audio extraction failed for {video_path}: {e}")
        return 0.0

    # Now load with librosa
    try:
        y, sr = librosa.load(str(temp_audio_path), sr=None)
    except Exception as e:
        print(f"Librosa failed to load audio for {video_path}: {e}")
        return 0.0
    finally:
        temp_audio_path.unlink(missing_ok=True)

    if len(y) == 0:
        return 0.0

    frame_length = 2048
    hop_length = 512
    energy = np.array([
        np.sum(np.abs(y[i:i+frame_length]**2))
        for i in range(0, len(y), hop_length)
    ])

    if len(energy) == 0:
        return 0.0

    energy_norm = (energy - energy.min()) / (energy.max() - energy.min() + 1e-9)
    return float(np.mean(energy_norm))

def analyze_motion(video_path, sample_stride=5):
    """
    Compute a simple motion score using frame differencing.
    Higher score = more visual movement.
    """
    cap = cv2.VideoCapture(str(video_path))
    ret, prev = cap.read()
    if not ret:
        cap.release()
        return 0.0

    prev_gray = cv2.cvtColor(prev, cv2.COLOR_BGR2GRAY)
    motion_values = []
    frame_idx = 0

    while True:
        ret, frame = cap.read()
        if not ret:
            break
        frame_idx += 1
        if frame_idx % sample_stride != 0:
            continue

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        diff = cv2.absdiff(prev_gray, gray)
        motion = diff.mean()
        motion_values.append(motion)
        prev_gray = gray

    cap.release()

    if not motion_values:
        return 0.0

    motion_arr = np.array(motion_values)
    motion_norm = (motion_arr - motion_arr.min()) / (motion_arr.max() - motion_arr.min() + 1e-9)
    avg_motion = float(np.mean(motion_norm))
    return avg_motion


def detect_scenes(video_path, threshold=27.0):
    """
    Use PySceneDetect to find scene boundaries.
    Returns a list of (start_sec, end_sec) tuples.
    """
    video_manager = VideoManager([str(video_path)])
    scene_manager = SceneManager()
    scene_manager.add_detector(ContentDetector(threshold=threshold))

    video_manager.start()
    scene_manager.detect_scenes(frame_source=video_manager)
    scene_list = scene_manager.get_scene_list()

    scenes_seconds = []
    for start, end in scene_list:
        start_sec = start.get_seconds()
        end_sec = end.get_seconds()
        if end_sec - start_sec >= MIN_SEGMENT_DURATION:
            scenes_seconds.append((start_sec, end_sec))

    video_manager.release()
    return scenes_seconds


def analyze_clip(video_path):
    """
    Run audio, motion, scene, and speech analysis on a single clip.
    """
    print(f"Analyzing {video_path.name}...")
    
    audio_energy = analyze_audio_energy(video_path)
    motion_score = analyze_motion(video_path)
    scenes = detect_scenes(video_path)

    # Whisper speech activity (GPU accelerated) - optional.
    if ENABLE_SPEECH_ANALYSIS and analyze_speech_activity is not None:
        try:
            speech_info = analyze_speech_activity(
                video_path,
                model_size="small",
                device="auto",
                compute_type="int8_float16",
            )
        except Exception as exc:
            print(f"[warn] Speech analysis failed for {video_path.name}: {exc}")
            speech_info = {
                "speech_segments": [],
                "speech_activity_score": 0.0,
            }
    else:
        speech_info = {
            "speech_segments": [],
            "speech_activity_score": 0.0,
        }

    speech_activity_score = speech_info["speech_activity_score"]

    weights = load_feedback_weights()
    combined_score = (
        weights["audio_weight"] * audio_energy +
        weights["motion_weight"] * motion_score
    )
    print("  Audio energy:", audio_energy)
    print("  Motion score:", motion_score)
    print("  Speech activity:", speech_activity_score)
    print("  Scenes detected:", len(scenes))
    
    return {
        "path": video_path,
        "audio_energy": audio_energy,
        "motion_score": motion_score,
        "speech_activity_score": speech_activity_score,
        "speech_segments": speech_info["speech_segments"],
        "combined_score": combined_score,
        "scenes": scenes,
    }


# ---------- SEGMENT GENERATION / SELECTION ----------

def generate_segments_from_analysis(clip_analysis):
    """
    Turn clip-level analysis into candidate segments with scores.
    """
    segments = []
    video_path = clip_analysis["path"]
    base_score = clip_analysis["combined_score"]
    scenes = clip_analysis["scenes"]

    if not scenes:
        clip = VideoFileClip(str(video_path))
        duration = clip.duration
        if duration >= MIN_SEGMENT_DURATION:
            segments.append({
                #"video_path": video_path,
                "video_path": str(video_path),
                "start": 0.0,
                "end": min(duration, MAX_SEGMENT_DURATION),
                "score": base_score,
            })
        clip.close()
        return segments

    for (start, end) in scenes:
        seg_duration = end - start
        if seg_duration < MIN_SEGMENT_DURATION:
            continue

        current = start
        while current < end:
            seg_end = min(current + MAX_SEGMENT_DURATION, end)
            seg_dur = seg_end - current
            if seg_dur >= MIN_SEGMENT_DURATION:
                segments.append({
                    #"video_path": video_path,
                    "video_path": str(video_path),
                    "start": current,
                    "end": seg_end,
                    "score": base_score,
                })
            current = seg_end

    return segments


def select_top_segments(all_segments, target_duration=TARGET_DURATION_SECONDS):
    """
    Sort segments by score and pick the best ones until we hit target duration.
    """
    sorted_segments = sorted(all_segments, key=lambda s: s["score"], reverse=True)

    selected = []
    total = 0.0

    for seg in sorted_segments:
        seg_duration = seg["end"] - seg["start"]
        if total + seg_duration > target_duration:
            remaining = target_duration - total
            if remaining >= MIN_SEGMENT_DURATION:
                trimmed = seg.copy()
                trimmed["end"] = trimmed["start"] + remaining
                selected.append(trimmed)
                total += remaining
            break
        else:
            selected.append(seg)
            total += seg_duration

    return selected, total


# ---------- RENDERING ----------
def render_compilation(segments, output_path):
    """
    Build the final rough compilation from selected segments.
    Apply Hero-driven color grading to each segment.
    Insert simple, tasteful transitions between clips.
    """
    clips = []
    for seg in segments:
        vp = Path(seg["video_path"])
        start = seg["start"]
        end = seg["end"]

        base_clip = VideoFileClip(str(vp))
        duration = base_clip.duration
        safe_end = min(end, duration - 0.001)

        print(f"  Using {vp.name} [{start:.2f}s - {safe_end:.2f}s] (score={seg['score']:.3f})")

        working = base_clip.subclipped(start, safe_end)
        graded = apply_color_grade(working).without_mask()
        clips.append(graded)

    if not clips:
        print("No clips selected, nothing to render.")
        return

    # Insert transitions between graded clips
    timeline_clips = insert_transitions_between_clips(clips, segments)

    # method="chain" keeps MoviePy from wrapping clips in CompositeVideoClip (saves RAM)
    final = concatenate_videoclips(timeline_clips, method="chain")

    final.write_videofile(str(output_path), codec="libx264", audio_codec="aac")

    final.close()
    for c in timeline_clips:
        c.close()
# ---------- MAIN PIPELINE ----------

def main():
    video_files = get_video_files()
    if not video_files:
        print("No video files found in raw_clips/")
        return

    run_log = {
        "timestamp": datetime.now().isoformat(),
        "settings": {
            "target_duration": TARGET_DURATION_SECONDS,
            "min_segment_duration": MIN_SEGMENT_DURATION,
            "max_segment_duration": MAX_SEGMENT_DURATION,
        },
        "clips": [],
        "segments_generated": [],
        "segments_selected": [],
    }

    all_segments = []

    # Analyze each clip
    for vf in video_files:
        analysis = analyze_clip(vf)

        run_log["clips"].append({
            "file": str(vf),
            "audio_energy": analysis["audio_energy"],
            "motion_score": analysis["motion_score"],
            "speech_activity_score": analysis["speech_activity_score"],
            "speech_segments": analysis["speech_segments"],
            "combined_score": analysis["combined_score"],
            "scenes": analysis["scenes"],
        })

        segments = generate_segments_from_analysis(analysis)
        all_segments.extend(segments)

        for seg in segments:
            run_log["segments_generated"].append({
                "video_path": str(seg["video_path"]),
                "start": seg["start"],
                "end": seg["end"],
                "score": seg["score"],
            })

    print(f"Total segments generated: {len(all_segments)}")
    
    if not all_segments:
        print("No segments generated; aborting render.")
        return

    segments = enrich_segments_with_siglip(all_segments, style_profile=STYLE_PROFILE)

    duration_prefs = STYLE_PROFILE.get("duration_preferences", {})
    plan_target = float(duration_prefs.get("target_total", TARGET_DURATION_SECONDS))

    selected_segments = plan_edit(
        segments,
        style_profile=STYLE_PROFILE,
        target_duration=plan_target,
    )
    
    if not selected_segments:
        print("Editing brain returned no segments; aborting render.")
        return

    total_duration = sum((seg["end"] - seg["start"]) for seg in selected_segments)
    print(f"Selected {len(selected_segments)} segments, total duration ~{total_duration:.1f}s")

    for seg in selected_segments:
        run_log["segments_selected"].append({
            "video_path": str(seg["video_path"]),
            "start": seg["start"],
            "end": seg["end"],
            "score": seg["score"],
            "style_score": seg.get("style_score"),
        })

    run_log["final_duration"] = total_duration

    # Write logs
    log_path = write_log(run_log)
    append_to_history({
        "timestamp": run_log["timestamp"],
        "final_duration": total_duration,
        "num_segments": len(selected_segments),
        "avg_score": float(np.mean([s.get("style_score", s.get("score", 0.0)) for s in selected_segments])),
    })
    print(f"Log written to: {log_path}")

    # Render video
    output_path = OUTPUT_DIR / "event_compilation_rough.mp4"
    render_compilation(selected_segments, output_path)
    print(f"Done. Output saved to: {output_path}")


if __name__ == "__main__":
    main()


