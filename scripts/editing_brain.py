"""
editing_brain.py
----------------
Scores and selects the best segment sequence for a compiled reel.

IMPROVEMENTS IN THIS VERSION:
  1. Narrative arc scoring — segments are scored for their position suitability
     (opener/middle/closer) so the reel tells a story, not just a list of
     best clips.
  2. Shot variety enforcement — prevents consecutive clips from the same
     source file AND prevents back-to-back clips with the same tag signature
     (e.g. two "dark outdoor minimal" clips in a row).
  3. Temporal rhythm — segment durations are varied deliberately: openers
     are longer for establishment, the middle alternates short/long for
     energy, closers are punchy.
  4. Lighting continuity — hard jumps between very dark and very bright
     clips are penalised unless a transition is assigned.
  5. Transition intelligence — transition type is auto-assigned based on
     the DELTA between consecutive clips (brightness, energy, tag change)
     rather than always defaulting to "cut".
  6. LLM is still advisory-only and cannot reduce segment count below
     80% of heuristic result.
"""

import os
import json
from pathlib import Path
from typing import List, Dict, Any, Optional, Set

BASE_DIR = Path(__file__).resolve().parent.parent

# ── Scoring weights (loaded from style profile, with defaults) ────────────────
_DEFAULT_WEIGHTS = {
    "W_STYLE_SIM":      0.40,
    "W_AESTHETIC":      0.15,
    "W_MOTION_SMOOTH":  0.10,
    "W_BASE_SCORE":     0.35,
}

# ── Transition constants ──────────────────────────────────────────────────────
# Brightness bands used for lighting continuity checks
_DARK_TAGS   = {"dark"}
_BRIGHT_TAGS = {"bright"}

# Tags that signal high visual energy
_HIGH_ENERGY_TAGS = {"high_energy", "busy", "face", "person"}

# Tags that signal calm / establishing shots
_CALM_TAGS = {"minimal", "outdoor", "wide_shot", "landscape"}


def _get_weights(style_profile: Dict[str, Any]) -> Dict[str, float]:
    w = style_profile.get("scoring_weights", {})
    return {k: float(w.get(k, _DEFAULT_WEIGHTS[k])) for k in _DEFAULT_WEIGHTS}


# ── Per-segment base score ────────────────────────────────────────────────────

def compute_style_score(seg: Dict[str, Any], weights: Dict[str, float]) -> float:
    """
    Weighted combination of all signal scores.
    Penalties: blurry (-0.15), dark tag (-0.02).
    Bonus: duration in 1–5s range (+0.05).
    """
    base       = float(seg.get("score", 0.0))
    style_sim  = float(seg.get("style_similarity",  0.0))
    aesthetic  = float(seg.get("aesthetic_score",   0.5))
    smoothness = float(seg.get("motion_smoothness", 0.5))
    duration   = float(seg.get("end", 0)) - float(seg.get("start", 0))

    score = (
        weights["W_BASE_SCORE"]    * base       +
        weights["W_STYLE_SIM"]     * style_sim  +
        weights["W_AESTHETIC"]     * aesthetic  +
        weights["W_MOTION_SMOOTH"] * smoothness
    )

    if 1.0 <= duration <= 5.0:
        score += 0.05
    if "dark" in seg.get("tags", []):
        score -= 0.02
    if seg.get("is_blurry"):
        score -= 0.15

    return round(score, 5)


# ── Narrative position scoring ────────────────────────────────────────────────

def _position_score(seg: Dict[str, Any], position: str) -> float:
    """
    Score a segment's suitability for a given narrative position.

    opener — wide establishing shot, calm, bright, ideally outdoor
    middle — high energy, faces, busy, dynamic
    closer — warm, minimal, satisfying composition, slightly longer
    """
    tags     = set(seg.get("tags", []))
    aesthetic = float(seg.get("aesthetic_score", 0.5))
    duration  = float(seg.get("end", 0)) - float(seg.get("start", 0))

    if position == "opener":
        score = 0.0
        if tags & {"outdoor", "wide_shot", "landscape"}:  score += 0.20
        if tags & {"bright", "warm"}:                     score += 0.15
        if tags & {"minimal"}:                            score += 0.10
        if tags & {"dark", "busy", "high_energy"}:        score -= 0.15
        if duration >= 2.0:                               score += 0.10
        return score

    if position == "middle":
        score = 0.0
        if tags & _HIGH_ENERGY_TAGS:                      score += 0.20
        if tags & {"face", "person"}:                     score += 0.10
        if tags & {"minimal", "wide_shot"}:               score -= 0.05
        if 1.0 <= duration <= 3.0:                        score += 0.10
        return score

    if position == "closer":
        score = 0.0
        if tags & {"warm", "golden_hour"}:                score += 0.20
        if tags & {"minimal", "outdoor"}:                 score += 0.10
        if tags & {"bright"}:                             score += 0.05
        if aesthetic > 0.65:                              score += 0.15
        if tags & {"busy", "high_energy"}:                score -= 0.10
        if 1.5 <= duration <= 4.0:                        score += 0.10
        return score

    return 0.0


# ── Lighting continuity penalty ───────────────────────────────────────────────

def _lighting_jump_penalty(seg_a: Dict[str, Any], seg_b: Dict[str, Any]) -> float:
    """
    Return a penalty (negative float) when consecutive segments have a
    jarring brightness jump that no transition can fully hide.

    dark → bright or bright → dark = -0.10 base
    Worsened if neither has a face (no anchor for the viewer).
    """
    tags_a = set(seg_a.get("tags", []))
    tags_b = set(seg_b.get("tags", []))

    a_dark   = bool(tags_a & _DARK_TAGS)
    b_dark   = bool(tags_b & _DARK_TAGS)
    a_bright = bool(tags_a & _BRIGHT_TAGS)
    b_bright = bool(tags_b & _BRIGHT_TAGS)

    hard_jump = (a_dark and b_bright) or (a_bright and b_dark)
    if not hard_jump:
        return 0.0

    penalty = -0.10
    # Anchor with a face softens the jump (the eye follows the subject)
    if tags_a & {"face"} or tags_b & {"face"}:
        penalty += 0.05
    return penalty


# ── Tag fingerprint for variety enforcement ───────────────────────────────────

def _tag_fingerprint(seg: Dict[str, Any]) -> frozenset:
    """
    Reduced tag set for variety comparison.
    Only lighting + setting + energy — ignores minor tags.
    """
    tags = set(seg.get("tags", []))
    relevant = tags & {
        "dark", "bright", "outdoor", "indoor",
        "high_energy", "busy", "minimal",
        "warm", "cool", "face",
    }
    return frozenset(relevant)


# ── Smart transition assignment ───────────────────────────────────────────────

def _assign_transition(
    seg_prev: Optional[Dict[str, Any]],
    seg_curr: Dict[str, Any],
    position: str,
) -> str:
    """
    Choose a transition type based on the content delta between clips.

    Rules (in priority order):
      1. First clip → None
      2. Opener → first middle: dissolve (feels like story beginning)
      3. Hard lighting jump: fade_black (masks the harshness)
      4. Both high-energy: cut or flash_white (kinetic feel)
      5. Mood shift (energy → calm or vice versa): dissolve
      6. Same setting consecutive: wipe_up (breaks monotony vertically)
      7. Default: cut
    """
    if seg_prev is None or position == "opener":
        return "cut"

    tags_prev = set(seg_prev.get("tags", []))
    tags_curr = set(seg_curr.get("tags", []))

    prev_dark   = bool(tags_prev & _DARK_TAGS)
    curr_dark   = bool(tags_curr & _DARK_TAGS)
    prev_bright = bool(tags_prev & _BRIGHT_TAGS)
    curr_bright = bool(tags_curr & _BRIGHT_TAGS)

    hard_jump = (prev_dark and curr_bright) or (prev_bright and curr_dark)
    if hard_jump:
        return "fade_black"

    prev_energy = bool(tags_prev & _HIGH_ENERGY_TAGS)
    curr_energy = bool(tags_curr & _HIGH_ENERGY_TAGS)

    if prev_energy and curr_energy:
        return "cut"  # kinetic back-to-back

    mood_shift = (prev_energy and not curr_energy) or (not prev_energy and curr_energy)
    if mood_shift:
        return "dissolve"

    # Same dominant setting — break it up vertically
    prev_setting = tags_prev & {"outdoor", "indoor"}
    curr_setting = tags_curr & {"outdoor", "indoor"}
    if prev_setting and prev_setting == curr_setting:
        return "wipe_up"

    if position == "closer":
        return "dissolve"

    return "cut"


# ── Duration shaping ──────────────────────────────────────────────────────────

def _shape_duration(
    seg: Dict[str, Any],
    position: str,
    index: int,
    min_segment: float,
    max_segment: float,
) -> Dict[str, Any]:
    """
    Adjust trimEnd to fit the narrative position's ideal duration.
    Never shorter than min_segment or longer than the original clip.
    """
    start    = float(seg["start"])
    end      = float(seg["end"])
    orig_dur = end - start

    if position == "opener":
        target = min(max_segment, max(2.5, orig_dur))
    elif position == "closer":
        target = min(max_segment * 0.8, max(2.0, orig_dur * 0.85))
    else:
        # Middle: alternate shorter / longer for rhythm
        if index % 2 == 0:
            target = min(orig_dur, max(min_segment, orig_dur * 0.75))
        else:
            target = min(max_segment, orig_dur)

    target = max(min_segment, min(target, orig_dur))
    shaped = seg.copy()
    shaped["end"] = round(start + target, 3)
    return shaped


# ── Main heuristic selection ──────────────────────────────────────────────────

BUFFER_SEGMENTS = int(os.environ.get("BUFFER_SEGMENTS", "5"))


def _heuristic_select(
    segments:        List[Dict[str, Any]],
    target_duration: float,
    min_segment:     float,
    max_segment:     float,
) -> List[Dict[str, Any]]:
    """
    Narrative-aware greedy selection.

    Strategy:
      1. Pick the best opener (wide/calm/bright, from a unique video)
      2. Fill middle greedily by style_score, enforcing:
           - no consecutive same-source video
           - no consecutive identical tag fingerprint
           - lighting continuity penalty applied to ranking
      3. Pick the best closer (warm/minimal/aesthetic)
      4. Append BUFFER_SEGMENTS extra options for the review UI
    """
    if not segments:
        return []

    # Pre-sort by style_score descending — base ranking
    by_score = sorted(segments, key=lambda s: s["style_score"], reverse=True)

    used_starts: Set[tuple] = set()

    def _mark_used(s: Dict[str, Any]):
        used_starts.add((s.get("video_path", ""), float(s.get("start", 0))))

    def _is_used(s: Dict[str, Any]) -> bool:
        return (s.get("video_path", ""), float(s.get("start", 0))) in used_starts

    # ── 1. Pick opener ────────────────────────────────────────────────────────
    opener_candidates = [
        (s, s["style_score"] + _position_score(s, "opener"))
        for s in by_score
        if not _is_used(s)
        and float(s["end"]) - float(s["start"]) >= min_segment
        and not s.get("is_blurry")
    ]
    opener_candidates.sort(key=lambda x: x[1], reverse=True)
    opener = _shape_duration(opener_candidates[0][0], "opener", 0, min_segment, max_segment) if opener_candidates else None

    selected: List[Dict[str, Any]] = []
    total = 0.0
    if opener:
        selected.append(opener)
        _mark_used(opener)
        total += float(opener["end"]) - float(opener["start"])

    # ── 2. Fill middle ────────────────────────────────────────────────────────
    middle_idx = 0
    while total < target_duration * 0.80:
        last = selected[-1] if selected else None
        last_fp = _tag_fingerprint(last) if last else frozenset()
        last_vp = last.get("video_path", "") if last else ""

        best_candidate = None
        best_combined  = -999.0

        for seg in by_score:
            if _is_used(seg):
                continue
            dur = float(seg["end"]) - float(seg["start"])
            if dur < min_segment:
                continue
            if seg.get("is_blurry"):
                continue
            if seg.get("video_path", "") == last_vp:
                continue  # never same source back-to-back
            fp = _tag_fingerprint(seg)
            if fp == last_fp and len(last_fp) >= 2:
                continue  # avoid visually identical consecutive clips

            combined = (
                seg["style_score"]
                + _position_score(seg, "middle")
                + (_lighting_jump_penalty(last, seg) if last else 0.0)
            )
            if combined > best_combined:
                best_combined  = combined
                best_candidate = seg

        if best_candidate is None:
            # Relax the same-source constraint as last resort
            for seg in by_score:
                if _is_used(seg):
                    continue
                dur = float(seg["end"]) - float(seg["start"])
                if dur < min_segment or seg.get("is_blurry"):
                    continue
                best_candidate = seg
                break

        if best_candidate is None:
            break

        # Trim to fit remaining target if needed
        remaining = target_duration - total
        shaped    = _shape_duration(best_candidate, "middle", middle_idx, min_segment, max_segment)
        shaped_dur = float(shaped["end"]) - float(shaped["start"])
        if total + shaped_dur > target_duration and remaining >= min_segment:
            trimmed = shaped.copy()
            trimmed["end"] = round(float(shaped["start"]) + remaining, 3)
            shaped = trimmed

        selected.append(shaped)
        _mark_used(shaped)
        total += float(shaped["end"]) - float(shaped["start"])
        middle_idx += 1

    # ── 3. Pick closer ────────────────────────────────────────────────────────
    last_vp = selected[-1].get("video_path", "") if selected else ""
    closer_candidates = [
        (s, s["style_score"] + _position_score(s, "closer"))
        for s in by_score
        if not _is_used(s)
        and float(s["end"]) - float(s["start"]) >= min_segment
        and not s.get("is_blurry")
        and s.get("video_path", "") != last_vp
    ]
    closer_candidates.sort(key=lambda x: x[1], reverse=True)

    if closer_candidates:
        closer = _shape_duration(closer_candidates[0][0], "closer", len(selected), min_segment, max_segment)
        closer_dur = float(closer["end"]) - float(closer["start"])
        if total + closer_dur <= target_duration * 1.15:  # allow slight overshoot for a good closer
            selected.append(closer)
            _mark_used(closer)
            total += closer_dur

    core_count = len(selected)
    core_dur   = sum(float(s["end"]) - float(s["start"]) for s in selected)

    # ── 4. Buffer segments ────────────────────────────────────────────────────
    buffer_added = 0
    for seg in by_score:
        if buffer_added >= BUFFER_SEGMENTS:
            break
        if _is_used(seg):
            continue
        dur = float(seg["end"]) - float(seg["start"])
        if dur < min_segment:
            continue
        buf = seg.copy()
        buf["buffer"] = True
        selected.append(buf)
        _mark_used(buf)
        buffer_added += 1

    buf_segs = selected[core_count:]
    buf_dur  = sum(float(s["end"]) - float(s["start"]) for s in buf_segs)

    # ── 5. Assign transitions ─────────────────────────────────────────────────
    core_segs = selected[:core_count]
    positions = (
        ["opener"]
        + ["middle"] * max(0, core_count - 2)
        + (["closer"] if core_count > 1 else [])
    )
    for i, seg in enumerate(core_segs):
        pos  = positions[i] if i < len(positions) else "middle"
        prev = core_segs[i - 1] if i > 0 else None
        seg["transition_in"] = _assign_transition(prev, seg, pos)
        seg["narrative_position"] = pos

    for buf_seg in buf_segs:
        buf_seg["transition_in"] = "cut"

    print(f"  [heuristic] {core_count} core segments ({core_dur:.1f}s)"
          f" + {len(buf_segs)} buffer ({buf_dur:.1f}s)"
          f"  arc: {' → '.join(positions[:5])}{'...' if len(positions) > 5 else ''}")

    return selected


def _apply_transitions(segments: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Ensure every segment has a transition_in field."""
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
    Main entry point. Scores all segments, runs narrative-aware heuristic
    selection, then optionally passes to LLM for reordering.
    """
    if not segments:
        print("  [editing_brain] No segments to plan.")
        return []

    weights     = _get_weights(style_profile)
    dur_prefs   = style_profile.get("duration_preferences", {})
    min_segment = float(dur_prefs.get("min_segment", 0.5))
    max_segment = float(dur_prefs.get("max_segment", 4.0))

    print(f"  [editing_brain] Scoring {len(segments)} segments...")
    for seg in segments:
        seg["style_score"] = compute_style_score(seg, weights)

    score_vals = [seg["style_score"] for seg in segments]
    print(f"  [editing_brain] Score range: {min(score_vals):.4f} – {max(score_vals):.4f}")

    final_segments   = _heuristic_select(segments, target_duration, min_segment, max_segment)
    heuristic_count  = len(final_segments)
    heuristic_dur    = sum(float(s["end"]) - float(s["start"]) for s in final_segments)
    print(f"  [editing_brain] Heuristic: {heuristic_count} segments, {heuristic_dur:.1f}s")

    # ── LLM planning (advisory only) ─────────────────────────────────────────
    use_llm = os.environ.get("ENABLE_LLM_PLANNER", "1") == "1"
    if use_llm:
        try:
            from scripts.llm_planner import ask_llm_for_edit_plan
            llm_result = ask_llm_for_edit_plan(final_segments, style_profile)
            if llm_result and len(llm_result) >= max(2, int(heuristic_count * 0.8)):
                print(f"  [editing_brain] LLM plan accepted: {len(llm_result)} segments")
                final_segments = llm_result
            else:
                print(f"  [editing_brain] LLM plan rejected — keeping heuristic")
        except Exception as exc:
            print(f"  [editing_brain] LLM failed: {exc} — keeping heuristic")
    else:
        print("  [editing_brain] LLM planner disabled (ENABLE_LLM_PLANNER=0)")

    _apply_transitions(final_segments)

    core_segs   = [s for s in final_segments if not s.get("buffer")]
    buffer_segs = [s for s in final_segments if s.get("buffer")]
    core_dur    = sum(float(s["end"]) - float(s["start"]) for s in core_segs)
    buf_dur     = sum(float(s["end"]) - float(s["start"]) for s in buffer_segs)

    print(f"  [editing_brain] Final: {len(core_segs)} core ({core_dur:.1f}s)"
          f" + {len(buffer_segs)} buffer ({buf_dur:.1f}s)"
          f" = {core_dur + buf_dur:.1f}s total")

    return final_segments