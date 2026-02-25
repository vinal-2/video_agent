import json
from pathlib import Path
from statistics import median

import cv2
import numpy as np
from scenedetect import VideoManager, SceneManager
from scenedetect.detectors import ContentDetector
from moviepy import VideoFileClip

BASE_DIR = Path(__file__).resolve().parent.parent
REF_DIR = BASE_DIR / "reference_ig"
STYLE_DIR = BASE_DIR / "style"
STYLE_DIR.mkdir(exist_ok=True)
STYLE_PATH = STYLE_DIR / "style_profile.json"


def list_reference_videos():
    vids = []
    for f in sorted(REF_DIR.iterdir()):
        if f.suffix.lower() in [".mp4", ".mov", ".mkv"]:
            vids.append(f)
    return vids


def is_hero(path: Path) -> bool:
    return "hero" in path.stem.lower()


def detect_cuts(video_path: Path, threshold: float = 27.0):
    video_manager = VideoManager([str(video_path)])
    scene_manager = SceneManager()
    scene_manager.add_detector(ContentDetector(threshold=threshold))

    video_manager.start()
    scene_manager.detect_scenes(frame_source=video_manager)
    scene_list = scene_manager.get_scene_list()
    video_manager.release()

    cuts = []
    for start, end in scene_list:
        cuts.append((start.get_seconds(), end.get_seconds()))
    return cuts


def sample_frames(video_path: Path, step: float = 0.5, max_frames: int = 400):
    clip = VideoFileClip(str(video_path))
    duration = clip.duration
    frames = []
    t = 0.0
    count = 0
    while t < duration and count < max_frames:
        frame = clip.get_frame(t)
        frames.append(frame)
        t += step
        count += 1
    clip.close()
    return frames, duration


def compute_color_stats(frames):
    if not frames:
        return None

    brightness_vals = []
    contrast_vals = []
    saturation_vals = []
    warmth_vals = []
    shadow_lift_vals = []
    highlight_rolloff_vals = []

    for frame in frames:
        img = frame.astype(np.float32) / 255.0

        # Brightness: mean of V channel in HSV
        hsv = cv2.cvtColor((img * 255).astype(np.uint8), cv2.COLOR_RGB2HSV)
        v = hsv[:, :, 2].astype(np.float32) / 255.0
        brightness_vals.append(float(v.mean()))

        # Contrast: std of luminance (Y from YCrCb)
        ycrcb = cv2.cvtColor((img * 255).astype(np.uint8), cv2.COLOR_RGB2YCrCb)
        y = ycrcb[:, :, 0].astype(np.float32) / 255.0
        contrast_vals.append(float(y.std()))

        # Saturation: mean of S channel
        s = hsv[:, :, 1].astype(np.float32) / 255.0
        saturation_vals.append(float(s.mean()))

        # Warmth: mean(R - B)
        r = img[:, :, 0]
        g = img[:, :, 1]
        b = img[:, :, 2]
        warmth_vals.append(float((r - b).mean()))

        # Shadow lift: proportion of pixels in low luminance but not crushed
        shadow_mask = (y < 0.25).astype(np.float32)
        shadow_lift_vals.append(float(shadow_mask.mean()))

        # Highlight rolloff: proportion of pixels in high luminance but not clipped
        highlight_mask = (y > 0.75).astype(np.float32)
        highlight_rolloff_vals.append(float(highlight_mask.mean()))

    def avg(xs):
        return float(np.mean(xs)) if xs else 0.0

    return {
        "brightness": avg(brightness_vals),
        "contrast": avg(contrast_vals),
        "saturation": avg(saturation_vals),
        "warmth": avg(warmth_vals),
        "shadow_lift": avg(shadow_lift_vals),
        "highlight_rolloff": avg(highlight_rolloff_vals),
    }


def compute_pacing_stats(cuts, duration):
    if not cuts or duration <= 0:
        return {
            "median_cut": None,
            "p10": None,
            "p90": None,
            "start_fast": False,
            "end_slow": False,
        }

    lengths = [end - start for start, end in cuts if end > start]
    if not lengths:
        return {
            "median_cut": None,
            "p10": None,
            "p90": None,
            "start_fast": False,
            "end_slow": False,
        }

    lengths_sorted = sorted(lengths)
    n = len(lengths_sorted)

    def percentile(p):
        idx = int(p * (n - 1))
        return float(lengths_sorted[idx])

    med = float(median(lengths_sorted))
    p10 = percentile(0.1)
    p90 = percentile(0.9)

    # Rough pacing curve: compare early vs late average cut length
    thirds = duration / 3.0
    early = [end - start for start, end in cuts if start < thirds]
    late = [end - start for start, end in cuts if start > 2 * thirds]

    early_avg = float(np.mean(early)) if early else med
    late_avg = float(np.mean(late)) if late else med

    start_fast = early_avg < med
    end_slow = late_avg > med

    return {
        "median_cut": med,
        "p10": p10,
        "p90": p90,
        "start_fast": start_fast,
        "end_slow": end_slow,
    }


def weighted_average_dict(dicts, weights):
    keys = dicts[0].keys()
    out = {}
    total_w = sum(weights)
    for k in keys:
        vals = []
        for d, w in zip(dicts, weights):
            v = d.get(k)
            if v is not None:
                vals.append((v, w))
        if not vals:
            out[k] = None
        else:
            num = sum(v * w for v, w in vals)
            den = sum(w for _, w in vals)
            out[k] = float(num / den) if den > 0 else None
    return out


def main():
    vids = list_reference_videos()
    if not vids:
        print("No reference videos found in reference_ig/")
        return

    color_stats_list = []
    pacing_stats_list = []
    weights = []

    hero_name = None

    for v in vids:
        w = 3.0 if is_hero(v) else 1.0
        if is_hero(v):
            hero_name = v.name
        print(f"Analyzing style from {v.name} (weight={w})")

        frames, duration = sample_frames(v, step=0.5, max_frames=400)
        color_stats = compute_color_stats(frames)
        cuts = detect_cuts(v)
        pacing_stats = compute_pacing_stats(cuts, duration)

        if color_stats is not None:
            color_stats_list.append(color_stats)
            pacing_stats_list.append(pacing_stats)
            weights.append(w)

    if not color_stats_list:
        print("No color stats computed; style profile not created.")
        return

    color_profile = weighted_average_dict(color_stats_list, weights)
    pacing_profile = weighted_average_dict(pacing_stats_list, weights)

    style_profile = {
        "color": color_profile,
        "pacing": pacing_profile,
        "metadata": {
            "hero_video": hero_name,
            "num_reference_videos": len(vids),
        },
    }

    with open(STYLE_PATH, "w") as f:
        json.dump(style_profile, f, indent=4)

    print(f"Style profile written to: {STYLE_PATH}")


if __name__ == "__main__":
    main()
