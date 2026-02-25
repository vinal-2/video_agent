import cv2
import torch
import numpy as np
from typing import Dict, Any

from transformers import AutoImageProcessor, AutoModel


_DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
_DINO_MODEL_NAME = "facebook/dinov2-base"
_DINO_MODEL = None
_DINO_PROCESSOR = None


def _load_dino():
    global _DINO_MODEL, _DINO_PROCESSOR
    if _DINO_MODEL is None:
        _DINO_PROCESSOR = AutoImageProcessor.from_pretrained(_DINO_MODEL_NAME)
        _DINO_MODEL = AutoModel.from_pretrained(_DINO_MODEL_NAME).to(_DEVICE).eval()
    return _DINO_MODEL, _DINO_PROCESSOR


def _compute_dino_embedding(frame_bgr: np.ndarray) -> torch.Tensor:
    model, processor = _load_dino()
    img_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
    inputs = processor(images=img_rgb, return_tensors="pt").to(_DEVICE)

    with torch.no_grad():
        outputs = model(**inputs)
        if hasattr(outputs, "pooler_output") and outputs.pooler_output is not None:
            emb = outputs.pooler_output[0]
        else:
            emb = outputs.last_hidden_state[:, 0, :]
        emb = emb / emb.norm(dim=-1, keepdim=True)
    return emb.cpu()


def _aesthetic_from_embedding(emb: torch.Tensor) -> float:
    """
    Simple heuristic aesthetic score:
    - use L2 norm stability + small nonlinearity
    - you can later replace this with a trained head
    """
    # emb is already normalized; use a dummy stable score
    # here we just return 1.0 as a placeholder for a "good" frame
    # but we can derive a bit of variation using mean absolute value
    score = float(torch.mean(torch.abs(emb)))
    # normalize roughly into [0, 1]
    return max(0.0, min(score * 5.0, 1.0))


def add_aesthetic_score_to_segment(seg: Dict[str, Any], frame_bgr: np.ndarray) -> Dict[str, Any]:
    emb = _compute_dino_embedding(frame_bgr)
    aesth = _aesthetic_from_embedding(emb)
    seg["aesthetic_score"] = aesth
    return seg
