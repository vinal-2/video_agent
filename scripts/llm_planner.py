"""
llm_planner.py
--------------
Sends candidate segments to the LM Studio text model for artistic edit
planning. Returns a reordered + annotated segment list.

IMPROVEMENTS IN THIS VERSION:
  1. Richer system prompt — explicitly instructs the model to think about
     narrative arc (opener / middle / closer), lighting continuity, shot
     variety, and pacing rhythm.
  2. Transition vocabulary aligned with the expanded transitions.py catalog
     (includes wipe_up, fade_black, dissolve, dip_white, zoom_in, zoom_out,
     flash_white, push_up).
  3. Segment summary includes narrative_position hint from editing_brain
     so the LLM knows what the heuristic already decided.
  4. Fallback, validation and model-discovery logic unchanged from v1.
"""

import json
import os
import re
import requests
from typing import List, Dict, Any

LMSTUDIO_BASE = "http://localhost:1234"
LMSTUDIO_URL  = f"{LMSTUDIO_BASE}/v1/chat/completions"

MAX_SEGMENTS_TO_LLM = 20

_NON_TEXT_PATTERNS = ("moondream", "embed", "nomic", "mmproj", "whisper", "clip")


def _is_text_model(model_id: str) -> bool:
    lower = model_id.lower()
    return not any(p in lower for p in _NON_TEXT_PATTERNS)


def _discover_model_id(timeout: int = 5) -> str:
    override = os.environ.get("LLM_PLANNER_MODEL", "").strip()
    if override:
        return override
    try:
        resp   = requests.get(f"{LMSTUDIO_BASE}/v1/models", timeout=timeout)
        resp.raise_for_status()
        data   = resp.json()
        models = [m["id"] for m in data.get("data", []) if _is_text_model(m.get("id", ""))]
        if models:
            return models[0]
        all_models = [m["id"] for m in data.get("data", [])]
        if all_models:
            return all_models[0]
    except Exception:
        pass
    return "lmstudio-model"


SYSTEM_PROMPT = """You are a professional social media video editor specialising in Instagram Reels and TikTok content.

Your job is to sequence video segments into a compelling 30-second reel that feels like a mini story — not just a list of good shots.

ARTISTIC PRINCIPLES TO FOLLOW:
1. NARRATIVE ARC — The reel must have three acts:
   - OPENER (1 clip): Wide or establishing shot. Calm, well-lit, beautiful. Sets the scene. Draws the viewer in within the first second.
   - MIDDLE (most clips): The heart of the story. Alternate energy: a dynamic shot followed by a more composed one. Include faces or subjects for connection. Vary shot types (close-up, wide, medium).
   - CLOSER (1 clip): The emotional payoff. Warm, minimal, visually satisfying. Leaves the viewer with a feeling.

2. LIGHTING CONTINUITY — Avoid sudden jumps from dark to bright clips without a transition. If you must cross a lighting boundary, use fade_black to soften it.

3. SHOT VARIETY — Never place two clips from the same source video consecutively. Mix indoor/outdoor, close-up/wide, calm/energetic.

4. TRANSITION INTELLIGENCE — Match the transition to the content delta:
   - cut         : same energy, similar lighting — invisible join
   - wipe_up     : vertical reel motion, natural feel for 9:16
   - dissolve    : mood shift, softer boundary
   - fade_black  : lighting jump, contemplative pause, scene change
   - flash_white : beat drop, high-energy moment, peak action
   - dip_white   : clean, minimal, product/food aesthetic
   - zoom_in     : building anticipation before a reveal
   - zoom_out    : pulling back for a wider conclusion
   - push_up     : kinetic, upward motion — suits travel/concert
   - null        : first segment only

5. PACING RHYTHM — Vary clip durations. Opener 2–3s. Middle alternates short (1–2s) and medium (2–3s). Closer 2–4s. Never three identical durations in a row.

CRITICAL RULES:
- Only use video_path values that appear EXACTLY in the input. Copy them character-for-character.
- Prefer high style_similarity and aesthetic_score. Avoid is_blurry=true unless nothing else exists.
- Do not exceed the target duration.

OUTPUT FORMAT — return ONLY a raw JSON array, no explanation, no markdown:
[
  {"video_path": "exact\\path\\to\\file.mp4", "start": 0.0, "end": 2.5, "transition_in": null, "narrative_position": "opener"},
  {"video_path": "exact\\path\\to\\file2.mp4", "start": 1.2, "end": 3.1, "transition_in": "wipe_up", "narrative_position": "middle"},
  {"video_path": "exact\\path\\to\\file3.mp4", "start": 5.0, "end": 8.2, "transition_in": "dissolve", "narrative_position": "closer"}
]"""


def summarize_segment(seg: Dict[str, Any]) -> Dict[str, Any]:
    """Compact segment summary for the LLM prompt."""
    return {
        "video_path":          seg.get("video_path"),
        "start":               round(float(seg.get("start", 0)), 2),
        "end":                 round(float(seg.get("end", 0)), 2),
        "duration_s":          round(float(seg.get("end", 0)) - float(seg.get("start", 0)), 2),
        "style_score":         round(float(seg.get("style_score", 0)), 3),
        "style_similarity":    round(float(seg.get("style_similarity", 0)), 3),
        "aesthetic_score":     round(float(seg.get("aesthetic_score", 0.5)), 3),
        "tags":                seg.get("tags", []),
        "is_blurry":           seg.get("is_blurry", False),
        "narrative_position":  seg.get("narrative_position", "middle"),
    }


def _parse_llm_response(content: str) -> List[Dict[str, Any]]:
    if not content or not content.strip():
        raise ValueError("Empty response content")

    content = re.sub(r"```(?:json)?", "", content).strip()
    content = content.replace("```", "").strip()

    try:
        parsed = json.loads(content)
        if isinstance(parsed, list):
            return parsed
        if isinstance(parsed, dict) and "segments" in parsed:
            return parsed["segments"]
    except json.JSONDecodeError:
        pass

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
    if not segments:
        return segments

    # Send only core segments (no buffer) to keep prompt clean
    core_segs     = [s for s in segments if not s.get("buffer")]
    sorted_segs   = sorted(core_segs, key=lambda s: s.get("style_score", 0), reverse=True)
    top_segs      = sorted_segs[:MAX_SEGMENTS_TO_LLM]
    original_paths = {s.get("video_path") for s in segments}

    target_dur  = float(style_profile.get("duration_preferences", {}).get("target_total", 30.0))
    template    = os.environ.get("STYLE_TEMPLATE", "travel_reel")
    summarized  = [summarize_segment(s) for s in top_segs]

    user_content = json.dumps({
        "template":                template,
        "target_duration_seconds": target_dur,
        "segments": summarized,
    }, indent=2)

    payload = {
        "model":    _discover_model_id(),
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": user_content},
        ],
        "temperature": 0.3,
        "max_tokens":  2048,
    }

    try:
        response = requests.post(LMSTUDIO_URL, json=payload, timeout=120)
        response.raise_for_status()
        data = response.json()

        content = None
        if "choices" in data and data["choices"]:
            choice  = data["choices"][0]
            message = choice.get("message") or choice.get("text", "")
            content = message.get("content", "") if isinstance(message, dict) else str(message)
        elif "content" in data:
            content = data["content"]
        elif "message" in data:
            content = data["message"]

        if not content:
            raise ValueError(f"No content in LLM response. Keys: {list(data.keys())}")

        plan       = _parse_llm_response(content)
        valid_plan = [s for s in plan if _validate_segment(s, original_paths)]

        if not valid_plan:
            raise ValueError(f"LLM returned {len(plan)} segments but none passed validation")

        # Merge LLM transition hints + narrative positions back onto original dicts
        path_start_to_orig = {
            (s["video_path"], round(float(s.get("start", 0)), 2)): s
            for s in segments
        }
        path_to_any = {s["video_path"]: s for s in segments}

        merged = []
        for llm_seg in valid_plan:
            key  = (llm_seg["video_path"], round(float(llm_seg.get("start", 0)), 2))
            orig = (path_start_to_orig.get(key)
                    or path_to_any.get(llm_seg["video_path"], {})).copy()
            orig["start"]              = float(llm_seg.get("start", orig.get("start", 0)))
            orig["end"]                = float(llm_seg.get("end",   orig.get("end",   1)))
            orig["transition_in"]      = llm_seg.get("transition_in", "cut")
            orig["narrative_position"] = llm_seg.get("narrative_position", "middle")
            merged.append(orig)

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
        return None
    except requests.exceptions.Timeout:
        print("[llm_planner] LM Studio timed out — using heuristic plan")
        return None
    except Exception as e:
        print(f"[llm_planner] Failed: {e} — using heuristic plan")
        return None
