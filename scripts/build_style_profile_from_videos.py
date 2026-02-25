"""
build_style_profile_from_videos.py
------------------------------------
Builds a complete style profile JSON from a folder of reference videos.

Automatically generates ALL fields required by editing_brain.py and
analyze_and_edit.py including scoring_weights, blur_threshold, pacing,
and duration_preferences — calibrated from the actual reference videos.

Usage (run from project root D:\\video-agent):

    python scripts/build_style_profile_from_videos.py --folder reference_ig/product_style   --output style_profiles/product_style.json   --name product_style
    python scripts/build_style_profile_from_videos.py --folder reference_ig/travel_reel      --output style_profiles/travel_reel.json      --name travel_reel
    python scripts/build_style_profile_from_videos.py --folder reference_ig/event_concert    --output style_profiles/event_concert.json    --name event_concert
    python scripts/build_style_profile_from_videos.py --folder reference_ig/grwm_style       --output style_profiles/grwm_style.json       --name grwm_style
    python scripts/build_style_profile_from_videos.py --folder reference_ig/breakfast_food   --output style_profiles/breakfast_food.json   --name breakfast_food

Fields produced in each JSON:
    siglip_style_embedding    768-dim mean style vector from all reference frames
    avg_aesthetic_score       Real LAION-Aesthetics v2 score
    tag_bias                  Fraction of frames with each tag
    pacing                    median_cut, p10, p90, min/max from scene detection
    duration_preferences      min/max/target calibrated from pacing
    scoring_weights           Per-template weights calibrated from content analysis
    blur_threshold            Calibrated at 25% of reference video sharpness
    metadata                  Per-video breakdown for debugging
"""

import os
import json
import argparse
import warnings
from pathlib import Path
from statistics import median
from typing import List, Dict, Any, Optional

import cv2
import numpy as np
import torch
from tqdm import tqdm
from transformers import AutoImageProcessor, SiglipVisionModel

try:
    from scenedetect import VideoManager, SceneManager
    from scenedetect.detectors import ContentDetector
    _SCENEDETECT_AVAILABLE = True
except ImportError:
    _SCENEDETECT_AVAILABLE = False
    print("[warn] scenedetect not found — pacing will use defaults. "
          "Install: pip install scenedetect[opencv]")

DEVICE = "cuda" if torch.cuda.is_available() else "cpu"


# ── SigLIP ────────────────────────────────────────────────────────────────────

SIGLIP_MODEL_NAME = "google/siglip-base-patch16-224"
_siglip_model     = None
_siglip_processor = None


def _load_siglip():
    global _siglip_model, _siglip_processor
    if _siglip_model is None:
        print("[info] Loading SigLIP model...")
        _siglip_processor = AutoImageProcessor.from_pretrained(SIGLIP_MODEL_NAME)
        _siglip_model     = SiglipVisionModel.from_pretrained(SIGLIP_MODEL_NAME).to(DEVICE).eval()
    return _siglip_model, _siglip_processor


def _encode_siglip(frame_bgr: np.ndarray) -> Optional[np.ndarray]:
    try:
        model, processor = _load_siglip()
        img_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        inputs  = processor(images=img_rgb, return_tensors="pt").to(DEVICE)
        with torch.no_grad():
            outputs = model(**inputs)
            emb     = outputs.pooler_output[0]
            emb     = emb / emb.norm(dim=-1, keepdim=True)
        return emb.cpu().numpy()
    except Exception as e:
        print(f"[warn] SigLIP encoding failed: {e}")
        return None


# ── LAION Aesthetic Scorer ────────────────────────────────────────────────────

def _get_aesthetic_score(frame_bgr: np.ndarray) -> Optional[float]:
    """Import the real LAION scorer from semantic_aesthetic.py."""
    try:
        from scripts.semantic_aesthetic import score_aesthetic
        return score_aesthetic(frame_bgr)
    except ImportError:
        pass
    try:
        import sys
        sys.path.append(str(Path(__file__).resolve().parent))
        from semantic_aesthetic import score_aesthetic
        return score_aesthetic(frame_bgr)
    except Exception as exc:
        print(f"[warn] Aesthetic scorer unavailable ({exc}). "
              "Ensure open_clip_torch is installed: pip install open_clip_torch")
        return None


# ── Indoor / Outdoor (mirrors semantic_siglip.py) ────────────────────────────

def _classify_indoor_outdoor(frame_bgr: np.ndarray) -> str:
    h_frame  = frame_bgr.shape[0]
    top      = frame_bgr[:int(h_frame * 0.20), :]
    hsv_top  = cv2.cvtColor(top,       cv2.COLOR_BGR2HSV).astype(np.float32)
    hsv_full = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV).astype(np.float32)
    h_t, s_t, v_t = hsv_top[:,:,0], hsv_top[:,:,1], hsv_top[:,:,2]
    sky_mask = (
        (h_t >= 95) & (h_t <= 130) &
        (v_t >= 150) &
        (s_t >= 30)  & (s_t <= 200)
    )
    sky_ratio = float(sky_mask.mean())
    mean_sat  = float(hsv_full[:,:,1].mean())
    return "outdoor" if sky_ratio > 0.15 or mean_sat > 80 else "indoor"


# ── Per-frame tag analysis ────────────────────────────────────────────────────

def _tag_frame(frame_bgr: np.ndarray):
    """Return ({tag: count}, blur_score) for a single frame."""
    counts: Dict[str, int] = {}

    gray            = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    mean_brightness = float(gray.mean())
    blur_score      = float(cv2.Laplacian(gray, cv2.CV_64F).var())

    # Brightness
    if mean_brightness > 175:
        counts["bright"] = 1
    elif mean_brightness < 65:
        counts["dark"] = 1

    # Indoor / outdoor
    counts[_classify_indoor_outdoor(frame_bgr)] = 1

    # Face
    face_cascade = cv2.CascadeClassifier(
        cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
    )
    small      = cv2.resize(frame_bgr, (320, 180))
    gray_small = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    faces      = face_cascade.detectMultiScale(gray_small, scaleFactor=1.1, minNeighbors=3)
    if len(faces) > 0:
        counts["face"] = 1

    # Colour mood
    img_f  = frame_bgr.astype(np.float32) / 255.0
    warmth = float(img_f[:,:,2].mean()) - float(img_f[:,:,0].mean())
    if warmth > 0.06:
        counts["warm"] = 1
    elif warmth < -0.06:
        counts["cool"] = 1

    # Composition
    edges     = cv2.Canny(gray, 50, 150)
    edge_dens = float(edges.mean())
    if edge_dens > 18:
        counts["busy"] = 1
    elif edge_dens < 6:
        counts["minimal"] = 1

    return counts, blur_score


# ── Frame sampling ────────────────────────────────────────────────────────────

def _sample_frames(video_path: str, num_samples: int = 12) -> List[np.ndarray]:
    cap         = cv2.VideoCapture(video_path)
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    if not cap.isOpened() or frame_count <= 0:
        cap.release()
        return []
    indices = np.linspace(0, frame_count - 1, num_samples).astype(int)
    frames  = []
    for idx in indices:
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ok, frame = cap.read()
        if ok:
            frames.append(frame)
    cap.release()
    return frames


# ── Pacing extraction ─────────────────────────────────────────────────────────

def _extract_pacing(video_path: str) -> Dict[str, Any]:
    if not _SCENEDETECT_AVAILABLE:
        return {}
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            vm = VideoManager([video_path])
            sm = SceneManager()
            sm.add_detector(ContentDetector(threshold=27.0))
            vm.start()
            sm.detect_scenes(frame_source=vm)
            scenes = sm.get_scene_list()
            vm.release()

        lengths = [
            round(e.get_seconds() - s.get_seconds(), 3)
            for s, e in scenes
            if e.get_seconds() > s.get_seconds()
        ]
        if not lengths:
            return {}

        ls = sorted(lengths)
        n  = len(ls)
        return {
            "median_cut": round(float(median(ls)), 3),
            "p10":        round(float(ls[int(0.1 * (n - 1))]), 3),
            "p90":        round(float(ls[int(0.9 * (n - 1))]), 3),
            "min_cut":    round(float(ls[0]), 3),
            "max_cut":    round(float(ls[-1]), 3),
            "num_cuts":   n,
        }
    except Exception as e:
        print(f"[warn] Pacing extraction failed: {e}")
        return {}


# ── Auto-calibrate settings ───────────────────────────────────────────────────

def _calibrate_settings(
    pacing:        Dict[str, Any],
    avg_blur:      float,
    tag_freq:      Dict[str, float],
    aesthetic_avg: Optional[float],
) -> Dict[str, Any]:
    """
    Derive scoring_weights, blur_threshold and duration_preferences
    automatically from the measured reference video characteristics.

    Calibration rules:
      Fast pacing  (median < 1.5s) → higher base_score weight, lower max_segment
      Slow pacing  (median > 3.0s) → higher style_sim weight, higher max_segment
      Low aesthetic avg (<4.5/10)  → reduce W_AESTHETIC (LAION underscores themed content)
      High avg blur score          → stricter blur threshold (good footage = high blur score)
    """
    median_cut = pacing.get("median_cut", 2.0)
    p10        = pacing.get("p10",        0.7)
    p90        = pacing.get("p90",        4.0)
    min_cut    = pacing.get("min_cut",    0.5)

    # Duration preferences
    min_segment  = max(0.5,  round(min_cut * 0.9,  1))
    max_segment  = round(min(p90 * 1.3, 10.0), 1)

    duration_prefs = {
        "min_segment":  min_segment,
        "max_segment":  max_segment,
        "target_total": 30.0,
    }

    # Blur threshold = 25% of reference average, clamped 80–400
    blur_threshold = int(max(80, min(avg_blur * 0.25, 400)))

    # Scoring weights — start from balanced defaults
    w_style_sim     = 0.50
    w_aesthetic     = 0.15
    w_motion_smooth = 0.05
    w_base_score    = 0.30

    if median_cut < 1.5:
        # Fast cuts — energy matters more than visual elegance
        w_base_score = 0.35
        w_style_sim  = 0.45

    if median_cut > 3.0:
        # Slow cuts — style consistency is the dominant signal
        w_style_sim  = 0.55
        w_base_score = 0.25

    if aesthetic_avg is not None and aesthetic_avg < 4.5:
        # LAION scores themed/product/food content lower than natural photography
        # Reduce aesthetic weight and redistribute to style_sim
        w_aesthetic  = max(0.10, w_aesthetic - 0.05)
        w_style_sim  = min(0.60, w_style_sim  + 0.05)

    # Normalise to sum = 1.0
    total           = w_style_sim + w_aesthetic + w_motion_smooth + w_base_score
    w_style_sim     = round(w_style_sim     / total, 3)
    w_aesthetic     = round(w_aesthetic     / total, 3)
    w_motion_smooth = round(w_motion_smooth / total, 3)
    w_base_score    = round(1.0 - w_style_sim - w_aesthetic - w_motion_smooth, 3)

    scoring_weights = {
        "W_STYLE_SIM":     w_style_sim,
        "W_AESTHETIC":     w_aesthetic,
        "W_MOTION_SMOOTH": w_motion_smooth,
        "W_BASE_SCORE":    w_base_score,
    }

    return {
        "duration_preferences": duration_prefs,
        "blur_threshold":       blur_threshold,
        "scoring_weights":      scoring_weights,
    }


# ── Main ──────────────────────────────────────────────────────────────────────

def build_style_profile(folder: str, output_json: str, name: str):
    video_files = sorted([
        os.path.join(folder, f)
        for f in os.listdir(folder)
        if f.lower().endswith((".mp4", ".mov", ".mkv"))
    ])

    if not video_files:
        print(f"No video files found in: {folder}")
        return

    print(f"\n{'='*58}")
    print(f"  Building profile: {name}")
    print(f"  Videos found:     {len(video_files)}")
    print(f"  Device:           {DEVICE}")
    print(f"{'='*58}\n")

    siglip_embeds:   List[np.ndarray] = []
    aesthetic_scores: List[float]     = []
    blur_scores:      List[float]     = []
    pacing_list:      List[Dict]      = []
    per_video_stats:  List[Dict]      = []

    tag_counts: Dict[str, int] = {
        "bright": 0, "dark":    0,
        "outdoor": 0, "indoor": 0,
        "face":   0,
        "warm":   0, "cool":   0,
        "busy":   0, "minimal": 0,
    }
    total_frames = 0

    for video in tqdm(video_files, desc="Processing videos"):
        frames = _sample_frames(video, num_samples=12)
        if not frames:
            print(f"  [warn] No frames from {Path(video).name}")
            continue

        v_blur   = []
        v_aesth  = []

        for frame in frames:
            emb = _encode_siglip(frame)
            if emb is not None:
                siglip_embeds.append(emb)

            aesth = _get_aesthetic_score(frame)
            if aesth is not None:
                aesthetic_scores.append(aesth)
                v_aesth.append(aesth)

            frame_tags, blur_val = _tag_frame(frame)
            blur_scores.append(blur_val)
            v_blur.append(blur_val)
            for tag, count in frame_tags.items():
                tag_counts[tag] = tag_counts.get(tag, 0) + count
            total_frames += 1

        pacing = _extract_pacing(video)
        if pacing:
            pacing_list.append(pacing)

        per_video_stats.append({
            "filename":       Path(video).name,
            "frames_sampled": len(frames),
            "avg_blur":       round(float(np.mean(v_blur)),  1) if v_blur  else None,
            "avg_aesthetic":  round(float(np.mean(v_aesth)), 3) if v_aesth else None,
            "num_cuts":       pacing.get("num_cuts"),
            "median_cut_s":   pacing.get("median_cut"),
        })

        print(f"  {Path(video).name:30s}  "
              f"frames={len(frames):3d}  "
              f"blur={round(np.mean(v_blur),0) if v_blur else 'n/a':>6}  "
              f"aesth={round(np.mean(v_aesth),2) if v_aesth else 'n/a':>5}  "
              f"cuts={pacing.get('num_cuts','n/a'):>3}  "
              f"median_cut={pacing.get('median_cut','n/a')}s")

    if not siglip_embeds:
        print("\n[error] No frames encoded — aborting.")
        return

    # Aggregate
    style_embedding = np.array(siglip_embeds).mean(axis=0).tolist()
    avg_aesthetic   = float(np.mean(aesthetic_scores)) if aesthetic_scores else None
    avg_blur        = float(np.mean(blur_scores))      if blur_scores      else 200.0

    denom    = max(total_frames, 1)
    tag_bias = {k: round(v / denom, 4) for k, v in tag_counts.items()}

    # Merge pacing across all videos
    if pacing_list:
        pacing_profile = {
            "median_cut": round(float(np.mean([p["median_cut"] for p in pacing_list if "median_cut" in p])), 3),
            "p10":        round(float(np.mean([p["p10"]        for p in pacing_list if "p10"        in p])), 3),
            "p90":        round(float(np.mean([p["p90"]        for p in pacing_list if "p90"        in p])), 3),
            "min_cut":    round(float(np.min( [p["min_cut"]    for p in pacing_list if "min_cut"    in p])), 3),
            "max_cut":    round(float(np.max( [p["max_cut"]    for p in pacing_list if "max_cut"    in p])), 3),
        }
    else:
        pacing_profile = {"median_cut": 2.0, "p10": 0.7, "p90": 4.0, "min_cut": 0.5, "max_cut": 8.0}

    calibrated = _calibrate_settings(
        pacing        = pacing_profile,
        avg_blur      = avg_blur,
        tag_freq      = tag_bias,
        aesthetic_avg = avg_aesthetic,
    )

    profile: Dict[str, Any] = {
        "name":                   name,
        "version":                2,
        "source":                 "instagram",
        "siglip_style_embedding": style_embedding,
        "avg_aesthetic_score":    round(avg_aesthetic, 4) if avg_aesthetic is not None else None,
        "tag_bias":               tag_bias,
        "pacing":                 pacing_profile,
        "duration_preferences":   calibrated["duration_preferences"],
        "blur_threshold":         calibrated["blur_threshold"],
        "scoring_weights":        calibrated["scoring_weights"],
        "metadata": {
            "num_reference_videos":  len(video_files),
            "total_frames_analysed": total_frames,
            "avg_blur_score":        round(avg_blur, 1),
            "per_video":             per_video_stats,
        },
    }

    os.makedirs(os.path.dirname(os.path.abspath(output_json)), exist_ok=True)
    with open(output_json, "w", encoding="utf-8") as f:
        json.dump(profile, f, indent=4)

    print(f"\n{'='*58}")
    print(f"  Saved:          {output_json}")
    print(f"  Videos:         {len(video_files)}")
    print(f"  Frames:         {total_frames}")
    print(f"  Aesthetic avg:  {round(avg_aesthetic, 3) if avg_aesthetic is not None else 'n/a'}")
    print(f"  Avg blur:       {round(avg_blur, 0)}")
    print(f"  Blur threshold: {calibrated['blur_threshold']}")
    print(f"  Median cut:     {pacing_profile['median_cut']}s")
    print(f"  Segment range:  {calibrated['duration_preferences']['min_segment']}–{calibrated['duration_preferences']['max_segment']}s")
    print(f"  Weights:        {calibrated['scoring_weights']}")
    print(f"  Tag bias:       {tag_bias}")
    print(f"{'='*58}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Build a complete style profile JSON from reference reels."
    )
    parser.add_argument("--folder", required=True, help="Folder containing reference MP4/MOV files.")
    parser.add_argument("--output", required=True, help="Output JSON path.")
    parser.add_argument("--name",   default="custom_style", help="Profile display name.")
    args = parser.parse_args()
    build_style_profile(args.folder, args.output, args.name)