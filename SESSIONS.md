# VideoAgent — Session Log

Claude Code reads the **most recent entry** at the start of every session.
Entries are added by Claude Code at the end of every session.
Never delete entries — they are a changelog and debugging history.

Format rules:
- One entry per session (not per day — if you work twice in a day, two entries)
- Date + rough time so you can correlate with git commits
- Be specific about file names and line numbers — vague entries are useless
- "Broken / known issues" section is mandatory — never leave it blank, write "None known" if clean
- "Next session starts with" is the single most important field — make it a concrete action, not a vague goal

---

## Entry format (copy this for every new entry)

```
### YYYY-MM-DD [morning/afternoon/evening]

**Phase:** [Which phase of FEATURES.md — or "maintenance" / "bugfix" / "UI"]
**Commits:** [git short hashes if committed, or "uncommitted"]

**Completed this session:**
- [specific thing done, file changed, what it does]
- [specific thing done, file changed, what it does]

**Tested and confirmed working:**
- [test run + result]
- [test run + result]

**Broken / known issues:**
- [description, file, line number if known]
- None known

**Files changed:**
- [filename] — [one-line description of change]

**Next session starts with:**
[Single concrete first action — a command to run, a file to open, a bug to fix]
```

---

## Session Log

---

### 2026-02-25 [evening]

**Phase:** Phase 1 + 2 + 3 implementation (all committed, none tested)
**Commits:** dc08832 (Phase 1), c17b42d (Phase 2), 689ad82 (Phase 3)

**Completed this session:**
- `scripts/smart_crop.py` — face/body cascade subject detection, returns crop X offset for 9:16 reframe
- `scripts/sam_helper.py` — SAM ViT-B point-prompt mask generation, auto-downloads checkpoint to `style/sam_vit_b.pth`
- `scripts/inpaint_worker.py` — ProPainter subprocess wrapper, writes progress to `output/inpaint_jobs/<job_id>.json`
- `analyze_and_edit.py` — `_render_segment_ffmpeg()` updated with crop filter (Phase 1) and SAM split-grade filter chain (Phase 2)
- `app.py` — added `POST /api/crop_auto`, `POST /api/sam_mask`, `POST /api/inpaint/start`, `GET /api/inpaint/status/<id>`, `POST /api/inpaint/cancel/<id>`
- `SegmentCard.tsx` — `CropTool` component (drag handle overlay) + `SamTool` component (click-to-segment, mask overlay)
- `InpaintTab.tsx` — new component: segment list, DrawRegionModal canvas, progress polling, Render with Inpainting button
- `MainContent.tsx` — 4th Inpaint tab added, disabled until `phase === "done"`
- `usePipeline.ts` — `cropData`, `samData`, `inpaintJobs` state + polling + `renderWithInpainting()`
- `src/lib/api.ts` — new interfaces and fetch functions for all new endpoints

**Tested and confirmed working:**
- Nothing tested yet — all three phases were implemented but testing was deferred

**Broken / known issues:**
- Phase 1: crop filter order in `_render_segment_ffmpeg()` not verified — crop must come BEFORE scale filter
- Phase 2: SAM split-grade ffmpeg filter_complex not verified — complex graph syntax, likely needs debugging
- Phase 3: ProPainter output path handling not verified — must point to .mp4 file not a directory
- Phase 3: `start=0, end=duration` reset for inpainted segments not verified in `renderWithInpainting()`
- Phase 3: `--cpu` flag confirmed absent from ProPainter argparse (correct — was removed from spec)
- None of the Phase 1/2/3 checklist items in FEATURES.md have been run

**Files changed:**
- `scripts/smart_crop.py` — new file
- `scripts/sam_helper.py` — new file
- `scripts/inpaint_worker.py` — new file
- `scripts/analyze_and_edit.py` — `_render_segment_ffmpeg()` crop + SAM filter chain
- `app.py` — 5 new endpoints, `_inpaint_jobs` dict
- `src/components/SegmentCard.tsx` — CropTool + SamTool added
- `src/components/InpaintTab.tsx` — new file
- `src/components/MainContent.tsx` — Inpaint tab wired
- `src/hooks/usePipeline.ts` — crop/SAM/inpaint state
- `src/lib/api.ts` — new interfaces and API functions
- `src/pages/Index.tsx` — prop wiring for new features

**Next session starts with:**
Run Phase 1 checklist item 1:
`python -c "from scripts.smart_crop import compute_auto_crop; print(compute_auto_crop('raw_clips/your_clip.mp4', 0, 3))"`
Fix any import errors before touching the UI. Do not move to Phase 2 testing until Phase 1 checklist is fully green.

---

### 2026-02-26 [afternoon]

**Phase:** Pre-flight audit + documentation + bugfix
**Commits:** uncommitted (trim fix only — no new features)

**Completed this session:**
- `FEATURES.md` — added "Implementation Status" section with per-phase requirement tables, spec deviation table (--mask_dilation default, --cpu absent, single PNG mask, _inpaint_jobs vs _pipeline_state, PipelineContext.tsx unchanged)
- `FEATURES.md` — updated testing checklists with note that no items verified yet
- Audited all 5 pre-run concerns raised against the implementation:
  1. `--mask_dilation`: confirmed ProPainter defaults to 4 — no action needed, can increase to 8–12 if ghosting seen
  2. SAM split-grade filter_complex (`analyze_and_edit.py:505–511`): confirmed structurally correct; grep backslash artifact was display-only, actual file at line 471 is correct
  3. Crop filter order (`analyze_and_edit.py:473–476`): confirmed `crop_prefix` prepended before `scale=` — correct
  4. ProPainter output path (`inpaint_worker.py:199–208`): confirmed `shutil.move` to `.mp4` file, `output_path` in status JSON points to file — correct
  5. `renderWithInpainting` trim reset: confirmed bug — `trimStart`/`trimEnd` were absolute source timestamps, invalid after inpainted clip resets to start=0
- `src/hooks/usePipeline.ts:285–286` — fixed: inpainted segments now subtract `seg.start` from trim values to re-baseline to the 0-origin inpainted clip

**Tested and confirmed working:**
- Frontend build clean after trim fix: `✓ built in 3m 8s` (exit code 0)

**Broken / known issues:**
- None introduced this session
- All Phase 1/2/3 checklist items in FEATURES.md still unverified (no end-to-end testing performed)
- `--mask_dilation` not passed explicitly — uses ProPainter default of 4; may need increase to 8–12 for thick freehand masks if ghosting artefacts appear

**Files changed:**
- `FEATURES.md` — Implementation Status section + updated testing checklists
- `src/hooks/usePipeline.ts` — `renderWithInpainting()` trim re-baseline fix (lines 285–286)

**Next session starts with:**
Run Phase 1 checklist item 1:
`python -c "from scripts.smart_crop import compute_auto_crop; print(compute_auto_crop('raw_clips/your_clip.mp4', 0, 3))"`
Fix any import errors before attempting UI or render tests.