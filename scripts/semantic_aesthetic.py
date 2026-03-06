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


# ---------------------------------------------------------------------------
# Template prompt library
# ---------------------------------------------------------------------------

TEMPLATE_PROMPTS: dict[str, list[str]] = {
    "concert": [
        "a performer singing on stage with dramatic lighting",
        "an energetic crowd at a live concert",
        "a close-up of a musician performing",
        "stage lights and visual effects at a concert",
        "a band performing on a large stage",
        "dramatic spotlights illuminating a performer",
    ],
    "travel": [
        "a scenic landscape with dramatic sky",
        "an establishing shot of a beautiful location",
        "a person exploring a stunning destination",
        "aerial or wide-angle view of a landmark",
        "golden hour light over a travel destination",
        "a breathtaking natural landscape",
    ],
    "food": [
        "a beautifully plated dish with vibrant colors",
        "a close-up of food with visible texture",
        "fresh ingredients being prepared in a kitchen",
        "a finished dish on a clean background",
        "steam rising from freshly cooked food",
        "an appetizing hero shot of a meal",
    ],
    "product": [
        "a product displayed on a clean minimal background",
        "a close-up of a product showing details and texture",
        "hands holding or demonstrating a product",
        "a product with clean professional lighting",
        "a product package or label clearly visible",
        "a beautifully lit product on a studio background",
    ],
    "grwm": [
        "a person applying makeup in front of a mirror",
        "a close-up of a beauty product being applied",
        "a person styling their hair getting ready",
        "a beauty transformation before and after",
        "a well-lit face during a makeup routine",
        "cosmetics and beauty products laid out neatly",
    ],
    "generic": [
        "a high quality well-composed video frame",
        "an interesting and visually appealing scene",
        "a sharp in-focus photograph with good lighting",
        "a cinematic and aesthetically pleasing shot",
    ],
}

# Template aliases — map STYLE_TEMPLATE env var values to prompt keys
TEMPLATE_ALIASES: dict[str, str] = {
    "event_concert":  "concert",
    "travel_reel":    "travel",
    "breakfast_food": "food",
    "product_style":  "product",
    "grwm_style":     "grwm",
    # UI display names (from frontend selectors)
    "Event Concert":      "concert",
    "Travel Reel":        "travel",
    "Food Content":       "food",
    "Product Showcase":   "product",
    "GRWM":               "grwm",
    "Get Ready With Me":  "grwm",
    # Default fallback
    "default":        "generic",
}


def get_template_key(template_name: str) -> str:
    """Resolve a STYLE_TEMPLATE name to a TEMPLATE_PROMPTS key."""
    if not template_name:
        return "generic"
    # Exact match
    if template_name in TEMPLATE_ALIASES:
        return TEMPLATE_ALIASES[template_name]
    # Case-insensitive partial match
    lower = template_name.lower()
    for alias, key in TEMPLATE_ALIASES.items():
        if alias.lower() in lower or lower in alias.lower():
            return key
    return "generic"


# ---------------------------------------------------------------------------
# Template-aware scoring
# ---------------------------------------------------------------------------

# Cache: (template_key, model_id) → normalised text embeddings tensor
_template_emb_cache: dict[tuple[str, str], torch.Tensor] = {}

_MODEL_ID = "clip_vit_l_14"  # static key — only one model variant used here


def _get_text_embeddings(prompts: list[str], template_key: str) -> torch.Tensor:
    """Encode text prompts via CLIP ViT-L/14. Cached per template key."""
    cache_key = (template_key, _MODEL_ID)
    if cache_key in _template_emb_cache:
        return _template_emb_cache[cache_key]

    model, _ = _load_clip()
    import open_clip as _oc
    tokens = _oc.tokenize(prompts).to(_DEVICE)
    with torch.no_grad():
        text_embs = model.encode_text(tokens)
        text_embs = text_embs / text_embs.norm(dim=-1, keepdim=True)
    _template_emb_cache[cache_key] = text_embs.cpu().float()
    return _template_emb_cache[cache_key]


def score_clip_template(
    frames,                    # List of PIL Images (already extracted by caller)
    template_key: str = "generic",
    model=None,                # unused — kept for signature compatibility
    processor=None,            # unused — kept for signature compatibility
) -> dict:
    """
    Score a clip against template-specific text prompts using CLIP ViT-L/14.

    Returns a dict:
    {
        "template_score": float,       # 0-1, sigmoid-stretched max similarity
        "template_key":   str,
        "prompt_scores":  list[float], # per-prompt clip-level similarities
        "best_prompt":    str,
        "frame_scores":   list[float], # per-frame max similarity
    }
    """
    empty = {
        "template_score": 0.0,
        "template_key":   template_key,
        "prompt_scores":  [],
        "best_prompt":    "",
        "frame_scores":   [],
    }
    if not frames:
        return empty

    prompts = TEMPLATE_PROMPTS.get(template_key, TEMPLATE_PROMPTS["generic"])

    try:
        clip_model, clip_preprocess = _load_clip()

        # Encode image frames
        tensors = torch.stack([clip_preprocess(img) for img in frames]).to(_DEVICE)
        with torch.no_grad():
            image_embs = clip_model.encode_image(tensors)
            image_embs = image_embs / image_embs.norm(dim=-1, keepdim=True)

        # Encode text prompts (cached)
        text_embs = _get_text_embeddings(prompts, template_key).to(_DEVICE)

        # Per-frame: max similarity across all prompts
        frame_sim = (image_embs @ text_embs.T)        # (N_frames, N_prompts)
        frame_scores = frame_sim.max(dim=1).values.cpu().tolist()

        # Clip-level: pool frames then score against each prompt
        clip_emb   = image_embs.mean(dim=0, keepdim=True)           # (1, 768)
        clip_emb   = clip_emb / clip_emb.norm(dim=-1, keepdim=True)
        prompt_sims = (clip_emb @ text_embs.T).squeeze(0).cpu().tolist()
        if isinstance(prompt_sims, float):
            prompt_sims = [prompt_sims]

        best_idx    = int(max(range(len(prompt_sims)), key=lambda i: prompt_sims[i]))
        raw_score   = float(prompt_sims[best_idx])

        # sigmoid(sim * 10 - 5) stretches raw cosine similarity [0.15-0.4] to [0-1]
        import math
        template_score = 1.0 / (1.0 + math.exp(-(raw_score * 10.0 - 5.0)))

        return {
            "template_score": float(max(0.0, min(template_score, 1.0))),
            "template_key":   template_key,
            "prompt_scores":  [float(s) for s in prompt_sims],
            "best_prompt":    prompts[best_idx],
            "frame_scores":   [float(s) for s in frame_scores],
        }

    except torch.cuda.OutOfMemoryError:
        print("[aesthetic] CUDA OOM in score_clip_template — retrying on CPU")
        try:
            clip_model.cpu()
            tensors = tensors.cpu()
            with torch.no_grad():
                image_embs = clip_model.encode_image(tensors)
                image_embs = image_embs / image_embs.norm(dim=-1, keepdim=True)
            text_embs  = _get_text_embeddings(prompts, template_key)
            clip_emb   = image_embs.mean(dim=0, keepdim=True)
            clip_emb   = clip_emb / clip_emb.norm(dim=-1, keepdim=True)
            prompt_sims = (clip_emb @ text_embs.T).squeeze(0).tolist()
            if isinstance(prompt_sims, float):
                prompt_sims = [prompt_sims]
            best_idx   = int(max(range(len(prompt_sims)), key=lambda i: prompt_sims[i]))
            raw_score  = float(prompt_sims[best_idx])
            import math
            template_score = 1.0 / (1.0 + math.exp(-(raw_score * 10.0 - 5.0)))
            return {
                "template_score": float(max(0.0, min(template_score, 1.0))),
                "template_key":   template_key,
                "prompt_scores":  [float(s) for s in prompt_sims],
                "best_prompt":    prompts[best_idx],
                "frame_scores":   [],
            }
        except Exception as exc2:
            print(f"[aesthetic] CPU fallback failed: {exc2}")
            return empty
    except Exception as exc:
        print(f"[aesthetic] score_clip_template failed: {exc}")
        return empty


def score_clip_combined(
    frames,
    template_key: str = "generic",
    aesthetic_weight: float = 0.4,
    template_weight: float = 0.6,
) -> dict:
    """
    Combine LAION aesthetic score with template-aware CLIP score.

    frames — List of PIL Images.
    Returns dict with combined_score, aesthetic_score, template_score,
    template_key, best_prompt, frame_scores.
    """
    assert abs(aesthetic_weight + template_weight - 1.0) < 1e-6, \
        "aesthetic_weight + template_weight must equal 1.0"

    # Template score (PIL frames → CLIP text-image matching)
    tmpl = score_clip_template(frames, template_key=template_key)

    # Aesthetic score: use midframe converted to BGR
    aesthetic = 0.5
    if frames:
        try:
            mid_pil = frames[len(frames) // 2]
            mid_rgb = np.array(mid_pil)
            mid_bgr = mid_rgb[:, :, ::-1].copy()
            aesthetic = score_aesthetic(mid_bgr)
        except Exception as exc:
            print(f"[aesthetic] aesthetic score failed in combined: {exc}")

    combined = aesthetic_weight * aesthetic + template_weight * tmpl["template_score"]

    return {
        "combined_score":  float(max(0.0, min(combined, 1.0))),
        "aesthetic_score": float(aesthetic),
        "template_score":  tmpl["template_score"],
        "template_key":    tmpl["template_key"],
        "best_prompt":     tmpl["best_prompt"],
        "frame_scores":    tmpl["frame_scores"],
    }


def add_aesthetic_score_to_segment(seg: Dict[str, Any], frame_bgr: np.ndarray) -> Dict[str, Any]:
    """
    Attach a real aesthetic score to a segment dict.
    Called from semantic_siglip.py for every segment.
    """
    seg["aesthetic_score"] = score_aesthetic(frame_bgr)
    return seg