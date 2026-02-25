import json
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import List, Dict, Any, Optional

import numpy as np
from scripts.llm_planner import ask_llm_for_edit_plan

BASE_DIR = Path(__file__).resolve().parent.parent
STYLE_DIR = BASE_DIR / "style"
STYLE_PROFILE_PATH = STYLE_DIR / "style_profile.json"
SEMANTIC_WEIGHTS_PATH = STYLE_DIR / "semantic_weights.json"


@dataclass
class EditDecision:
    segment: Dict[str, Any]
    role: str
    style_score: float
    keep: bool


@dataclass(frozen=True)
class StylePreferences:
    median_cut: float = 3.0
    p10: float = 1.5
    p90: float = 5.0
    hero_tags: Optional[List[str]] = None


@lru_cache(maxsize=1)
def load_style_preferences() -> StylePreferences:
    if not STYLE_PROFILE_PATH.exists():
        return StylePreferences()

    try:
        data = json.loads(STYLE_PROFILE_PATH.read_text())
    except json.JSONDecodeError:
        return StylePreferences()

    pacing = data.get("pacing", {})
    metadata = data.get("metadata", {})
    hero_tags = metadata.get("hero_tags")
    if isinstance(hero_tags, str):
        hero_tags = [hero_tags]

    return StylePreferences(
        median_cut=float(pacing.get("median_cut") or 3.0),
        p10=float(pacing.get("p10") or 1.5),
        p90=float(pacing.get("p90") or 5.0),
        hero_tags=hero_tags,
    )


def compute_style_score(seg: Dict[str, Any]) -> float:
    base = float(seg.get("score", 0.0))

    duration = seg["end"] - seg["start"]
    if 1.0 <= duration <= 5.0:
        base += 0.05

    style_sim = float(seg.get("style_similarity", 0.0))
    base += 0.4 * style_sim

    aesth = float(seg.get("aesthetic_score", 0.0))
    base += 0.3 * aesth

    tags = seg.get("tags", [])
    if "bright" in tags:
        base += 0.03
    if "dark" in tags:
        base -= 0.02

    if seg.get("is_blurry"):
        base -= 0.15

    return base


def assign_roles(sorted_segments: List[Dict[str, Any]]) -> List[EditDecision]:
    n = len(sorted_segments)
    decisions: List[EditDecision] = []

    for i, seg in enumerate(sorted_segments):
        if i == 0:
            role = "opener"
        elif i == 1 and n > 6:
            role = "opener"
        elif i >= n - 2:
            role = "closer"
        else:
            role = "middle"

        style_score = compute_style_score(seg)
        decisions.append(EditDecision(segment=seg, role=role, style_score=style_score, keep=True))

    return decisions


def plan_edit(
    segments: List[Dict[str, Any]],
    style_profile: Dict[str, Any],
    target_duration: float = 30.0
) -> List[Dict[str, Any]]:

    if not segments:
        return []

    for seg in segments:
        seg["style_score"] = compute_style_score(seg)

    sorted_segments = sorted(segments, key=lambda s: s["style_score"], reverse=True)
    decisions = assign_roles(sorted_segments)

    openers = [d for d in decisions if d.role == "opener" and d.keep]
    middles = [d for d in decisions if d.role == "middle" and d.keep]
    closers = [d for d in decisions if d.role == "closer" and d.keep]

    ordered = openers + middles + closers

    final_segments: List[Dict[str, Any]] = []
    total = 0.0

    for d in ordered:
        seg = d.segment
        dur = seg["end"] - seg["start"]
        if total + dur > target_duration and total > 0:
            break
        final_segments.append(seg)
        total += dur

    # LLM REASONING (optional)
    use_llm = os.environ.get("ENABLE_LLM_PLANNER", "1") == "1"
    if use_llm:
        try:
            final_segments = ask_llm_for_edit_plan(final_segments, style_profile)
        except Exception as exc:
            print(f"[warn] LLM planner failed, using heuristic plan: {exc}")

    # If you’re dropping transitions for now, you can remove this block entirely
    for i, seg in enumerate(final_segments):
        if "transition_in" not in seg:
            seg["transition_in"] = None if i == 0 else "cut"

    return final_segments
