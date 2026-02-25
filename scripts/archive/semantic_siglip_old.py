import os
from typing import Dict, Any, List, Optional

import torch
import cv2
import numpy as np
from transformers import AutoImageProcessor, SiglipVisionModel
from scripts.semantic_aesthetic_old import add_aesthetic_score_to_segment

_DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
_SIGLIP_MODEL_NAME = "google/siglip-base-patch16-224"
_SIGLIP_MODEL: Optional[SiglipVisionModel] = None
_SIGLIP_PROCESSOR: Optional[AutoImageProcessor] = None


def _load_siglip():
    global _SIGLIP_MODEL, _SIGLIP_PROCESSOR
    if _SIGLIP_MODEL is None:
        _SIGLIP_PROCESSOR = AutoImageProcessor.from_pretrained(_SIGLIP_MODEL_NAME)
        _SIGLIP_MODEL = SiglipVisionModel.from_pretrained(_SIGLIP_MODEL_NAME).to(_DEVICE).eval()
    return _SIGLIP_MODEL, _SIGLIP_PROCESSOR


def _encode_frame_bgr_siglip(frame_bgr: np.ndarray) -> torch.Tensor:
    model, processor = _load_siglip()
    img_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)

    inputs = processor(images=img_rgb, return_tensors="pt").to(_DEVICE)

    with torch.no_grad():
        outputs = model(**inputs)
        emb = outputs.pooler_output[0]
        emb = emb / emb.norm(dim=-1, keepdim=True)

    return emb.cpu()


def _cosine_sim(a: torch.Tensor, b: torch.Tensor) -> float:
    return float((a @ b) / (a.norm() * b.norm() + 1e-8))


def _estimate_blur(frame_bgr: np.ndarray) -> float:
    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def _basic_tags(frame_bgr: np.ndarray) -> List[str]:
    tags: List[str] = []
    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    mean_brightness = float(gray.mean())
    if mean_brightness > 180:
        tags.append("bright")
    elif mean_brightness < 60:
        tags.append("dark")
    return tags


def _sample_mid_frame(video_path: str, start: float, end: float) -> Optional[np.ndarray]:
    cap = cv2.VideoCapture(os.fspath(video_path))
    if not cap.isOpened():
        return None
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    mid_t = (start + end) / 2.0
    frame_idx = int(mid_t * fps)
    cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
    ok, frame = cap.read()
    cap.release()
    if not ok:
        return None
    return frame


def analyze_segment_with_siglip(
    seg: Dict[str, Any],
    style_embedding: Optional[torch.Tensor] = None,
    tag_bias: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    video_path = seg.get("video_path")
    start = float(seg.get("start", 0.0))
    end = float(seg.get("end", start + 1.0))

    if not video_path or not os.path.exists(os.fspath(video_path)):
        seg["tags"] = []
        seg["style_similarity"] = 0.0
        seg["is_blurry"] = False
        seg["aesthetic_score"] = 0.0
        return seg

    frame = _sample_mid_frame(video_path, start, end)
    if frame is None:
        seg["tags"] = []
        seg["style_similarity"] = 0.0
        seg["is_blurry"] = False
        seg["aesthetic_score"] = 0.0
        return seg

    blur_val = _estimate_blur(frame)
    is_blurry = blur_val < 80.0

    emb = _encode_frame_bgr_siglip(frame)
    if style_embedding is not None:
        style_sim = _cosine_sim(emb, style_embedding)
    else:
        style_sim = 0.0

    tags = _basic_tags(frame)

    if tag_bias:
        bias_bonus = 0.0
        for t in tags:
            bias_bonus += float(tag_bias.get(t, 0.0))
        style_sim = float(style_sim + 0.1 * bias_bonus)

    seg["tags"] = tags
    seg["style_similarity"] = style_sim
    seg["is_blurry"] = is_blurry
    seg["siglip_embedding"] = emb.numpy().tolist()

    seg = add_aesthetic_score_to_segment(seg, frame)

    return seg


def enrich_segments_with_siglip(
    segments: List[Dict[str, Any]],
    style_profile: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    style_emb = None
    tag_bias = None

    if style_profile is not None:
        se = style_profile.get("siglip_style_embedding")
        if se is not None:
            style_emb = torch.tensor(se, dtype=torch.float32)
        tag_bias = style_profile.get("tag_bias", {})

    enriched: List[Dict[str, Any]] = []
    for seg in segments:
        enriched.append(analyze_segment_with_siglip(seg, style_emb, tag_bias))
    return enriched
