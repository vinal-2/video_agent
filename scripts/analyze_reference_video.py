"""
analyze_reference_video.py
--------------------------
Run this on a single reference Instagram reel to produce a ground-truth
analysis JSON. Paste the output into your chat so the pipeline can be
calibrated to what your reference video actually contains.

Usage:
    python analyze_reference_video.py --video "path/to/your/reel.mp4"

Output:
    Prints a JSON block to console AND saves to: ground_truth_analysis.json
    Paste the JSON output into your chat for pipeline calibration.

Requirements (all already in your pipeline):
    pip install opencv-python numpy torch transformers tqdm
    pip install open_clip_torch   (for aesthetic scoring)
    pip install scenedetect[opencv]

NO changes to your existing pipeline files.
"""

import argparse
import json
import time
import warnings
from pathlib import Path

import cv2
import numpy as np
import torch

warnings.filterwarnings("ignore")

DEVICE = "cuda" if torch.cuda.is_available() else "cpu"


# ─────────────────────────────────────────────
# Frame sampling
# ─────────────────────────────────────────────

def sample_frames_evenly(video_path: str, num_samples: int = 20):
    """Sample frames evenly across the full video."""
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise IOError(f"Cannot open video: {video_path}")

    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps          = cap.get(cv2.CAP_PROP_FPS) or 25.0
    duration_s   = total_frames / fps

    indices = np.linspace(0, total_frames - 1, num_samples).astype(int)
    frames, timestamps = [], []

    for idx in indices:
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ok, frame = cap.read()
        if ok:
            frames.append(frame)
            timestamps.append(round(idx / fps, 2))

    cap.release()
    return frames, timestamps, duration_s, fps


# ─────────────────────────────────────────────
# Cut / pacing detection
# ─────────────────────────────────────────────

def detect_cuts(video_path: str):
    """Detect scene cuts and return cut lengths + timestamps."""
    try:
        from scenedetect import VideoManager, SceneManager
        from scenedetect.detectors import ContentDetector

        vm = VideoManager([video_path])
        sm = SceneManager()
        sm.add_detector(ContentDetector(threshold=27.0))
        vm.start()
        sm.detect_scenes(frame_source=vm)
        scenes = sm.get_scene_list()
        vm.release()

        cuts = [(round(s.get_seconds(), 2), round(e.get_seconds(), 2)) for s, e in scenes]
        lengths = [round(e - s, 3) for s, e in cuts if e > s]

        return {
            "num_cuts":     len(cuts),
            "cut_lengths":  lengths,
            "median_cut_s": round(float(np.median(lengths)), 3) if lengths else None,
            "min_cut_s":    round(float(np.min(lengths)), 3)    if lengths else None,
            "max_cut_s":    round(float(np.max(lengths)), 3)    if lengths else None,
            "p10_s":        round(float(np.percentile(lengths, 10)), 3) if lengths else None,
            "p90_s":        round(float(np.percentile(lengths, 90)), 3) if lengths else None,
            "cut_timestamps": cuts,
        }
    except ImportError:
        return {"error": "scenedetect not installed — run: pip install scenedetect[opencv]"}
    except Exception as e:
        return {"error": str(e)}


# ─────────────────────────────────────────────
# Per-frame heuristic analysis
# ─────────────────────────────────────────────

def _classify_indoor_outdoor(frame_bgr: np.ndarray) -> str:
    h_frame = frame_bgr.shape[0]
    top     = frame_bgr[:int(h_frame * 0.20), :]

    hsv_top  = cv2.cvtColor(top, cv2.COLOR_BGR2HSV).astype(np.float32)
    hsv_full = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV).astype(np.float32)

    h_t, s_t, v_t = hsv_top[:,:,0], hsv_top[:,:,1], hsv_top[:,:,2]
    sky = ((h_t >= 95) & (h_t <= 130) & (v_t >= 150) & (s_t >= 30) & (s_t <= 200))
    sky_ratio      = float(sky.mean())
    mean_sat       = float(hsv_full[:,:,1].mean())

    return "outdoor" if sky_ratio > 0.15 or mean_sat > 80 else "indoor"


def analyze_frame(frame_bgr: np.ndarray, timestamp: float) -> dict:
    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)

    # Brightness
    mean_brightness = float(gray.mean())
    brightness_tag  = "bright" if mean_brightness > 175 else ("dark" if mean_brightness < 65 else "normal")

    # Blur
    blur_score = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    is_blurry  = blur_score < 80.0

    # Indoor / outdoor
    io_tag = _classify_indoor_outdoor(frame_bgr)

    # Colour mood (warm / cool / neutral)
    img_f  = frame_bgr.astype(np.float32) / 255.0
    r_mean = float(img_f[:,:,2].mean())
    g_mean = float(img_f[:,:,1].mean())
    b_mean = float(img_f[:,:,0].mean())
    warmth = r_mean - b_mean
    colour_mood = "warm" if warmth > 0.06 else ("cool" if warmth < -0.06 else "neutral")

    # Saturation
    hsv        = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)
    mean_sat   = float(hsv[:,:,1].mean())
    sat_label  = "high_saturation" if mean_sat > 100 else ("low_saturation" if mean_sat < 40 else "medium_saturation")

    # Contrast
    contrast   = float(gray.std())

    # Edge density (composition complexity)
    edges      = cv2.Canny(gray, 50, 150)
    edge_dens  = float(edges.mean())
    complexity = "busy" if edge_dens > 18 else ("minimal" if edge_dens < 6 else "moderate")

    # Face detection
    face_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    small        = cv2.resize(frame_bgr, (320, 180))
    gray_small   = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    faces        = face_cascade.detectMultiScale(gray_small, scaleFactor=1.1, minNeighbors=3)
    has_face     = len(faces) > 0

    # Dominant colour (average BGR → rough description)
    avg_b, avg_g, avg_r = (
        float(img_f[:,:,0].mean()),
        float(img_f[:,:,1].mean()),
        float(img_f[:,:,2].mean()),
    )

    tags = [brightness_tag, io_tag, colour_mood, sat_label, complexity]
    if has_face:
        tags.append("face")
    if is_blurry:
        tags.append("blurry")

    return {
        "timestamp_s":     timestamp,
        "brightness":      round(mean_brightness, 1),
        "brightness_tag":  brightness_tag,
        "blur_score":      round(blur_score, 1),
        "is_blurry":       is_blurry,
        "indoor_outdoor":  io_tag,
        "colour_mood":     colour_mood,
        "saturation":      round(mean_sat, 1),
        "saturation_label":sat_label,
        "contrast":        round(contrast, 1),
        "composition":     complexity,
        "edge_density":    round(edge_dens, 2),
        "has_face":        has_face,
        "avg_rgb":         [round(avg_r,3), round(avg_g,3), round(avg_b,3)],
        "tags":            tags,
    }


# ─────────────────────────────────────────────
# Aesthetic scoring (LAION)
# ─────────────────────────────────────────────

def score_aesthetic_batch(frames):
    """
    Score aesthetics using LAION-Aesthetics v2 MLP if open_clip_torch is
    installed. Falls back gracefully with a warning.
    """
    try:
        import urllib.request
        import torch.nn as nn
        from PIL import Image
        import open_clip

        WEIGHTS_URL = (
            "https://github.com/christophschuhmann/"
            "improved-aesthetic-predictor/raw/main/sac%2Blogos%2Bava1-l14-linearMSE.pth"
        )
        weights_path = Path("aesthetic_mlp_temp.pth")
        if not weights_path.exists():
            print("  [aesthetic] Downloading LAION MLP weights (~1MB)...")
            urllib.request.urlretrieve(WEIGHTS_URL, weights_path)

        class _MLP(nn.Module):
            def __init__(self):
                super().__init__()
                self.layers = nn.Sequential(
                    nn.Linear(768, 1024), nn.Dropout(0.2),
                    nn.Linear(1024, 128), nn.Dropout(0.2),
                    nn.Linear(128, 64),  nn.Dropout(0.1),
                    nn.Linear(64, 16),   nn.Linear(16, 1),
                )
            def forward(self, x):
                return self.layers(x)

        mlp = _MLP()
        mlp.load_state_dict(torch.load(weights_path, map_location="cpu"))
        mlp.eval().to(DEVICE)

        clip_model, _, preprocess = open_clip.create_model_and_transforms(
            "ViT-L-14", pretrained="openai", device=DEVICE
        )
        clip_model.eval()

        scores = []
        for frame in frames:
            img_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            pil     = Image.fromarray(img_rgb)
            tensor  = preprocess(pil).unsqueeze(0).to(DEVICE)
            with torch.no_grad():
                emb   = clip_model.encode_image(tensor)
                emb   = emb / emb.norm(dim=-1, keepdim=True)
                score = float(mlp(emb.float()).squeeze())
            scores.append(round(score, 3))

        return scores, True

    except ImportError:
        print("  [aesthetic] open_clip_torch not installed — skipping aesthetic scores.")
        print("  Install with: pip install open_clip_torch")
        return [None] * len(frames), False
    except Exception as e:
        print(f"  [aesthetic] Scorer failed: {e}")
        return [None] * len(frames), False


# ─────────────────────────────────────────────
# Motion smoothness
# ─────────────────────────────────────────────

def analyze_motion_smoothness(video_path: str, num_pairs: int = 10) -> dict:
    """Measure overall motion smoothness across the full video."""
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return {}

    total  = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps    = cap.get(cv2.CAP_PROP_FPS) or 25.0
    indices = np.linspace(0, total - 2, num_pairs).astype(int)

    flow_mags = []
    for idx in indices:
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ok1, f1 = cap.read()
        ok2, f2 = cap.read()
        if not ok1 or not ok2:
            continue
        g1 = cv2.resize(cv2.cvtColor(f1, cv2.COLOR_BGR2GRAY), (160, 90))
        g2 = cv2.resize(cv2.cvtColor(f2, cv2.COLOR_BGR2GRAY), (160, 90))
        flow = cv2.calcOpticalFlowFarneback(
            g1, g2, None, 0.5, 2, 12, 2, 5, 1.1, 0
        )
        mag = np.sqrt(flow[...,0]**2 + flow[...,1]**2)
        flow_mags.append(float(mag.mean()))

    cap.release()

    if len(flow_mags) < 2:
        return {}

    arr        = np.array(flow_mags)
    cv_score   = float(arr.std() / (arr.mean() + 1e-6))
    smoothness = round(float(max(0.0, min(1.0 - cv_score * 0.5, 1.0))), 3)

    return {
        "motion_smoothness_score": smoothness,
        "smoothness_label":        "smooth" if smoothness > 0.65 else ("moderate" if smoothness > 0.35 else "shaky"),
        "avg_flow_magnitude":      round(float(arr.mean()), 3),
        "flow_variance":           round(float(arr.std()), 3),
    }


# ─────────────────────────────────────────────
# Summary aggregation
# ─────────────────────────────────────────────

def aggregate_frame_stats(frame_analyses: list, aesthetic_scores: list) -> dict:
    """Roll up per-frame stats into a video-level summary."""

    def pct(data, key):
        vals = [f[key] for f in data if isinstance(f.get(key), (int, float))]
        return round(float(np.mean(vals)), 2) if vals else None

    def mode_tag(data, key):
        from collections import Counter
        vals = [f[key] for f in data if f.get(key)]
        return Counter(vals).most_common(1)[0][0] if vals else None

    def tag_frequency(data):
        from collections import Counter
        all_tags = [t for f in data for t in f.get("tags", [])]
        total    = len(data)
        return {k: round(v / total, 3) for k, v in Counter(all_tags).most_common()}

    valid_aesth = [s for s in aesthetic_scores if s is not None]

    return {
        "avg_brightness":      pct(frame_analyses, "brightness"),
        "avg_blur_score":      pct(frame_analyses, "blur_score"),
        "avg_contrast":        pct(frame_analyses, "contrast"),
        "avg_saturation":      pct(frame_analyses, "saturation"),
        "dominant_setting":    mode_tag(frame_analyses, "indoor_outdoor"),
        "dominant_colour_mood":mode_tag(frame_analyses, "colour_mood"),
        "dominant_composition":mode_tag(frame_analyses, "composition"),
        "face_presence_ratio": round(sum(1 for f in frame_analyses if f["has_face"]) / len(frame_analyses), 3),
        "blurry_frame_ratio":  round(sum(1 for f in frame_analyses if f["is_blurry"]) / len(frame_analyses), 3),
        "tag_frequency":       tag_frequency(frame_analyses),
        "aesthetic_score_avg": round(float(np.mean(valid_aesth)), 3) if valid_aesth else None,
        "aesthetic_score_min": round(float(np.min(valid_aesth)), 3)  if valid_aesth else None,
        "aesthetic_score_max": round(float(np.max(valid_aesth)), 3)  if valid_aesth else None,
        "aesthetic_available": len(valid_aesth) > 0,
    }


# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Ground truth analysis of a single reference reel.")
    parser.add_argument("--video",   required=True,  help="Path to the reference MP4/MOV file.")
    parser.add_argument("--frames",  default=20, type=int, help="Number of frames to sample (default: 20).")
    parser.add_argument("--output",  default="ground_truth_analysis.json", help="Output JSON path.")
    parser.add_argument("--no_aesthetic", action="store_true", help="Skip aesthetic scoring (faster).")
    args = parser.parse_args()

    video_path = args.video
    if not Path(video_path).exists():
        print(f"ERROR: Video file not found: {video_path}")
        return

    print(f"\n{'='*60}")
    print(f"  Ground Truth Analysis")
    print(f"  Video: {Path(video_path).name}")
    print(f"{'='*60}\n")

    t_start = time.time()

    # 1. Basic video metadata
    cap       = cv2.VideoCapture(video_path)
    fps       = cap.get(cv2.CAP_PROP_FPS)
    width     = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height    = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    n_frames  = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    duration  = round(n_frames / fps, 2) if fps > 0 else None
    cap.release()

    video_meta = {
        "filename":       Path(video_path).name,
        "duration_s":     duration,
        "fps":            round(fps, 2),
        "resolution":     f"{width}x{height}",
        "aspect_ratio":   "9:16 (vertical)" if height > width else "16:9 (horizontal)",
        "total_frames":   n_frames,
    }
    print(f"  Duration: {duration}s  |  {width}x{height}  |  {fps:.1f}fps")

    # 2. Cut detection
    print("  Detecting cuts...")
    pacing = detect_cuts(video_path)
    if "num_cuts" in pacing:
        print(f"  Cuts found: {pacing['num_cuts']}  |  Median cut: {pacing.get('median_cut_s')}s")

    # 3. Frame sampling + per-frame analysis
    print(f"  Sampling {args.frames} frames...")
    frames, timestamps, _, _ = sample_frames_evenly(video_path, args.frames)
    print(f"  Analysing frames...")
    frame_analyses = [analyze_frame(f, t) for f, t in zip(frames, timestamps)]

    # 4. Aesthetic scoring
    aesthetic_scores = [None] * len(frames)
    if not args.no_aesthetic:
        print("  Scoring aesthetics (LAION)...")
        aesthetic_scores, aesth_ok = score_aesthetic_batch(frames)
        if aesth_ok:
            for i, s in enumerate(aesthetic_scores):
                frame_analyses[i]["aesthetic_score_laion"] = s
            print(f"  Aesthetic scores: min={min(s for s in aesthetic_scores if s):.2f}  "
                  f"max={max(s for s in aesthetic_scores if s):.2f}  "
                  f"avg={np.mean([s for s in aesthetic_scores if s]):.2f}")

    # 5. Motion smoothness
    print("  Analysing motion smoothness...")
    motion = analyze_motion_smoothness(video_path)
    if motion:
        print(f"  Motion smoothness: {motion.get('smoothness_label')} ({motion.get('motion_smoothness_score')})")

    # 6. Aggregate
    summary = aggregate_frame_stats(frame_analyses, aesthetic_scores)

    # 7. Assemble output
    elapsed = round(time.time() - t_start, 1)

    output = {
        "analysis_meta": {
            "script_version": "1.0",
            "device_used":    DEVICE,
            "frames_sampled": len(frames),
            "elapsed_s":      elapsed,
        },
        "video_metadata":   video_meta,
        "pacing":           pacing,
        "motion":           motion,
        "summary":          summary,
        "per_frame":        frame_analyses,
    }

    # Save
    out_path = Path(args.output)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)

    print(f"\n{'='*60}")
    print(f"  Analysis complete in {elapsed}s")
    print(f"  Output saved to: {out_path}")
    print(f"{'='*60}\n")

    # Print compact summary to console for easy copy-paste
    paste_summary = {
        "video_metadata":   video_meta,
        "pacing":           pacing,
        "motion":           motion,
        "summary":          summary,
    }
    print("── PASTE THIS INTO CHAT ──────────────────────────────────")
    print(json.dumps(paste_summary, indent=2))
    print("──────────────────────────────────────────────────────────\n")


if __name__ == "__main__":
    main()