import os
from typing import Dict, Any, List, Tuple

import torch
import cv2
import numpy as np

try:
    import clip
except ImportError:
    clip = None

_DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
_CLIP_MODEL = None
_CLIP_PREPROCESS = None
_HERO_EMBEDDING = None

def _load_clip_model():
    global _CLIP_MODEL, _CLIP_PREPROCESS
    if clip is None:
        return None, None
    if _CLIP_MODEL is None:
        model, preprocess = clip.load("ViT-B/32", device=_DEVICE)
        _CLIP_MODEL = model.eval()
        _CLIP_PREPROCESS = preprocess
    return _CLIP_MODEL, _CLIP_PREPROCESS

def _encode_image_bgr(frame_bgr: np.ndarray) -> torch.Tensor:
    """
    Takes a BGR OpenCV frame, converts to RGB, runs through CLIP, returns a normalized embedding.
    """
    model, preprocess = _load_clip_model() 
    if model is None: 
        return torch.zeros(512) # or some neutral embedding

    img_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
    img_pil = cv2.cvtColor(img_rgb, cv2.COLOR_RGB2BGR)  # OpenCV to PIL-like array is fine for preprocess

    # preprocess expects a PIL Image, but it also works with numpy arrays shaped like images
    image_input = preprocess(img_pil).unsqueeze(0).to(_DEVICE)

    with torch.no_grad():
        image_features = model.encode_image(image_input)
        image_features = image_features / image_features.norm(dim=-1, keepdim=True)

    return image_features[0].cpu()


def _cosine_sim(a: torch.Tensor, b: torch.Tensor) -> float:
    return float((a @ b) / (a.norm() * b.norm() + 1e-8))


def _sample_frame(video_path: str, t: float = 0.5) -> np.ndarray:
    """
    Sample a frame at relative position t in [0,1] from the video.
    """
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return None

    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    if frame_count <= 0:
        cap.release()
        return None

    idx = int(frame_count * t)
    cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
    ok, frame = cap.read()
    cap.release()
    if not ok:
        return None
    return frame


def compute_hero_embedding(hero_video_path: str) -> torch.Tensor:
    """
    Compute and cache a CLIP embedding for the hero video.
    """
    global _HERO_EMBEDDING
    if _HERO_EMBEDDING is not None:
        return _HERO_EMBEDDING

    frame = _sample_frame(hero_video_path, t=0.5)
    if frame is None:
        return None

    emb = _encode_image_bgr(frame)
    if emb is None:
        return None
    _HERO_EMBEDDING = emb
    return emb


def _estimate_blur(frame_bgr: np.ndarray) -> float:
    """
    Simple blur metric using Laplacian variance.
    Lower values = blurrier.
    """
    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def _tag_from_frame(frame_bgr: np.ndarray) -> List[str]:
    """
    Placeholder for semantic tags.
    For now, we can do simple heuristics (later: text prompts with CLIP).
    """
    tags: List[str] = []

    # brightness heuristic
    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    mean_brightness = float(gray.mean())
    if mean_brightness > 180:
        tags.append("bright")
    elif mean_brightness < 60:
        tags.append("dark")

    # aspect / composition heuristics could go here

    return tags


def analyze_segment_semantics(seg: Dict[str, Any], hero_embedding: torch.Tensor | None) -> Dict[str, Any]:
    """
    Given a segment dict with video_path/start/end,
    attach semantic info:
      - tags
      - hero_similarity
      - is_blurry
    """
    video_path = seg.get("video_path")
    start = float(seg.get("start", 0.0))
    end = float(seg.get("end", start + 1.0))

    if not video_path or not os.path.exists(video_path):
        seg["tags"] = []
        seg["hero_similarity"] = 0.0
        seg["is_blurry"] = False
        return seg

    # sample a frame roughly in the middle of the segment
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        seg["tags"] = []
        seg["hero_similarity"] = 0.0
        seg["is_blurry"] = False
        return seg

    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    mid_t = (start + end) / 2.0
    frame_idx = int(mid_t * fps)
    cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
    ok, frame = cap.read()
    cap.release()

    if not ok or frame is None:
        seg["tags"] = []
        seg["hero_similarity"] = 0.0
        seg["is_blurry"] = False
        return seg

    # blur metric
    blur_val = _estimate_blur(frame)
    is_blurry = blur_val < 80.0  # threshold can be tuned

    # CLIP embedding for this segment
    seg_emb = _encode_image_bgr(frame)
    if seg_emb is None or hero_embedding is None:
        hero_sim = 0.0
    else:
        hero_sim = _cosine_sim(seg_emb, hero_embedding)

    # hero similarity
    if seg_emb is None:
        hero_sim = 0.0

    # simple tags
    tags = _tag_from_frame(frame)

    seg["tags"] = tags
    seg["hero_similarity"] = hero_sim
    seg["is_blurry"] = is_blurry
    return seg


def enrich_segments_with_semantics(segments: List[Dict[str, Any]], hero_video_path: str | None = None) -> List[Dict[str, Any]]:
    hero_emb = None
    if hero_video_path:
        hero_emb = compute_hero_embedding(hero_video_path)

    enriched = []
    for seg in segments:
        enriched.append(analyze_segment_semantics(seg, hero_emb))
    return enriched
