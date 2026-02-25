"""
llm_planner.py
--------------
Sends candidate segments to Qwen2.5-7B-Instruct (via LM Studio) for
edit planning. Returns a reordered + annotated segment list.

Fixes in this version:
  1. Robust response parsing — handles Qwen2.5 response format correctly,
     strips markdown code fences, falls back gracefully on any parse error.
  2. Better prompt — explicitly tells the model to return ONLY JSON with
     no preamble, explanation, or markdown. Qwen2.5 was adding text before
     the JSON which caused the 'choices' KeyError.
  3. Sends only the top 20 segments by style_score to keep the prompt short
     and reduce the chance of the model getting confused by a huge list.
  4. Validates each returned segment has required fields before accepting it.
  5. Timeout increased to 30s — Qwen2.5-7B can be slow on first inference.
"""

import json
import os
import re
import requests
from typing import List, Dict, Any

LMSTUDIO_BASE = "http://localhost:1234"
LMSTUDIO_URL  = f"{LMSTUDIO_BASE}/v1/chat/completions"

# Maximum segments to send — keeps prompt tight and response reliable
MAX_SEGMENTS_TO_LLM = 20

# Substrings that identify vision/embedding models — these cannot handle
# text-only planning tasks and must be skipped during auto-discovery.
_NON_TEXT_PATTERNS = ("moondream", "embed", "nomic", "mmproj", "whisper", "clip")


def _is_text_model(model_id: str) -> bool:
    lower = model_id.lower()
    return not any(p in lower for p in _NON_TEXT_PATTERNS)


def _discover_model_id(timeout: int = 5) -> str:
    """
    BUG FIX: picking models[0] selected Moondream (a vision model) when multiple
    models are loaded in LM Studio.  Moondream expects an image, so sending it a
    text-only planning payload causes 'Channel Error' → 400.

    Resolution order:
      1. LLM_PLANNER_MODEL env var — explicit override, always respected
      2. /v1/models filtered to text-only models (skip vision/embedding models)
      3. Hard-coded fallback 'lmstudio-model' if LM Studio is unreachable
    """
    # 1. Explicit override
    override = os.environ.get("LLM_PLANNER_MODEL", "").strip()
    if override:
        return override

    # 2. Auto-discover — prefer text instruction/chat models
    try:
        resp = requests.get(f"{LMSTUDIO_BASE}/v1/models", timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        models = [m["id"] for m in data.get("data", []) if _is_text_model(m.get("id", ""))]
        if models:
            return models[0]
        # All models were filtered out — fall back to first available
        all_models = [m["id"] for m in data.get("data", [])]
        if all_models:
            return all_models[0]
    except Exception:
        pass
    return "lmstudio-model"

SYSTEM_PROMPT = """You are an expert Instagram video editor.

Given a list of video segments with scores and tags, output the best edit sequence.

CRITICAL: You must ONLY use video_path values that appear EXACTLY in the input segments list.
Do NOT invent, modify, or shorten any video_path. Copy them character-for-character.

RULES:
- Select segments that together fill the target duration
- Prefer high style_similarity and aesthetic_score
- Avoid is_blurry=true segments unless nothing better exists
- Mix shot types and settings for visual variety
- Assign transition_in for each segment: "cut", "flash", or "crossfade"
  - "flash" for high-energy consecutive cuts
  - "crossfade" for mood changes
  - "cut" for everything else
  - null for the first segment

OUTPUT FORMAT — return ONLY a raw JSON array, no explanation, no markdown:
[
  {"video_path": "D:\\path\\to\\video.mp4", "start": 0.0, "end": 1.5, "transition_in": null},
  {"video_path": "D:\\path\\to\\video2.mp4", "start": 2.1, "end": 3.4, "transition_in": "cut"}
]"""


def summarize_segment(seg: Dict[str, Any]) -> Dict[str, Any]:
    """Compact segment summary for the LLM prompt — only what it needs."""
    return {
        "video_path":      seg.get("video_path"),
        "start":           round(float(seg.get("start", 0)), 2),
        "end":             round(float(seg.get("end", 0)), 2),
        "style_score":     round(float(seg.get("style_score", 0)), 3),
        "style_similarity":round(float(seg.get("style_similarity", 0)), 3),
        "aesthetic_score": round(float(seg.get("aesthetic_score", 0.5)), 3),
        "tags":            seg.get("tags", []),
        "is_blurry":       seg.get("is_blurry", False),
    }


def _parse_llm_response(content: str) -> List[Dict[str, Any]]:
    """
    Extract a JSON array from LLM response content.
    Handles:
      - Clean JSON
      - JSON wrapped in ```json ... ``` fences
      - JSON with leading/trailing explanation text
    """
    if not content or not content.strip():
        raise ValueError("Empty response content")

    # Strip markdown code fences
    content = re.sub(r"```(?:json)?", "", content).strip()
    content = content.replace("```", "").strip()

    # Try direct parse first
    try:
        parsed = json.loads(content)
        if isinstance(parsed, list):
            return parsed
        if isinstance(parsed, dict) and "segments" in parsed:
            return parsed["segments"]
    except json.JSONDecodeError:
        pass

    # Find the first [...] array in the text
    match = re.search(r"\[.*\]", content, re.DOTALL)
    if match:
        try:
            parsed = json.loads(match.group())
            if isinstance(parsed, list):
                return parsed
        except json.JSONDecodeError:
            pass

    raise ValueError(f"Could not extract JSON array from response: {content[:200]}")


def _validate_segment(seg: Any, original_paths: set) -> bool:
    """Check a returned segment has required fields and a valid video_path."""
    if not isinstance(seg, dict):
        return False
    if not seg.get("video_path"):
        return False
    if seg["video_path"] not in original_paths:
        return False
    if "start" not in seg or "end" not in seg:
        return False
    if float(seg["end"]) <= float(seg["start"]):
        return False
    return True


def ask_llm_for_edit_plan(
    segments:      List[Dict[str, Any]],
    style_profile: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """
    Ask Qwen2.5-7B-Instruct to produce an edit plan for the given segments.
    Falls back to the original segment list on any error.
    """
    if not segments:
        return segments

    # Send only top N by style_score — keeps prompt manageable
    sorted_segs   = sorted(segments, key=lambda s: s.get("style_score", 0), reverse=True)
    top_segs      = sorted_segs[:MAX_SEGMENTS_TO_LLM]
    original_paths = {s.get("video_path") for s in segments}

    target_dur   = float(style_profile.get("duration_preferences", {}).get("target_total", 30.0))
    summarized   = [summarize_segment(s) for s in top_segs]

    user_content = json.dumps({
        "target_duration_seconds": target_dur,
        "segments": summarized,
    }, indent=2)

    payload = {
        "model":       _discover_model_id(),
        "messages": [
            {"role": "system",  "content": SYSTEM_PROMPT},
            {"role": "user",    "content": user_content},
        ],
        "temperature": 0.3,
        "max_tokens":  2048,
    }

    try:
        response = requests.post(LMSTUDIO_URL, json=payload, timeout=120)
        response.raise_for_status()
        data = response.json()

        # Robust extraction — handle both standard OpenAI format and variations
        content = None
        if "choices" in data and data["choices"]:
            choice  = data["choices"][0]
            message = choice.get("message") or choice.get("text", "")
            if isinstance(message, dict):
                content = message.get("content", "")
            else:
                content = str(message)
        elif "content" in data:
            content = data["content"]
        elif "message" in data:
            content = data["message"]

        if not content:
            raise ValueError(f"No content in LLM response. Keys: {list(data.keys())}")

        plan = _parse_llm_response(content)

        # Validate returned segments
        valid_plan = [s for s in plan if _validate_segment(s, original_paths)]

        if not valid_plan:
            raise ValueError(f"LLM returned {len(plan)} segments but none passed validation")

        # Merge LLM-assigned transition hints back onto original segment dicts
        # (which have all the scoring fields the rest of the pipeline expects).
        #
        # BUG FIX: keying by video_path alone loses data when multiple segments
        # come from the same source file (e.g. two scene cuts from file.mp4).
        # Key by (video_path, rounded_start) for an exact match; fall back to
        # any segment from that file only as a last resort.
        path_start_to_orig = {
            (s["video_path"], round(float(s.get("start", 0)), 2)): s
            for s in segments
        }
        path_to_any = {s["video_path"]: s for s in segments}  # fallback only
        merged = []
        for llm_seg in valid_plan:
            key  = (llm_seg["video_path"], round(float(llm_seg.get("start", 0)), 2))
            orig = (path_start_to_orig.get(key)
                    or path_to_any.get(llm_seg["video_path"], {})).copy()
            orig["start"]         = float(llm_seg.get("start", orig.get("start", 0)))
            orig["end"]           = float(llm_seg.get("end",   orig.get("end",   1)))
            orig["transition_in"] = llm_seg.get("transition_in", "cut")
            merged.append(orig)

        # Save debug output
        try:
            with open("llm_plan_debug.json", "w", encoding="utf-8") as f:
                json.dump({"input_segments": len(top_segs),
                           "output_segments": len(merged),
                           "plan": valid_plan}, f, indent=2)
        except Exception:
            pass

        print(f"[llm_planner] Plan accepted — {len(merged)} segments")
        return merged

    except requests.exceptions.ConnectionError:
        print("[llm_planner] LM Studio not reachable at localhost:1234 — using heuristic plan")
        return None  # BUG FIX: returning `segments` caused editing_brain to log "LLM plan accepted"
    except requests.exceptions.Timeout:
        print("[llm_planner] LM Studio timed out — using heuristic plan")
        return None
    except Exception as e:
        print(f"[llm_planner] Failed: {e} — using heuristic plan")
        return None