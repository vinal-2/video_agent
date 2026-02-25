"""
semantic_aesthetic.py
---------------------
Aesthetic scoring for video segments using a real LAION-Aesthetics v2 MLP
head on top of CLIP ViT-L/14 embeddings.

Replaces the previous placeholder that returned mean(abs(DINOv2 embedding)) * 5
which was essentially random noise.

The LAION aesthetic predictor is a small 5-layer MLP trained on ~176k human
preference ratings. It returns a score in [0, 10] which we normalise to [0, 1].

Requirements:
    pip install open_clip_torch torch
    The MLP weights are downloaded automatically on first use (~1 MB).

Environment variables:
    AESTHETIC_MODEL_DEVICE  override device ("cuda" / "cpu"), default: auto
"""

import urllib.request
from pathlib import Path
from typing import Dict, Any

import cv2
import numpy as np
import torch
import torch.nn as nn

# ---------------------------------------------------------------------------
# Device
# ---------------------------------------------------------------------------
_DEVICE = torch.device(
    str.__new__(str, __import__("os").environ.get("AESTHETIC_MODEL_DEVICE", ""))
    if __import__("os").environ.get("AESTHETIC_MODEL_DEVICE")
    else ("cuda" if torch.cuda.is_available() else "cpu")
)

# ---------------------------------------------------------------------------
# LAION Aesthetic Predictor MLP
# ---------------------------------------------------------------------------
_MLP_WEIGHTS_URL = (
    "https://github.com/christophschuhmann/"
    "improved-aesthetic-predictor/raw/main/sac%2Blogos%2Bava1-l14-linearMSE.pth"
)
_WEIGHTS_CACHE = Path(__file__).resolve().parent.parent / "style" / "aesthetic_mlp.pth"


class _AestheticMLP(nn.Module):
    def __init__(self, input_size: int = 768):
        super().__init__()
        self.layers = nn.Sequential(
            nn.Linear(input_size, 1024),
            nn.Dropout(0.2),
            nn.Linear(1024, 128),
            nn.Dropout(0.2),
            nn.Linear(128, 64),
            nn.Dropout(0.1),
            nn.Linear(64, 16),
            nn.Linear(16, 1),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.layers(x)


_AESTHETIC_MODEL: _AestheticMLP | None = None
_CLIP_MODEL = None
_CLIP_PREPROCESS = None


def _download_weights() -> Path:
    """Download MLP weights if not cached."""
    _WEIGHTS_CACHE.parent.mkdir(parents=True, exist_ok=True)
    if not _WEIGHTS_CACHE.exists():
        print("[aesthetic] Downloading LAION aesthetic MLP weights (~1 MB)...")
        urllib.request.urlretrieve(_MLP_WEIGHTS_URL, _WEIGHTS_CACHE)
        print("[aesthetic] Weights downloaded.")
    return _WEIGHTS_CACHE


def _load_aesthetic_model() -> _AestheticMLP:
    global _AESTHETIC_MODEL
    if _AESTHETIC_MODEL is None:
        weights_path = _download_weights()
        model = _AestheticMLP(input_size=768)
        state = torch.load(weights_path, map_location="cpu")
        model.load_state_dict(state)
        model.eval()
        _AESTHETIC_MODEL = model.to(_DEVICE)
    return _AESTHETIC_MODEL


def _load_clip():
    """Load CLIP ViT-L/14 for aesthetic embedding (separate from SigLIP)."""
    global _CLIP_MODEL, _CLIP_PREPROCESS
    if _CLIP_MODEL is None:
        try:
            import open_clip
            model, _, preprocess = open_clip.create_model_and_transforms(
                "ViT-L-14", pretrained="openai", device=_DEVICE
            )
            model.eval()
            _CLIP_MODEL = model
            _CLIP_PREPROCESS = preprocess
        except ImportError:
            raise ImportError(
                "open_clip_torch is required for aesthetic scoring. "
                "Install with: pip install open_clip_torch"
            )
    return _CLIP_MODEL, _CLIP_PREPROCESS


def _encode_for_aesthetic(frame_bgr: np.ndarray) -> torch.Tensor | None:
    """Encode a BGR frame into a CLIP ViT-L/14 embedding for the aesthetic MLP."""
    try:
        from PIL import Image
        model, preprocess = _load_clip()
        img_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        pil_img = Image.fromarray(img_rgb)
        tensor = preprocess(pil_img).unsqueeze(0).to(_DEVICE)
        with torch.no_grad():
            emb = model.encode_image(tensor)
            emb = emb / emb.norm(dim=-1, keepdim=True)
        return emb[0].cpu().float()
    except Exception as exc:
        print(f"[aesthetic] CLIP encoding failed: {exc}")
        return None


def score_aesthetic(frame_bgr: np.ndarray) -> float:
    """
    Return a real aesthetic quality score in [0, 1] for a BGR frame.

    Uses LAION-Aesthetics v2 MLP on top of CLIP ViT-L/14 embeddings.
    Raw score is in [0, 10]; we divide by 10 for pipeline compatibility.

    Falls back to 0.5 if any model fails.
    """
    emb = _encode_for_aesthetic(frame_bgr)
    if emb is None:
        return 0.5

    try:
        mlp = _load_aesthetic_model()
        with torch.no_grad():
            raw = mlp(emb.unsqueeze(0).to(_DEVICE))
        score_0_10 = float(raw.squeeze())
        return float(max(0.0, min(score_0_10 / 10.0, 1.0)))
    except Exception as exc:
        print(f"[aesthetic] MLP scoring failed: {exc}")
        return 0.5


def add_aesthetic_score_to_segment(seg: Dict[str, Any], frame_bgr: np.ndarray) -> Dict[str, Any]:
    """
    Attach a real aesthetic score to a segment dict.
    Called from semantic_siglip.py for every segment.
    """
    seg["aesthetic_score"] = score_aesthetic(frame_bgr)
    return seg