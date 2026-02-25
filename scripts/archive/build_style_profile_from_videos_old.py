import os
import json
from typing import List, Dict, Any

import cv2
import numpy as np
import torch
from tqdm import tqdm
from transformers import (
    AutoImageProcessor,
    SiglipVisionModel,
    AutoModelForImageClassification,
)

DEVICE = "cuda" if torch.cuda.is_available() else "cpu"

# ------------------------------------------------------------
# SigLIP (VISION-ONLY)
# ------------------------------------------------------------
SIGLIP_MODEL_NAME = "google/siglip-base-patch16-224"
siglip_model = None
siglip_processor = None


def load_siglip():
    """Load SigLIP vision encoder only."""
    global siglip_model, siglip_processor
    if siglip_model is None:
        siglip_processor = AutoImageProcessor.from_pretrained(SIGLIP_MODEL_NAME)
        siglip_model = SiglipVisionModel.from_pretrained(SIGLIP_MODEL_NAME).to(DEVICE).eval()
    return siglip_model, siglip_processor


def encode_siglip(frame_bgr: np.ndarray) -> torch.Tensor:
    """Encode a frame using SigLIP vision encoder."""
    model, processor = load_siglip()
    img_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
    inputs = processor(images=img_rgb, return_tensors="pt").to(DEVICE)

    with torch.no_grad():
        outputs = model(**inputs)
        emb = outputs.pooler_output[0]
        emb = emb / emb.norm(dim=-1, keepdim=True)

    return emb.cpu()


# ------------------------------------------------------------
# DINO aesthetic proxy
# ------------------------------------------------------------
DINO_MODEL_NAME = "facebook/dinov2-base-imagenet1k-1-layer"
dino_model = None
dino_processor = None


def load_dino():
    global dino_model, dino_processor
    if dino_model is None:
        dino_processor = AutoImageProcessor.from_pretrained(DINO_MODEL_NAME)
        dino_model = AutoModelForImageClassification.from_pretrained(DINO_MODEL_NAME).to(
            DEVICE
        ).eval()
    return dino_model, dino_processor

def compute_aesthetic_score(frame_bgr: np.ndarray) -> float:
    """Uses DINOv2 classification logits as a loose aesthetic proxy."""
    model, processor = load_dino()
    img_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
    inputs = processor(images=img_rgb, return_tensors="pt").to(DEVICE)

    with torch.no_grad():
        outputs = model(**inputs)
        logits = outputs.logits[0]
        score = float(torch.softmax(logits, dim=-1).max())
    return score


# ------------------------------------------------------------
# Frame sampling
# ------------------------------------------------------------
def sample_frames(video_path: str, num_samples: int = 5) -> List[np.ndarray]:
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return []

    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    if frame_count <= 0:
        cap.release()
        return []

    indices = np.linspace(0, frame_count - 1, num_samples).astype(int)
    frames: List[np.ndarray] = []

    for idx in indices:
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ok, frame = cap.read()
        if ok:
            frames.append(frame)

    cap.release()
    return frames


# ------------------------------------------------------------
# Build style profile
# ------------------------------------------------------------
def build_style_profile(folder: str, output_json: str, name: str):
    video_files = [
        os.path.join(folder, f)
        for f in os.listdir(folder)
        if f.lower().endswith(".mp4")
    ]

    if not video_files:
        print("No MP4 files found.")
        return

    siglip_embeds: List[np.ndarray] = []
    aesthetic_scores: List[float] = []
    tag_counts: Dict[str, int] = {"bright": 0, "dark": 0, "outdoor": 0, "indoor": 0}

    for video in tqdm(video_files, desc="Processing videos"):
        frames = sample_frames(video, num_samples=5)
        for frame in frames:
            emb = encode_siglip(frame)
            siglip_embeds.append(emb.numpy())

            aesth = compute_aesthetic_score(frame)
            aesthetic_scores.append(aesth)

            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            mean_brightness = float(gray.mean())
            if mean_brightness > 180:
                tag_counts["bright"] += 1
            elif mean_brightness < 60:
                tag_counts["dark"] += 1

            if mean_brightness > 120:
                tag_counts["outdoor"] += 1
            else:
                tag_counts["indoor"] += 1

    # ---- FIXED: Guard before computing means ----
    if not siglip_embeds:
        print("No frames encoded; style profile not created.")
        return

    siglip_embeds_np = np.array(siglip_embeds)
    style_embedding = siglip_embeds_np.mean(axis=0).tolist()

    if aesthetic_scores:
        avg_aesthetic = float(np.mean(aesthetic_scores))
    else:
        avg_aesthetic = 0.5

    total_tags = sum(tag_counts.values()) or 1
    tag_bias = {k: v / total_tags for k, v in tag_counts.items()}

    profile: Dict[str, Any] = {
        "name": name,
        "version": 1,
        "source": "instagram",
        "siglip_style_embedding": style_embedding,
        "avg_aesthetic_score": avg_aesthetic,
        "tag_bias": tag_bias,
        "duration_preferences": {
            "min_segment": 1.0,
            "max_segment": 5.0,
            "target_total": 30.0,
        },
    }

    os.makedirs(os.path.dirname(output_json), exist_ok=True)
    with open(output_json, "w", encoding="utf-8") as f:
        json.dump(profile, f, indent=4)

    print(f"Saved style profile to {output_json}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Build a SigLIP-based style profile.")
    parser.add_argument("--folder", required=True, help="Folder containing MP4 videos.")
    parser.add_argument("--output", required=True, help="Output JSON path.")
    parser.add_argument("--name", default="custom_style", help="Profile display name.")
    args = parser.parse_args()

    build_style_profile(args.folder, args.output, args.name)
