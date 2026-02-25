"""
pipeline_logger.py
------------------
Centralised structured logging for the video-agent pipeline.

Writes two files per run into logs/:
  segment_scores_TEMPLATE_TIMESTAMP.csv   — every segment with all scores,
                                            tags, and whether it was selected
  run_summary_TEMPLATE_TIMESTAMP.txt      — human-readable run summary

Also prints a compact score table to the console after enrichment so you
can see immediately why segments were selected or rejected without opening
a file.

Usage (already called from analyze_and_edit.py and editing_brain.py):
    from scripts.pipeline_logger import PipelineLogger
    logger = PipelineLogger(template_name, logs_dir)
    logger.log_segments_after_enrichment(all_segments)
    logger.log_selected_segments(selected_segments, all_segments)
    logger.write_files()
"""

import csv
import json
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional


class PipelineLogger:

    def __init__(self, template_name: str, logs_dir: Path):
        self.template_name = template_name
        self.logs_dir      = Path(logs_dir)
        self.logs_dir.mkdir(exist_ok=True)
        self.timestamp     = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.all_segments: List[Dict[str, Any]]      = []
        self.selected_set: set                        = set()  # (video_path, start, end)
        self.run_meta:     Dict[str, Any]             = {}

    # ── Called from analyze_and_edit.py ──────────────────────────────────────

    def log_segments_after_enrichment(self, segments: List[Dict[str, Any]]):
        """Store all enriched segments and print a score table to console."""
        self.all_segments = segments

        if not segments:
            return

        # Console table
        print("\n── Segment scores after enrichment ──")
        print(f"  {'File':<28} {'Start':>6} {'End':>6} {'StyleSim':>9} "
              f"{'Aesthetic':>10} {'Blur':>7} {'Tags'}")
        print("  " + "─" * 90)

        for seg in sorted(segments, key=lambda s: s.get("style_similarity", 0), reverse=True):
            fname    = Path(seg.get("video_path", "?")).name[:27]
            start    = float(seg.get("start", 0))
            end      = float(seg.get("end", 0))
            sim      = seg.get("style_similarity", 0.0)
            aesth    = seg.get("aesthetic_score",  0.5)
            blur     = seg.get("blur_score",       0.0)
            tags     = ", ".join(seg.get("tags", []))
            blurry   = "⚠ BLUR" if seg.get("is_blurry") else ""
            print(f"  {fname:<28} {start:>6.2f} {end:>6.2f} {sim:>9.4f} "
                  f"{aesth:>10.4f} {blur:>7.0f}  {tags} {blurry}")

        print()

    def log_selected_segments(
        self,
        selected:     List[Dict[str, Any]],
        all_segments: Optional[List[Dict[str, Any]]] = None,
    ):
        """Record which segments were selected."""
        if all_segments:
            self.all_segments = all_segments

        self.selected_set = {
            (seg.get("video_path"), round(float(seg.get("start", 0)), 3))
            for seg in selected
        }

        total_dur = sum(s["end"] - s["start"] for s in selected)

        print("── Selected segments ──")
        for i, seg in enumerate(selected):
            fname = Path(seg.get("video_path", "?")).name
            dur   = seg["end"] - seg["start"]
            sim   = seg.get("style_similarity", 0.0)
            tags  = ", ".join(seg.get("tags", []))
            trans = seg.get("transition_in", "cut") or "cut"
            print(f"  {i+1:>2}. {fname:<30} {seg['start']:.2f}–{seg['end']:.2f}s "
                  f"({dur:.2f}s)  sim={sim:.4f}  [{trans}]  {tags}")
        print(f"  Total: {len(selected)} segments, {total_dur:.1f}s\n")

    def log_run_meta(self, meta: Dict[str, Any]):
        self.run_meta = meta

    # ── File output ───────────────────────────────────────────────────────────

    def write_files(self):
        self._write_csv()
        self._write_summary()

    def _write_csv(self):
        if not self.all_segments:
            return

        path = self.logs_dir / f"segment_scores_{self.template_name}_{self.timestamp}.csv"

        fieldnames = [
            "selected", "video_file", "start", "end", "duration",
            "style_score", "style_similarity", "aesthetic_score",
            "blur_score", "is_blurry", "motion_smoothness",
            "base_score", "tags", "transition_in",
        ]

        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()

            for seg in sorted(
                self.all_segments,
                key=lambda s: s.get("style_score", s.get("style_similarity", 0)),
                reverse=True,
            ):
                key      = (seg.get("video_path"), round(float(seg.get("start", 0)), 3))
                selected = key in self.selected_set
                dur      = float(seg.get("end", 0)) - float(seg.get("start", 0))

                writer.writerow({
                    "selected":         "YES" if selected else "",
                    "video_file":       Path(seg.get("video_path", "")).name,
                    "start":            round(float(seg.get("start", 0)), 3),
                    "end":              round(float(seg.get("end", 0)), 3),
                    "duration":         round(dur, 3),
                    "style_score":      round(float(seg.get("style_score", 0)), 5),
                    "style_similarity": round(float(seg.get("style_similarity", 0)), 5),
                    "aesthetic_score":  round(float(seg.get("aesthetic_score", 0.5)), 5),
                    "blur_score":       round(float(seg.get("blur_score", 0)), 1),
                    "is_blurry":        "YES" if seg.get("is_blurry") else "",
                    "motion_smoothness":round(float(seg.get("motion_smoothness", 0.5)), 4),
                    "base_score":       round(float(seg.get("score", 0)), 5),
                    "tags":             "|".join(seg.get("tags", [])),
                    "transition_in":    seg.get("transition_in", ""),
                })

        print(f"[log] Segment scores → {path}")

    def _write_summary(self):
        path = self.logs_dir / f"run_summary_{self.template_name}_{self.timestamp}.txt"

        selected = [
            s for s in self.all_segments
            if (s.get("video_path"), round(float(s.get("start", 0)), 3)) in self.selected_set
        ]
        rejected = [s for s in self.all_segments if s not in selected]

        def avg(lst, key):
            vals = [float(s.get(key, 0)) for s in lst if s.get(key) is not None]
            return sum(vals) / len(vals) if vals else 0.0

        lines = [
            f"Run Summary — {self.template_name}",
            f"Timestamp:   {self.timestamp}",
            f"",
            f"Segments total:    {len(self.all_segments)}",
            f"Segments selected: {len(selected)}",
            f"Segments rejected: {len(rejected)}",
            f"",
            f"── Selected ──",
            f"  Avg style_similarity:  {avg(selected, 'style_similarity'):.4f}",
            f"  Avg aesthetic_score:   {avg(selected, 'aesthetic_score'):.4f}",
            f"  Avg blur_score:        {avg(selected, 'blur_score'):.1f}",
            f"  Total duration:        {sum(s['end']-s['start'] for s in selected):.1f}s",
            f"",
            f"── Rejected ──",
            f"  Avg style_similarity:  {avg(rejected, 'style_similarity'):.4f}",
            f"  Avg aesthetic_score:   {avg(rejected, 'aesthetic_score'):.4f}",
            f"  Avg blur_score:        {avg(rejected, 'blur_score'):.1f}",
            f"",
        ]

        # Rejection reasons
        blur_rejected  = [s for s in rejected if s.get("is_blurry")]
        low_sim        = [s for s in rejected if float(s.get("style_similarity", 0)) < 0.15]
        low_aesth      = [s for s in rejected if float(s.get("aesthetic_score", 0.5)) < 0.3]

        lines += [
            f"── Rejection signals (may overlap) ──",
            f"  Failed blur check:      {len(blur_rejected)}",
            f"  Low style similarity:   {len(low_sim)}  (< 0.15)",
            f"  Low aesthetic score:    {len(low_aesth)}  (< 0.3)",
            f"",
        ]

        # Tag frequency in selected vs rejected
        def tag_freq(segs):
            counts: Dict[str, int] = {}
            for s in segs:
                for t in s.get("tags", []):
                    counts[t] = counts.get(t, 0) + 1
            total = max(len(segs), 1)
            return {k: round(v/total, 2) for k, v in sorted(counts.items(), key=lambda x: -x[1])}

        lines += [
            f"── Tag frequency ──",
            f"  Selected: {tag_freq(selected)}",
            f"  Rejected: {tag_freq(rejected)}",
            f"",
        ]

        if self.run_meta:
            lines += [
                f"── Run meta ──",
                *[f"  {k}: {v}" for k, v in self.run_meta.items()],
            ]

        path.write_text("\n".join(lines), encoding="utf-8")
        print(f"[log] Run summary   → {path}")