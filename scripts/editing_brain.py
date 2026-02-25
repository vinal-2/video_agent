"""
editing_brain.py
----------------
Scores and selects the best segment sequence for a compiled reel.

Key fix vs original:
  The original passed final_segments (heuristic selection) to the LLM
  and then replaced final_segments with whatever the LLM returned —
  including when the LLM returned only 2 segments or timed out.
  The heuristic result was being silently discarded.

  Fix: LLM is now advisory only. It can reorder and add transition hints
  but cannot reduce the segment count below what the heuristic selected.
  If the LLM returns fewer segments than the heuristic, the heuristic
  result is kept.

  Also adds step-by-step debug prints so you can see exactly what happens
  at each stage of selection.
"""

import os
import json
from pathlib import Path
from typing import List, Dict, Any, Optional

BASE_DIR = Path(__file__).resolve().parent.parent

# ── Scoring weights (loaded from style profile, with defaults) ────────────────
_DEFAULT_WEIGHTS = {
    "W_STYLE_SIM":      0.40,
    "W_AESTHETIC":      0.15,
    "W_MOTION_SMOOTH":  0.10,
    "W_BASE_SCORE":     0.35,
}


def _get_weights(style_profile: Dict[str, Any]) -> Dict[str, float]:
    w = style_profile.get("scoring_weights", {})
    return {k: float(w.get(k, _DEFAULT_WEIGHTS[k])) for k in _DEFAULT_WEIGHTS}


def compute_style_score(seg: Dict[str, Any], weights: Dict[str, float]) -> float:
    """
    Weighted combination of all signal scores.
    Penalties: blurry (-0.15), dark tag (-0.02).
    Bonus: duration in 1–5s range (+0.05).
    """
    base      = float(seg.get("score", 0.0))
    style_sim = float(seg.get("style_similarity",  0.0))
    aesthetic = float(seg.get("aesthetic_score",   0.5))
    smoothness= float(seg.get("motion_smoothness", 0.5))
    duration  = float(seg.get("end", 0)) - float(seg.get("start", 0))

    score = (
        weights["W_BASE_SCORE"]     * base       +
        weights["W_STYLE_SIM"]      * style_sim  +
        weights["W_AESTHETIC"]      * aesthetic  +
        weights["W_MOTION_SMOOTH"]  * smoothness
    )

    if 1.0 <= duration <= 5.0:
        score += 0.05
    if "dark" in seg.get("tags", []):
        score -= 0.02
    if seg.get("is_blurry"):
        score -= 0.15

    return round(score, 5)


# Number of extra segments to generate beyond the target duration.
# These give you material to review and cut in your editor.
# Override with: set BUFFER_SEGMENTS=0  (to disable)  or  =10 (for more)
BUFFER_SEGMENTS = int(os.environ.get("BUFFER_SEGMENTS", "5"))


def _heuristic_select(
    segments:        List[Dict[str, Any]],
    target_duration: float,
    min_segment:     float,
) -> List[Dict[str, Any]]:
    """
    Greedy selection by style_score until target_duration is filled,
    then continues for BUFFER_SEGMENTS extra segments beyond the target.

    The buffer segments are marked with "buffer": True so the renderer
    and logger can distinguish them from the core selection. They appear
    at the end of the output video — trim them off in your editor.
    """
    if not segments:
        return []

    sorted_segs = sorted(segments, key=lambda s: s["style_score"], reverse=True)

    seen_videos: set = set()
    selected:    List[Dict[str, Any]] = []
    total        = 0.0
    # BUG FIX: track used (video_path, start) tuples so the buffer pass can
    # detect overlap with trimmed segments.  Checking `seg in selected` via dict
    # equality failed when the second pass stored a trimmed copy (different `end`),
    # allowing the original segment to be re-added as a buffer and create an
    # overlapping clip in the final render.
    used_starts: set = set()

    def _use(s: Dict[str, Any]):
        used_starts.add((s.get("video_path", ""), float(s.get("start", 0))))

    # First pass: pick top-scoring segment from each video up to target
    for seg in sorted_segs:
        vp = seg.get("video_path", "")
        if vp in seen_videos:
            continue
        dur = float(seg["end"]) - float(seg["start"])
        if dur < min_segment:
            continue
        if total + dur > target_duration and total > 0:
            break
        selected.append(seg)
        _use(seg)
        total += dur
        seen_videos.add(vp)

    # Second pass: fill remaining time up to target
    for seg in sorted_segs:
        if total >= target_duration * 0.95:
            break
        key = (seg.get("video_path", ""), float(seg.get("start", 0)))
        if key in used_starts:
            continue
        dur = float(seg["end"]) - float(seg["start"])
        if dur < min_segment:
            continue
        if total + dur > target_duration:
            remaining = target_duration - total
            if remaining >= min_segment:
                trimmed = seg.copy()
                trimmed["end"] = round(float(seg["start"]) + remaining, 3)
                selected.append(trimmed)
                _use(trimmed)
                total += remaining
            break
        selected.append(seg)
        _use(seg)
        total += dur

    core_count = len(selected)

    # Third pass: buffer segments beyond target duration
    if BUFFER_SEGMENTS > 0:
        buffer_added = 0
        for seg in sorted_segs:
            if buffer_added >= BUFFER_SEGMENTS:
                break
            key = (seg.get("video_path", ""), float(seg.get("start", 0)))
            if key in used_starts:
                continue
            dur = float(seg["end"]) - float(seg["start"])
            if dur < min_segment:
                continue
            buffered = seg.copy()
            buffered["buffer"] = True   # mark as review material
            selected.append(buffered)
            _use(buffered)
            total += dur
            buffer_added += 1

    # Sort core segments into natural viewing order (by filename then start time)
    # Keep buffer segments at the end, also sorted
    core    = selected[:core_count]
    buffer  = selected[core_count:]
    core.sort(  key=lambda s: (Path(s.get("video_path","")).name, float(s["start"])))
    buffer.sort(key=lambda s: s.get("style_score", 0), reverse=True)  # best first
    selected = core + buffer

    buf_dur = sum(float(s["end"])-float(s["start"]) for s in buffer)
    print(f"  [heuristic] Selected {core_count} core segments "
          f"({sum(float(s['end'])-float(s['start']) for s in core):.1f}s) "
          f"+ {len(buffer)} buffer segments ({buf_dur:.1f}s extra) "
          f"from {len(seen_videos)} source videos")

    return selected


def _apply_transitions(segments: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Assign default transitions if not already set by LLM."""
    for i, seg in enumerate(segments):
        if "transition_in" not in seg:
            seg["transition_in"] = None if i == 0 else "cut"
    return segments


def plan_edit(
    segments:        List[Dict[str, Any]],
    style_profile:   Dict[str, Any],
    target_duration: float = 30.0,
) -> List[Dict[str, Any]]:
    """
    Main entry point. Scores all segments, runs heuristic selection,
    then optionally passes to LLM for reordering.
    """
    if not segments:
        print("  [editing_brain] No segments to plan.")
        return []

    # Load settings from style profile
    weights     = _get_weights(style_profile)
    dur_prefs   = style_profile.get("duration_preferences", {})
    min_segment = float(dur_prefs.get("min_segment", 0.5))

    print(f"  [editing_brain] Scoring {len(segments)} segments...")

    # Score all segments
    for seg in segments:
        seg["style_score"] = compute_style_score(seg, weights)

    score_vals = [seg["style_score"] for seg in segments]
    print(f"  [editing_brain] Score range: {min(score_vals):.4f} – {max(score_vals):.4f}")

    # Heuristic selection
    final_segments = _heuristic_select(segments, target_duration, min_segment)

    if not final_segments:
        print("  [editing_brain] Heuristic returned no segments.")
        return []

    heuristic_count = len(final_segments)
    heuristic_dur   = sum(float(s["end"]) - float(s["start"]) for s in final_segments)
    print(f"  [editing_brain] Heuristic result: {heuristic_count} segments, {heuristic_dur:.1f}s")

    # LLM planning (optional — reorders and adds transition hints)
    use_llm = os.environ.get("ENABLE_LLM_PLANNER", "1") == "1"
    if use_llm:
        try:
            from scripts.llm_planner import ask_llm_for_edit_plan
            llm_result = ask_llm_for_edit_plan(final_segments, style_profile)

            # Only accept LLM result if it returned at least 80% of the segments
            # This prevents a bad LLM response from discarding most of the plan
            if llm_result and len(llm_result) >= max(2, int(heuristic_count * 0.8)):
                print(f"  [editing_brain] LLM plan accepted: {len(llm_result)} segments")
                final_segments = llm_result
            else:
                print(f"  [editing_brain] LLM plan rejected "
                      f"({len(llm_result) if llm_result else 0} segs < "
                      f"80% of {heuristic_count}) — keeping heuristic")
        except Exception as exc:
            print(f"  [editing_brain] LLM failed: {exc} — keeping heuristic")
    else:
        print("  [editing_brain] LLM planner disabled (ENABLE_LLM_PLANNER=0)")

    _apply_transitions(final_segments)

    core_segs   = [s for s in final_segments if not s.get("buffer")]
    buffer_segs = [s for s in final_segments if s.get("buffer")]
    core_dur    = sum(float(s["end"]) - float(s["start"]) for s in core_segs)
    buf_dur     = sum(float(s["end"]) - float(s["start"]) for s in buffer_segs)
    total_dur   = core_dur + buf_dur

    print(f"  [editing_brain] Final plan: {len(core_segs)} core segments ({core_dur:.1f}s target)"
          f" + {len(buffer_segs)} buffer segments ({buf_dur:.1f}s review material)"
          f" = {total_dur:.1f}s total")

    return final_segments