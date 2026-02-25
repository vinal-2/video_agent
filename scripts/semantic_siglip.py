"""
semantic_siglip.py
------------------
Batched GPU enrichment for video segments.

Key fix in this version:
  motion_smoothness was causing 1645s enrichment time for 53 segments
  (53 × ~31s each). Root cause: optical flow opened a new VideoCapture
  per segment even for sub-2s clips where the metric is meaningless.

  Fix: segments shorter than MOTION_SMOOTHNESS_MIN_DURATION (default 3.0s)
  skip motion smoothness entirely and receive a neutral score of 0.5.
  For travel_reel (max_segment=2.3s) this means zero smoothness calls.
  For grwm_style (max_segment=8.5s) it still runs on longer clips.
"""

import os
import logging
import time
import warnings
from collections import defaultdict
from typing import Dict, Any, List, Optional, Tuple

import torch
import cv2
import numpy as np
from transformers import AutoImageProcessor, SiglipVisionModel

log = logging.getLogger(__name__)

_DEVICE            = "cuda" if torch.cuda.is_available() else "cpu"
_SIGLIP_MODEL_NAME = "google/siglip-base-patch16-224"
_SIGLIP_BATCH_SIZE = int(os.environ.get("SIGLIP_BATCH_SIZE", "16"))

# Segments shorter than this (seconds) skip motion smoothness.
# travel_reel max_segment=2.3 → all skipped (saves ~1640s per 53 segments)
# grwm_style  max_segment=8.5 → runs on longer clips where it matters
MOTION_SMOOTHNESS_MIN_DURATION = float(
    os.environ.get("MOTION_SMOOTHNESS_MIN_DURATION", "3.0")
)

_SIGLIP_MODEL:     Optional[SiglipVisionModel]  = None
_SIGLIP_PROCESSOR: Optional[AutoImageProcessor] = None


# ── SigLIP ────────────────────────────────────────────────────────────────────

def _load_siglip():
    global _SIGLIP_MODEL, _SIGLIP_PROCESSOR
    if _SIGLIP_MODEL is None:
        print("[siglip] Loading SigLIP model...")
        _SIGLIP_PROCESSOR = AutoImageProcessor.from_pretrained(_SIGLIP_MODEL_NAME)
        _SIGLIP_MODEL     = SiglipVisionModel.from_pretrained(_SIGLIP_MODEL_NAME).to(_DEVICE).eval()
    return _SIGLIP_MODEL, _SIGLIP_PROCESSOR


def _encode_frames_batch_siglip(frames_bgr: List[np.ndarray]) -> torch.Tensor:
    """Encode list of BGR frames → (N, 768) L2-normalised tensor, one GPU pass."""
    model, processor = _load_siglip()
    imgs_rgb = [cv2.cvtColor(f, cv2.COLOR_BGR2RGB) for f in frames_bgr]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        inputs = processor(images=imgs_rgb, return_tensors="pt").to(_DEVICE)
    with torch.no_grad():
        outputs = model(**inputs)
        embs    = outputs.pooler_output
        embs    = embs / embs.norm(dim=-1, keepdim=True)
    return embs.cpu()


def _cosine_sim(a: torch.Tensor, b: torch.Tensor) -> float:
    return float((a @ b) / (a.norm() * b.norm() + 1e-8))


# ── LAION Aesthetic Scorer (batched) ──────────────────────────────────────────

_AESTHETIC_MLP    = None
_CLIP_MODEL       = None
_CLIP_PREPROCESS  = None
_AESTHETIC_LOADED = False


def _load_aesthetic_models():
    global _AESTHETIC_MLP, _CLIP_MODEL, _CLIP_PREPROCESS, _AESTHETIC_LOADED
    if _AESTHETIC_LOADED:
        return _AESTHETIC_MLP is not None
    _AESTHETIC_LOADED = True
    try:
        import urllib.request
        import torch.nn as nn
        import open_clip
        from pathlib import Path

        WEIGHTS_URL  = ("https://github.com/christophschuhmann/"
                        "improved-aesthetic-predictor/raw/main/sac%2Blogos%2Bava1-l14-linearMSE.pth")
        weights_path = Path(__file__).resolve().parent.parent / "style" / "aesthetic_mlp.pth"
        weights_path.parent.mkdir(exist_ok=True)
        if not weights_path.exists():
            print("[aesthetic] Downloading LAION MLP weights...")
            urllib.request.urlretrieve(WEIGHTS_URL, weights_path)

        class _MLP(nn.Module):
            def __init__(self):
                super().__init__()
                self.layers = nn.Sequential(
                    nn.Linear(768, 1024), nn.Dropout(0.2),
                    nn.Linear(1024, 128), nn.Dropout(0.2),
                    nn.Linear(128, 64),   nn.Dropout(0.1),
                    nn.Linear(64, 16),    nn.Linear(16, 1),
                )
            def forward(self, x): return self.layers(x)

        mlp = _MLP()
        mlp.load_state_dict(torch.load(weights_path, map_location="cpu"))
        mlp.eval().to(_DEVICE)
        _AESTHETIC_MLP = mlp

        clip_model, _, preprocess = open_clip.create_model_and_transforms(
            "ViT-L-14", pretrained="openai", device=_DEVICE
        )
        clip_model.eval()
        _CLIP_MODEL      = clip_model
        _CLIP_PREPROCESS = preprocess
        print("[aesthetic] LAION aesthetic scorer ready.")
        return True
    except Exception as exc:
        print(f"[aesthetic] Scorer unavailable: {exc}")
        return False


def _score_aesthetic_batch(frames_bgr: List[np.ndarray]) -> List[float]:
    """Score list of frames → [0,1] in a single GPU pass."""
    if not _load_aesthetic_models():
        return [0.5] * len(frames_bgr)
    try:
        from PIL import Image
        pil_imgs = [Image.fromarray(cv2.cvtColor(f, cv2.COLOR_BGR2RGB)) for f in frames_bgr]
        tensors  = torch.stack([_CLIP_PREPROCESS(img) for img in pil_imgs]).to(_DEVICE)
        with torch.no_grad():
            embs   = _CLIP_MODEL.encode_image(tensors)
            embs   = embs / embs.norm(dim=-1, keepdim=True)
            scores = _AESTHETIC_MLP(embs.float()).squeeze(-1)
        return [float(max(0.0, min(s / 10.0, 1.0))) for s in scores.cpu()]
    except Exception as exc:
        log.warning(f"[aesthetic] Batch failed: {exc}")
        return [0.5] * len(frames_bgr)


# ── Shared VideoCapture frame reader ──────────────────────────────────────────

def _read_midframes_for_video(
    video_path: str,
    segments:   List[Tuple[int, float, float]],
) -> Dict[int, Optional[np.ndarray]]:
    """Open video once, read midpoint frames for all segments. Returns {idx: frame}."""
    cap = cv2.VideoCapture(os.fspath(video_path))
    if not cap.isOpened():
        return {idx: None for idx, _, _ in segments}
    fps    = cap.get(cv2.CAP_PROP_FPS) or 25.0
    result = {}
    for (idx, start, end) in sorted(segments, key=lambda x: int(((x[1]+x[2])/2)*fps)):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(((start + end) / 2.0) * fps))
        ok, frame = cap.read()
        result[idx] = frame if ok else None
    cap.release()
    return result


# ── Heuristic tags ────────────────────────────────────────────────────────────

def _classify_indoor_outdoor(frame_bgr: np.ndarray) -> str:
    h   = frame_bgr.shape[0]
    top = frame_bgr[:int(h * 0.20), :]
    ht  = cv2.cvtColor(top,       cv2.COLOR_BGR2HSV).astype(np.float32)
    hf  = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV).astype(np.float32)
    sky = ((ht[:,:,0]>=95)&(ht[:,:,0]<=130)&(ht[:,:,2]>=150)&(ht[:,:,1]>=30)&(ht[:,:,1]<=200))
    return "outdoor" if float(sky.mean()) > 0.15 or float(hf[:,:,1].mean()) > 80 else "indoor"


def _enrich_tags(frame_bgr: np.ndarray) -> List[str]:
    tags = []
    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    mb   = float(gray.mean())
    if mb > 175:   tags.append("bright")
    elif mb < 65:  tags.append("dark")
    tags.append(_classify_indoor_outdoor(frame_bgr))
    cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    small   = cv2.resize(frame_bgr, (320, 180))
    if len(cascade.detectMultiScale(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), 1.1, 3)) > 0:
        tags.append("face")
    img_f  = frame_bgr.astype(np.float32) / 255.0
    warmth = float(img_f[:,:,2].mean()) - float(img_f[:,:,0].mean())
    if warmth > 0.06:    tags.append("warm")
    elif warmth < -0.06: tags.append("cool")
    edges = cv2.Canny(gray, 50, 150)
    ed    = float(edges.mean())
    if ed > 18:   tags.append("busy")
    elif ed < 6:  tags.append("minimal")
    return tags


def _estimate_blur(frame_bgr: np.ndarray) -> float:
    return float(cv2.Laplacian(cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY), cv2.CV_64F).var())


def _brightness_adaptive_blur_threshold(frame_bgr: np.ndarray, base_threshold: float) -> float:
    """
    Scale the blur threshold down for dark frames.

    Laplacian variance is inherently lower for dark frames — a sharp night
    sky scores 3-8, a sharp daylit scene scores 200+. Applying a fixed
    threshold calibrated on bright reference footage will mark every dark
    frame as blurry even when it is perfectly sharp.

    Multipliers match the brightness bands observed in practice:
      very dark  (<60):   ×0.12  → threshold ~15  (night sky, silhouettes)
      dark       (60-100):×0.20  → threshold ~25  (night scenes)
      dim        (100-140):×0.35 → threshold ~43  (golden hour, overcast)
      medium     (140-180):×0.60 → threshold ~74  (normal daylight)
      bright     (>180):  ×1.00  → full threshold (direct sun, studio)
    """
    gray       = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    brightness = float(gray.mean())
    if   brightness < 60:   mult = 0.12
    elif brightness < 100:  mult = 0.20
    elif brightness < 140:  mult = 0.35
    elif brightness < 180:  mult = 0.60
    else:                   mult = 1.00
    return base_threshold * mult


# ── Motion smoothness ─────────────────────────────────────────────────────────

def _estimate_motion_smoothness(video_path: str, start: float, end: float) -> float:
    """Optical flow smoothness. Only called for segments >= MOTION_SMOOTHNESS_MIN_DURATION."""
    cap = cv2.VideoCapture(os.fspath(video_path))
    if not cap.isOpened():
        return 0.5
    fps     = cap.get(cv2.CAP_PROP_FPS) or 25.0
    indices = np.linspace(int(start * fps), int(end * fps) - 2, 4).astype(int)
    mags    = []
    for idx in indices:
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ok1, f1 = cap.read()
        ok2, f2 = cap.read()
        if not ok1 or not ok2:
            continue
        g1 = cv2.resize(cv2.cvtColor(f1, cv2.COLOR_BGR2GRAY), (160, 90))
        g2 = cv2.resize(cv2.cvtColor(f2, cv2.COLOR_BGR2GRAY), (160, 90))
        flow = cv2.calcOpticalFlowFarneback(g1, g2, None, 0.5, 2, 12, 2, 5, 1.1, 0)
        mags.append(float(np.sqrt(flow[...,0]**2 + flow[...,1]**2).mean()))
    cap.release()
    if len(mags) < 2:
        return 0.5
    arr = np.array(mags)
    return float(max(0.0, min(1.0 - (arr.std() / (arr.mean() + 1e-6)) * 0.5, 1.0)))


# ── Vision tagger — Moondream2 GGUF via llama-cpp-python ─────────────────────
#
# Activate with:  set VISION_TAGGER_MODEL=moondream
#
# Model path is resolved from LM Studio's cache by default.
# Override with:  set MOONDREAM_MODEL_DIR=C:\path\to\model\folder
#
# The April 2025 4-bit GGUF ships two files:
#   moondream2-text-model-f16.gguf   — language model
#   moondream2-mmproj-f16.gguf       — multimodal projector (vision encoder)
# Both must be present in the same folder.

_VISION_TAGGER        = None
_VISION_TAGGER_LOADED = False

# Prompt tuned for the April 2025 model's output style.
# Short and direct — longer prompts cause the model to ramble.
_MOONDREAM_PROMPT = (
    "Describe this video frame in one sentence covering: "
    "setting (indoor/outdoor), lighting (bright/dark/warm/cool/golden hour), "
    "subject (person/people/food/product/landscape), "
    "shot type (close-up/wide shot/medium shot), "
    "and energy (calm/dynamic/busy)."
)


def _find_moondream_gguf_paths():
    """
    Locate the two GGUF files. Checks MOONDREAM_MODEL_DIR env var first,
    then falls back to LM Studio's default cache location.
    """
    from pathlib import Path

    # Explicit override
    override = os.environ.get("MOONDREAM_MODEL_DIR")
    if override:
        folder = Path(override)
    else:
        # LM Studio default on Windows
        folder = (Path.home() / ".cache" / "lm-studio" / "models"
                  / "moondream" / "moondream-2b-2025-04-14-4bit")

    if not folder.exists():
        raise FileNotFoundError(f"Moondream model folder not found: {folder}\n"
                                f"Set MOONDREAM_MODEL_DIR to the folder containing the .gguf files.")

    # Find text model and projector — names may vary slightly across versions
    text_candidates = list(folder.glob("*text-model*.gguf")) + list(folder.glob("*text*.gguf"))
    proj_candidates = list(folder.glob("*mmproj*.gguf")) + list(folder.glob("*proj*.gguf"))

    if not text_candidates:
        raise FileNotFoundError(f"No text model .gguf found in {folder}")
    if not proj_candidates:
        raise FileNotFoundError(f"No mmproj .gguf found in {folder}")

    return str(text_candidates[0]), str(proj_candidates[0])


def _load_vision_tagger():
    global _VISION_TAGGER, _VISION_TAGGER_LOADED
    if _VISION_TAGGER_LOADED:
        return _VISION_TAGGER
    _VISION_TAGGER_LOADED = True

    model_name = os.environ.get("VISION_TAGGER_MODEL", "none").strip().lower()
    if model_name in ("none", ""):
        return None

    if model_name not in ("moondream", "moondream2"):
        print(f"[vision_tagger] Unknown model '{model_name}'. "
              "Valid: 'moondream'. Skipping vision tagger.")
        return None

    try:
        import sys as _sys, os as _os
        # Set before import — suppresses C-level stdout on Windows
        _os.environ["LLAMA_CPP_VERBOSE"] = "0"
        from llama_cpp import Llama
        from llama_cpp.llama_chat_format import Llava15ChatHandler

        text_path, proj_path = _find_moondream_gguf_paths()
        print(f"[vision_tagger] Loading Moondream2 GGUF (CPU)...")

        # BUG FIX: the previous approach redirected sys.stderr (Python-level) but
        # llama.cpp writes directly to C-level file descriptors 1 (stdout) and 2
        # (stderr), bypassing Python entirely.  Use os.dup2 to redirect at the fd
        # level so the verbose "add_text:" / "encoding image slice..." noise is
        # suppressed rather than leaking into the pipeline log stream.
        def _suppress_c_stdio():
            """Return (saved_stdout_fd, saved_stderr_fd, devnull_fd) for restore."""
            _sys.stdout.flush()
            _sys.stderr.flush()
            devnull_fd  = _os.open(_os.devnull, _os.O_WRONLY)
            saved_out   = _os.dup(1)
            saved_err   = _os.dup(2)
            _os.dup2(devnull_fd, 1)
            _os.dup2(devnull_fd, 2)
            _os.close(devnull_fd)
            return saved_out, saved_err

        def _restore_c_stdio(saved_out: int, saved_err: int):
            _os.dup2(saved_out, 1)
            _os.dup2(saved_err, 2)
            _os.close(saved_out)
            _os.close(saved_err)

        saved_out, saved_err = _suppress_c_stdio()
        try:
            handler = Llava15ChatHandler(clip_model_path=proj_path, verbose=False)
            llm     = Llama(
                model_path   = text_path,
                chat_handler = handler,
                n_ctx        = 2048,
                n_gpu_layers = 0,
                verbose      = False,
            )
        finally:
            _restore_c_stdio(saved_out, saved_err)

        def _tag(frame_bgr: np.ndarray) -> List[str]:
            import base64, sys as _sys2, os as _os2
            ok, buf = cv2.imencode(".jpg", frame_bgr, [cv2.IMWRITE_JPEG_QUALITY, 85])
            if not ok:
                return []
            b64      = base64.b64encode(buf.tobytes()).decode("utf-8")
            data_uri = f"data:image/jpeg;base64,{b64}"
            # Suppress C-level fd output per frame
            _sys2.stdout.flush()
            _sys2.stderr.flush()
            _devnull_fd = _os2.open(_os2.devnull, _os2.O_WRONLY)
            _saved_out  = _os2.dup(1)
            _saved_err  = _os2.dup(2)
            _os2.dup2(_devnull_fd, 1)
            _os2.dup2(_devnull_fd, 2)
            _os2.close(_devnull_fd)
            try:
                response = llm.create_chat_completion(
                    messages=[{
                        "role": "user",
                        "content": [
                            {"type": "text",      "text": _MOONDREAM_PROMPT},
                            {"type": "image_url", "image_url": {"url": data_uri}},
                        ],
                    }],
                    max_tokens  = 80,
                    temperature = 0.1,
                )
            finally:
                _os2.dup2(_saved_out, 1)
                _os2.dup2(_saved_err, 2)
                _os2.close(_saved_out)
                _os2.close(_saved_err)
            caption = response["choices"][0]["message"]["content"]
            return _caption_to_tags(caption)

        # BUG FIX: Llama.__del__ throws TypeError at interpreter exit because
        # Python module teardown sets C-extension functions to None before GC
        # runs.  Register an atexit handler to explicitly close the model while
        # the interpreter is still fully operational.
        import atexit as _atexit
        _atexit.register(lambda: llm.close() if hasattr(llm, "close") else None)

        _VISION_TAGGER = _tag
        print("[vision_tagger] Moondream2 GGUF ready (~33s/frame on CPU)")

    except ImportError:
        print("[vision_tagger] llama-cpp-python not installed. "
              "Run: pip install llama-cpp-python")
    except FileNotFoundError as exc:
        print(f"[vision_tagger] {exc}")
    except Exception as exc:
        print(f"[vision_tagger] Failed to load: {exc}")

    return _VISION_TAGGER


def _caption_to_tags(caption: str) -> List[str]:
    """
    Map Moondream2 caption text to pipeline tags.
    Keyword list tuned for April 2025 model output phrasing.
    """
    c    = caption.lower()
    tags = []

    # Map of (phrase_in_caption → pipeline_tag)
    # Ordered so more specific phrases come before generic ones
    km = {
        # Lighting / time of day
        "golden hour":    "golden_hour",
        "golden light":   "golden_hour",
        "sunset":         "golden_hour",
        "sunrise":        "golden_hour",
        "well-lit":       "bright",
        "brightly lit":   "bright",
        "bright":         "bright",
        "sunny":          "bright",
        "low light":      "dark",
        "dimly lit":      "dark",
        "dark":           "dark",
        "night":          "dark",
        # Colour mood
        "warm light":     "warm",
        "warm tones":     "warm",
        "warm":           "warm",
        "cool tones":     "cool",
        "cool light":     "cool",
        "blue tones":     "cool",
        "cool":           "cool",
        # Setting
        "outdoors":       "outdoor",
        "outdoor":        "outdoor",
        "outside":        "outdoor",
        "open air":       "outdoor",
        "nature":         "outdoor",
        "beach":          "outdoor",
        "street":         "outdoor",
        "city":           "outdoor",
        "park":           "outdoor",
        "indoors":        "indoor",
        "indoor":         "indoor",
        "inside":         "indoor",
        "room":           "indoor",
        "kitchen":        "indoor",
        "restaurant":     "indoor",
        "studio":         "indoor",
        # Subject
        "close-up":       "close_up",
        "close up":       "close_up",
        "closeup":        "close_up",
        "macro":          "close_up",
        "wide shot":      "wide_shot",
        "wide angle":     "wide_shot",
        "establishing":   "wide_shot",
        "medium shot":    "medium_shot",
        "mid shot":       "medium_shot",
        "portrait":       "face",
        "face":           "face",
        "faces":          "face",
        "person":         "person",
        "people":         "person",
        "crowd":          "person",
        "group":          "person",
        "food":           "product",
        "dish":           "product",
        "meal":           "product",
        "product":        "product",
        # Energy / composition
        "dynamic":        "high_energy",
        "energetic":      "high_energy",
        "action":         "high_energy",
        "fast-paced":     "high_energy",
        "busy":           "busy",
        "crowded":        "busy",
        "cluttered":      "busy",
        "calm":           "minimal",
        "serene":         "minimal",
        "minimalist":     "minimal",
        "clean":          "minimal",
        "simple":         "minimal",
        # Cinematic quality
        "cinematic":      "cinematic",
        "bokeh":          "cinematic",
        "shallow depth":  "cinematic",
        "blurred background": "cinematic",
    }

    for phrase, tag in km.items():
        if phrase in c and tag not in tags:
            tags.append(tag)

    return tags


# ── Main enrichment entry point ───────────────────────────────────────────────

def enrich_segments_with_siglip(
    segments:      List[Dict[str, Any]],
    style_profile: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """
    Enrich all segments. Batched GPU for SigLIP + aesthetic.
    Motion smoothness skipped for segments < MOTION_SMOOTHNESS_MIN_DURATION.
    """
    if not segments:
        return segments

    t0 = time.perf_counter()

    style_emb = None
    tag_bias  = {}
    blur_thr  = 80

    if style_profile:
        se = style_profile.get("siglip_style_embedding")
        if se is not None:
            style_emb = torch.tensor(se, dtype=torch.float32)
        tag_bias = style_profile.get("tag_bias", {})
        blur_thr = int(style_profile.get("blur_threshold", 80))

    # Group by source video
    video_to_segs: Dict[str, List[Tuple[int, float, float]]] = defaultdict(list)
    for i, seg in enumerate(segments):
        vp = seg.get("video_path", "")
        if vp and os.path.exists(os.fspath(vp)):
            video_to_segs[vp].append((i, float(seg.get("start", 0)), float(seg.get("end", 1))))

    # Read frames
    print(f"  Reading frames from {len(video_to_segs)} source videos...")
    all_frames: Dict[int, Optional[np.ndarray]] = {}
    for vp, segs_for_video in video_to_segs.items():
        all_frames.update(_read_midframes_for_video(vp, segs_for_video))

    valid_indices = [i for i, f in all_frames.items() if f is not None]
    valid_frames  = [all_frames[i] for i in valid_indices]

    # Batch SigLIP
    siglip_embs: Dict[int, torch.Tensor] = {}
    if valid_frames:
        print(f"  SigLIP batch encoding {len(valid_frames)} frames (batch_size={_SIGLIP_BATCH_SIZE})...")
        all_embs = []
        for bs in range(0, len(valid_frames), _SIGLIP_BATCH_SIZE):
            all_embs.append(_encode_frames_batch_siglip(valid_frames[bs:bs+_SIGLIP_BATCH_SIZE]))
        embs_tensor = torch.cat(all_embs, dim=0)
        for j, idx in enumerate(valid_indices):
            siglip_embs[idx] = embs_tensor[j]

    # Batch aesthetic
    aesthetic_scores: Dict[int, float] = {}
    if valid_frames:
        print(f"  Aesthetic batch scoring {len(valid_frames)} frames...")
        all_scores = []
        for bs in range(0, len(valid_frames), _SIGLIP_BATCH_SIZE):
            all_scores.extend(_score_aesthetic_batch(valid_frames[bs:bs+_SIGLIP_BATCH_SIZE]))
        for j, idx in enumerate(valid_indices):
            aesthetic_scores[idx] = all_scores[j]

    vision_tagger      = _load_vision_tagger()
    skipped_smoothness = 0
    ran_smoothness     = 0

    # Vision tagger is slow (~33s/frame on CPU).
    # Only run on the top N segments by SigLIP similarity — enough to enrich
    # the candidate pool without running on every segment.
    # Default: top 15. Override with VISION_TAGGER_MAX_SEGMENTS env var.
    VISION_MAX = int(os.environ.get("VISION_TAGGER_MAX_SEGMENTS", "15"))
    if vision_tagger is not None:
        # Rank by SigLIP similarity — already computed above
        sim_ranked = sorted(
            [i for i in valid_indices if siglip_embs.get(i) is not None],
            key=lambda i: _cosine_sim(siglip_embs[i], style_emb) if style_emb is not None else 0.0,
            reverse=True,
        )
        vision_eligible = set(sim_ranked[:VISION_MAX])
        print(f"  Vision tagging top {len(vision_eligible)} segments (of {len(segments)})...")
    else:
        vision_eligible = set()

    for i, seg in enumerate(segments):
        frame = all_frames.get(i)

        if frame is None:
            seg.update({"tags": [], "style_similarity": 0.0, "is_blurry": False,
                        "blur_score": 999.0, "aesthetic_score": 0.5, "motion_smoothness": 0.5})
            continue

        blur_val      = _estimate_blur(frame)
        adaptive_thr  = _brightness_adaptive_blur_threshold(frame, blur_thr)
        is_blurry     = blur_val < adaptive_thr

        emb       = siglip_embs.get(i)
        style_sim = 0.0
        if emb is not None and style_emb is not None:
            style_sim = _cosine_sim(emb, style_emb)

        tags = _enrich_tags(frame)

        if vision_tagger is not None and i in vision_eligible:
            try:
                for t in vision_tagger(frame):
                    if t not in tags:
                        tags.append(t)
            except Exception as exc:
                log.warning(f"[vision_tagger] seg {i}: {exc}")

        if tag_bias:
            style_sim += 0.1 * sum(float(tag_bias.get(t, 0.0)) for t in tags)

        aesthetic    = aesthetic_scores.get(i, 0.5)
        seg_duration = float(seg.get("end", 1)) - float(seg.get("start", 0))
        vp           = seg.get("video_path", "")

        if seg_duration >= MOTION_SMOOTHNESS_MIN_DURATION and vp:
            smoothness = _estimate_motion_smoothness(vp, float(seg.get("start", 0)), float(seg.get("end", 1)))
            ran_smoothness += 1
        else:
            smoothness = 0.5
            skipped_smoothness += 1

        seg["tags"]              = tags
        seg["style_similarity"]  = float(style_sim)
        seg["is_blurry"]         = is_blurry
        seg["blur_score"]        = round(blur_val, 1)
        seg["aesthetic_score"]   = aesthetic
        seg["motion_smoothness"] = smoothness

    elapsed = time.perf_counter() - t0
    print(f"  Enrichment complete — {elapsed:.1f}s  "
          f"(smoothness: {ran_smoothness} ran, {skipped_smoothness} skipped)")
    return segments