# Generative Features Spec
## For Claude Code — read CLAUDE.md first, then this file

This document defines three new features to add to the Video Agent pipeline.
Build them strictly in Phase order. Do not start Phase 2 until Phase 1 is
tested end-to-end. Do not start Phase 3 until Phase 2 is tested.

---

## Overview of the three features

| Feature | Name | Speed | Where it runs |
|---------|------|-------|---------------|
| A | Smart Crop / Reframe | Instant | At render time — ffmpeg filter only |
| B | SAM Subject Isolation | ~5s/segment | Pre-process, triggered from Review UI |
| C | ProPainter Inpaint | 20–40 min/segment | New "Inpaint" tab, separate from Review, fully skippable |

---

## Current pipeline (do not break this)

```
Run Pipeline → analyze → enrich (SigLIP) → plan_edit
    → [REVIEW TAB] → user accepts/rejects/trims/grades
    → Render → [OUTPUT TAB]
```

## New pipeline after these features

```
Run Pipeline → analyze → enrich → plan_edit
    → [REVIEW TAB]
         Feature A: user sets crop region per segment (optional)
         Feature B: user clicks subject point → SAM runs → mask overlay shown
    → Render (applies crop + SAM split-grade via ffmpeg)
    → [INPAINT TAB]  ← NEW — entirely skippable
         Feature C: user draws removal region per segment (optional)
         Background job runs ProPainter
         "Skip Inpaint" button always visible
    → [OUTPUT TAB]
```

---

## Phase 1 — Smart Crop / Reframe (Feature A)

### Goal
Allow users to specify a 9:16 crop region per segment in the Review UI.
At render time, the crop is applied as an ffmpeg filter.
If no crop is set, the pipeline auto-centres (no behaviour change from today).
This is in addition to the existing features and sits when drop down option is selected.

### New file: `scripts/smart_crop.py`

```python
"""
smart_crop.py
-------------
Subject-tracking crop path computation.

Given a video path + time range, detects the dominant subject centroid
per frame (face cascade → body cascade → frame centre fallback),
smooths the crop X position with a rolling average to prevent jitter,
and returns a single representative X offset for the segment midframe.

Also provides compute_auto_crop() which returns the best static X offset
for the whole segment (used when user has not manually set a crop).

Output is always: { x: int, y: int, w: int, h: int }
where w = floor(source_height * 9/16), h = source_height, y = 0.
Width is always locked to 9:16 ratio. Only X is variable.
"""
```

Implementation notes for Claude Code:
- Use `cv2.CascadeClassifier` with `haarcascade_frontalface_default.xml` first
- Fall back to `haarcascade_upperbody.xml` if no face found
- Fall back to horizontal centre if no body found
- Sample every 10th frame in the segment range, not every frame
- Smooth X positions with `np.convolve(x_positions, np.ones(5)/5, mode='same')`
- Return the median X of the smoothed positions as the static crop X
- Clamp X so `x + w <= source_width` always

### Changes to `analyze_and_edit.py`

In `_render_segment_ffmpeg()`, add crop filter support:

```python
# If seg has a crop field, prepend a crop filter before the scale filter
# crop=w:h:x:y  (ffmpeg crop syntax)
# Example: crop=607:1080:200:0,scale=1080:1920:...
```

The crop filter must come BEFORE the scale filter in the vf chain.
If `crop` field is absent or None, behaviour is unchanged.

### New segment data field

```typescript
crop?: {
  x: number        // left edge in source pixels
  y: number        // always 0 — full height crop
  w: number        // source_height * 9/16, rounded down to even
  h: number        // source height
  auto: boolean    // true = computed by smart_crop.py, false = user-set
}
```

### New API endpoint

```
POST /api/crop_auto
  body: { video_path: string, start: number, end: number }
  returns: { x: int, y: int, w: int, h: int, source_w: int, source_h: int }
  runs smart_crop.compute_auto_crop() synchronously (~1s)
  called when user expands a segment card for the first time
```

### Review UI changes — `SegmentCard.tsx`

Inside the expanded card, add a "Crop" section between the video player and
the grade panel.

**Visual design:**
- Show a thumbnail of the segment midframe (use existing `/video/` endpoint with `#t=` fragment)
- Overlay a semi-transparent dark mask on the left and right of a draggable 9:16 crop box
- The crop box has a left drag handle only (right edge is locked to left+w)
- Show source resolution and crop coordinates as text below: `Crop: x=340 (1080×1920 from 3840×2160)`
- "Auto-detect" button — calls `POST /api/crop_auto`, sets the crop box position
- "Reset" button — removes crop field entirely (render will auto-centre)

**State:**
- Add `cropData: Record<string, CropSettings>` to segment state in `usePipeline.ts`
  (keyed by segment index, same pattern as `trimData` and `gradeData`)
- `updateCrop(index, crop)` function
- Include `crop: cropData[idx] ?? null` in the segment payload sent to `POST /api/review`

**Only show the crop tool when source video is wider than 9:16.**
If source is already 9:16 or narrower, hide the crop section entirely.

---

## Phase 2 — SAM Subject Isolation (Feature B)

### Goal
User clicks a point on a segment's video frame in the Review UI.
SAM ViT-B generates a binary mask of the subject at that point.
At render time, the mask is used to apply a different colour grade to
subject vs background (subject gets the user's grade settings, background
gets a desaturated/darkened version).

### New file: `scripts/sam_helper.py`

```python
"""
sam_helper.py
-------------
SAM ViT-B subject isolation helper.

Loads SAM ViT-B (fits in 4GB VRAM / runs on CPU).
Given a video path, timestamp, and a (point_x, point_y) prompt
as fractions of frame dimensions (0.0–1.0), returns a binary mask
as a base64-encoded PNG (same dimensions as the source frame).

Model checkpoint: automatically downloaded to style/sam_vit_b.pth
on first use (~375MB).

Environment:
  SAM_DEVICE   override device ("cuda"/"cpu"), default auto
"""
```

Implementation notes for Claude Code:
- Use `segment_anything` library: `pip install segment-anything`
- Model download URL: `https://dl.fbaipublicfiles.com/segment_anything/sam_vit_b_01ec64.pth`
- Download to `BASE_DIR / "style" / "sam_vit_b.pth"` (same folder as aesthetic_mlp.pth)
- Use `SamPredictor`, not `SamAutomaticMaskGenerator` — point prompt only
- The point is a positive prompt (label=1). No negative prompts needed.
- Return the largest mask if SAM returns multiple candidates
- Convert mask to uint8 PNG, base64-encode, return as string
- The mask PNG is full-frame resolution (not thumbnail resolution)

### Changes to `analyze_and_edit.py`

In `_render_segment_ffmpeg()`, add SAM split-grade support:

```python
# If seg has sam_mask field and sam_mask.ready == True:
#   1. Write mask PNG to tmp file from base64
#   2. Build ffmpeg filter chain:
#      [base video] → split into [fg_branch] and [bg_branch]
#      [fg_branch]  → apply user's full grade (brightness/contrast/sat/lut)
#      [bg_branch]  → apply desaturated grade (saturation * 0.3, brightness * 0.85)
#      [mask]       → alphamerge or overlay to blend fg over bg
#   3. This replaces the single grade_filter with a split filter chain
#
# If sam_mask absent or not ready: use existing single grade_filter (no change)
```

The ffmpeg filter chain for split grading:
```
-filter_complex
  "[0:v]split[fg][bg];
   [fg]<fg_grade_filter>[fg_graded];
   [bg]<bg_grade_filter>[bg_graded];
   [bg_graded][fg_graded][1:v]maskedmerge[v]"
-i <mask_path>
-map [v] -map 0:a
```
Where `[1:v]` is the mask input (second `-i` argument).

### New segment data field

```typescript
sam_mask?: {
  point_x: number    // user click X as fraction 0.0–1.0 of frame width
  point_y: number    // user click Y as fraction 0.0–1.0 of frame height
  mask_b64: string   // base64 PNG of the binary mask
  ready: boolean     // false while SAM is running, true when done
  timestamp: number  // which frame the mask was generated from (seconds)
}
```

### New API endpoint

```
POST /api/sam_mask
  body: {
    video_path: string,
    timestamp: number,   // midframe of segment in seconds
    point_x: number,     // 0.0–1.0 fraction of frame width
    point_y: number      // 0.0–1.0 fraction of frame height
  }
  returns: { mask_b64: string, width: int, height: int }
  synchronous — runs SAM, returns when done (~5s)
  Flask timeout must be set to at least 30s for this endpoint
```

### Review UI changes — `SegmentCard.tsx`

Add a "Subject" section in the expanded card, below the crop tool.

**Visual design:**
- Label: "SAM Subject Isolation"
- Show the segment thumbnail with a crosshair cursor
- User clicks anywhere on the thumbnail — that point is sent to `POST /api/sam_mask`
- While waiting: spinner overlay on thumbnail, "Detecting subject…" text
- When done: show the mask overlaid as a lime-green semi-transparent layer (opacity 0.35)
  over the thumbnail so user can see what was isolated
- Show toggle: "Split Grade: ON/OFF" — if OFF, mask is ignored at render time
- "Clear" button — removes sam_mask field

**State:**
- Add `samData: Record<string, SamMaskSettings>` to segment state in `usePipeline.ts`
- `updateSamMask(index, samMask)` function
- Include `sam_mask: samData[idx] ?? null` in segment payload sent to `POST /api/review`

**Important:** The click point must be translated from thumbnail pixel coordinates
to 0.0–1.0 fractions before sending to the API. The thumbnail is not necessarily
the same resolution as the source video.

---

## Phase 3 — ProPainter Inpaint (Feature C)

### Goal
After the main video is rendered and appears in the Output tab, a new
"Inpaint" tab becomes available. The user can open it, draw removal regions
on any segment from the rendered video, and trigger ProPainter to fill them.
The tab can be skipped entirely — if the user never opens it or clicks
"Skip Inpaint", the output video from the Output tab is the final result.

### Tab structure change

Current tabs: `Log | Review | Output`
New tabs:     `Log | Review | Output | Inpaint`

The Inpaint tab:
- Is greyed out / disabled until `phase === "done"` (output video exists)
- Has a "Skip Inpaint →" button at the top right, always visible
- Shows the rendered output video at the top for reference
- Below: a list of the accepted segments (same segments that were rendered)
- Each segment has: thumbnail, "Draw Region" button, status indicator
- "Render with Inpainting" button at the bottom — only active if at least
  one segment has a completed inpaint job

### New file: `scripts/inpaint_worker.py`

```python
"""
inpaint_worker.py
-----------------
ProPainter wrapper for the Video Agent pipeline.

Calls ProPainter's inference_propainter.py as a subprocess.
Writes progress to a JSON status file so Flask can poll it.
Returns path to the inpainted output clip on success.

ProPainter location: D:\video-agent\ProPainter\
Weights location:    D:\video-agent\ProPainter\weights\

Usage:
  python -m scripts.inpaint_worker \
    --video  path/to/segment.mp4 \
    --mask   path/to/mask.png \
    --output path/to/output.mp4 \
    --job_id abc123
    
Status file: output/inpaint_jobs/<job_id>.json
  { "status": "running"|"done"|"failed",
    "progress": 0.0–1.0,
    "frames_done": int,
    "frames_total": int,
    "output_path": str | null,
    "error": str | null }
"""
```

Implementation notes for Claude Code:

**ProPainter call pattern:**
```python
PROPAINTER_DIR = BASE_DIR / "ProPainter"
cmd = [
    sys.executable,
    str(PROPAINTER_DIR / "inference_propainter.py"),
    "--video",       str(video_path),
    "--mask",        str(mask_dir),   # ProPainter expects a directory of per-frame PNGs
    "--output",      str(output_dir),
    "--width",       "640",           # downscale for CPU — 640px is the practical max
    "--height",      "1138",          # maintain 9:16 ratio at 640px wide
    "--fp16",                         # half precision — reduces memory
    "--cpu",                          # force CPU mode on M1200 (4GB VRAM too small for full model)
]
```

**Mask preparation:**
ProPainter expects a directory of per-frame mask PNGs named `00000.png`, `00001.png`, etc.
The `inpaint_worker.py` must:
1. Extract frames from the segment video to a temp dir
2. Resize the user's drawn mask to match each frame's resolution
3. Save as numbered PNGs in a `masks/` subdirectory
4. Run ProPainter
5. Reassemble output frames back to MP4 using ffmpeg

**Progress parsing:**
ProPainter prints lines like `Processing: 12/47 frames`.
Parse these to update the status JSON file during the run.

**Output resolution:**
Run at 640×1138 (9:16 at 640px) regardless of source resolution.
The final render pipeline will handle upscaling if needed.
This keeps CPU inpaint time to ~20 min per 3s segment instead of 2 hours.

### New API endpoints

```
POST /api/inpaint/start
  body: {
    segment_index: int,
    video_path: string,
    start: number,
    end: number,
    mask_b64: string     // user-drawn region as base64 PNG
  }
  returns: { job_id: string }
  spawns inpaint_worker.py as background subprocess
  non-blocking — returns immediately

GET /api/inpaint/status/<job_id>
  returns: {
    status: "pending"|"running"|"done"|"failed",
    progress: number,       // 0.0–1.0
    frames_done: int,
    frames_total: int,
    estimated_seconds: int, // rough estimate based on progress rate
    output_path: string | null
  }

POST /api/inpaint/cancel/<job_id>
  kills the subprocess, cleans up temp files
  returns: { ok: true }
```

Store active inpaint jobs in a module-level dict in `app.py`:
```python
_inpaint_jobs: dict[str, dict] = {}   # job_id → { proc, status_file, segment_index }
```

### New pipeline state fields

Add to `_pipeline_state` in `app.py`:
```python
"inpaint_jobs": {},       # job_id → status dict
"inpaint_phase": "idle",  # "idle" | "running" | "done" | "skipped"
```

### Inpaint UI — new `InpaintTab.tsx` component

Create `src/components/InpaintTab.tsx`.

**Layout:**
```
┌─────────────────────────────────────────────────────┐
│  Inpaint Cleanup        [Skip Inpaint →]            │
├─────────────────────────────────────────────────────┤
│  Rendered output preview (small, top)               │
├─────────────────────────────────────────────────────┤
│  Segment 1  [thumbnail]  [Draw Region] [status]     │
│  Segment 2  [thumbnail]  [Draw Region] [status]     │
│  ...                                                │
├─────────────────────────────────────────────────────┤
│  [Render with Inpainting]  (disabled if no jobs)    │
└─────────────────────────────────────────────────────┘
```

**Draw Region modal:**
When user clicks "Draw Region" on a segment:
- Open a modal/drawer showing the segment thumbnail at ~400px wide
- User draws a freehand region using canvas `mousedown/mousemove/mouseup`
- Drawn path is filled to create a binary mask
- "Confirm Region" button — sends to `POST /api/inpaint/start`, closes modal
- "Clear" button — resets canvas

**Progress display per segment:**
After job is started, show:
- Amber pulsing dot + `"Inpainting… 8 min remaining"` (from estimated_seconds)
- Progress bar (thin, under the segment row)
- Poll `GET /api/inpaint/status/<job_id>` every 5 seconds

**"Render with Inpainting" button:**
- Disabled until at least one job is `status === "done"`
- On click: calls existing `POST /api/review` but replaces `video_path` for
  inpainted segments with the `output_path` from their completed job
- Segments without inpaint jobs are included unchanged

### Drawing the mask — canvas implementation

The mask drawn by the user must be:
1. Captured as a canvas ImageData
2. Converted to a binary PNG (white = remove, black = keep)
3. base64-encoded
4. Sent to the API

```typescript
// After user finishes drawing:
const canvas = canvasRef.current
const ctx = canvas.getContext('2d')
// Threshold to binary — any drawn pixel becomes white, rest black
const imageData = ctx.getImageData(0, 0, canvas.width, canvas.height)
// ... threshold alpha channel to 0/255 ...
const maskDataUrl = canvas.toDataURL('image/png')
const maskB64 = maskDataUrl.split(',')[1]
```

The mask must be sent at the **thumbnail resolution** — `inpaint_worker.py`
is responsible for resizing it to match each extracted video frame.

---

## File change summary

### New files to create:

| File | Phase |
|------|-------|
| `scripts/smart_crop.py` | 1 |
| `scripts/sam_helper.py` | 2 |
| `scripts/inpaint_worker.py` | 3 |
| `src/components/InpaintTab.tsx` | 3 |

### Files to modify:

| File | Phase | What changes |
|------|-------|-------------|
| `scripts/analyze_and_edit.py` | 1 | `_render_segment_ffmpeg()` — add crop filter |
| `scripts/analyze_and_edit.py` | 2 | `_render_segment_ffmpeg()` — add SAM split-grade filter chain |
| `app.py` | 1 | Add `POST /api/crop_auto` |
| `app.py` | 2 | Add `POST /api/sam_mask` |
| `app.py` | 3 | Add `POST /api/inpaint/start`, `GET /api/inpaint/status/<id>`, `POST /api/inpaint/cancel/<id>` |
| `app.py` | 3 | Add `_inpaint_jobs` dict, `inpaint_phase` state field |
| `src/components/SegmentCard.tsx` | 1 | Add crop drag overlay |
| `src/components/SegmentCard.tsx` | 2 | Add SAM point-click + mask overlay |
| `src/components/MainContent.tsx` | 3 | Add Inpaint tab (4th tab, disabled until phase=done) |
| `src/hooks/usePipeline.ts` | 1 | Add `cropData`, `updateCrop()` |
| `src/hooks/usePipeline.ts` | 2 | Add `samData`, `updateSamMask()` |
| `src/hooks/usePipeline.ts` | 3 | Add inpaint job polling logic |
| `src/context/PipelineContext.tsx` | 3 | Add `inpaint_phase` to phase type union |

### Files that do NOT change:

- `scripts/editing_brain.py` — no changes needed
- `scripts/transitions.py` — no changes needed
- `scripts/llm_planner.py` — no changes needed
- `scripts/semantic_siglip.py` — no changes needed
- `scripts/semantic_aesthetic.py` — no changes needed
- `scripts/pipeline_logger.py` — no changes needed
- `scripts/whisper_helper.py` — no changes needed
- `src/components/DashboardHeader.tsx` — no changes needed
- `src/components/PipelineSidebar.tsx` — no changes needed

---

## ProPainter installation details

Already installed at: `D:\video-agent\ProPainter\`

The `inpaint_worker.py` must use this absolute path. Add a constant at the
top of the script:

```python
PROPAINTER_DIR = Path(__file__).resolve().parent.parent / "ProPainter"
PROPAINTER_SCRIPT = PROPAINTER_DIR / "inference_propainter.py"
PROPAINTER_WEIGHTS = PROPAINTER_DIR / "weights"
```

Verify these exist before starting any job. If `PROPAINTER_SCRIPT` does not
exist, `POST /api/inpaint/start` must return a clear error:
`{ "error": "ProPainter not found at D:\\video-agent\\ProPainter" }`

---

## Testing checklist — verify each phase before proceeding

### Phase 1 checklist
- [ ] `python -c "from scripts.smart_crop import compute_auto_crop; print('ok')"` succeeds
- [ ] `POST /api/crop_auto` returns `{ x, y, w, h, source_w, source_h }` for a real clip
- [ ] Expanding a segment card in Review shows the crop overlay
- [ ] Dragging the crop handle updates the displayed coordinates
- [ ] Rendering an accepted segment with `crop` set produces a 9:16 cropped output
- [ ] Rendering a segment without `crop` set produces the same output as before

### Phase 2 checklist
- [ ] `python -c "from scripts.sam_helper import run_sam; print('ok')"` succeeds
- [ ] SAM checkpoint downloads automatically on first use
- [ ] `POST /api/sam_mask` returns a base64 PNG within 30s
- [ ] Clicking a subject in the Review UI shows the green mask overlay
- [ ] Rendering with SAM mask set produces visually distinct subject/background grades
- [ ] Rendering without SAM mask is unchanged

### Phase 3 checklist
- [ ] `python -m scripts.inpaint_worker --help` succeeds
- [ ] `POST /api/inpaint/start` returns `{ job_id }` immediately
- [ ] Status file appears at `output/inpaint_jobs/<job_id>.json` within 5s
- [ ] `GET /api/inpaint/status/<job_id>` returns progress updates
- [ ] Inpaint tab is disabled when phase is not "done"
- [ ] Inpaint tab becomes active after successful render
- [ ] Draw Region modal opens and allows freehand drawing
- [ ] "Render with Inpainting" replaces the inpainted segment's video_path
- [ ] "Skip Inpaint" dismisses the tab without affecting the output video
