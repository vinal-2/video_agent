# VideoAgent — Features Spec
## For Claude Code — read CLAUDE.md and SESSIONS.md first, then this file

Build phases strictly in order. Do not start a phase until the previous
phase passes its full testing checklist. Read the "Known risks" and
"Bugs fixed" sections before touching any code — these record real
failures that have already been debugged.

---

## Feature status overview

| Phase | Feature | Status |
|-------|---------|--------|
| 1 | Smart Crop / Reframe | ✅ Implemented — scripted tests passed, browser UI pending (needs landscape clip) |
| 2 | SAM Subject Isolation | ✅ Implemented — scripted tests passed, 1 bug fixed, browser UI pending (safe to test locally) |
| 3 | ProPainter Inpaint (local CPU) | ✅ Implemented — API + CLI tested, 5 bugs fixed, browser test **blocked by RAM** (see below) |
| 4 | ProPainter Inpaint (remote — Google Drive + Colab) | 🔲 Not started — **infrastructure setup unblocked, start now** |

**Do not re-implement Phases 1, 2, or 3.**
Their code is committed and partially verified. Phase 3 local inpaint stays
as the fallback when Colab is offline — it is not deleted.

### Hardware constraints — read before planning any test session

**Machine:** Windows laptop, Quadro M1200 (4 GB VRAM, Maxwell, no tensor cores), 16 GB RAM

**RAM situation:** Background processes consume ~12 GB, leaving ~4 GB free.
ProPainter at 400px requires ≥5 GB free. This means **Phase 3 browser testing
is unreliable on this machine** — it may work if you kill everything first, but it
is not a reliable baseline. Do not treat a passing run as proof; do not treat a
crash as a regression.

**What this means for test ordering:**

| Test | RAM needed | Safe locally? |
|------|-----------|---------------|
| Phase 1 scripted (import, API) | negligible | ✅ Yes |
| Phase 1 browser (drag UI, landscape clip) | negligible | ✅ Yes |
| Phase 2 scripted (import, API) | ~1.5 GB peak | ✅ Yes |
| Phase 2 browser (SAM overlay, split-grade render) | ~1.5 GB peak | ✅ Yes |
| Phase 3 browser (DrawRegionModal, Render) | ≥5 GB free | ⚠️ Marginal — kill everything first |
| Phase 4 infrastructure (rclone, Colab heartbeat) | 0 | ✅ Yes — do this first |
| Phase 4 end-to-end (real job via Colab) | negligible (job offloaded) | ✅ Yes |

**Recommended order given hardware:**
1. Phase 2 browser test (SAM) — safe, do now
2. Phase 4 infrastructure (rclone + Colab) — no code, no RAM, unblock ProPainter testing
3. Phase 3 browser test — attempt locally with everything killed, OR test end-to-end via Colab once Phase 4 infra is live
4. Phase 1 landscape browser test — generate test clip, test drag UI

---

## Pipeline flow (do not break any of this)

```
Run Pipeline → analyze → enrich (SigLIP) → plan_edit
    → [REVIEW TAB]
         Phase 1: optional crop region per segment
         Phase 2: optional SAM subject point → mask overlay
    → Render (applies crop + SAM split-grade via ffmpeg)
    → [INPAINT TAB]  ← Phase 3 UI — fully skippable
         Phase 3/4: user draws removal region per segment
         Local mode  → Phase 3: ProPainter runs on local CPU
         Remote mode → Phase 4: job sent to Google Drive, Colab processes it
         "Skip Inpaint →" always visible
    → [OUTPUT TAB]
```

---

## Phase 1 — Smart Crop / Reframe ✅ IMPLEMENTED

### What was built (commit dc08832)
- `scripts/smart_crop.py` — face/body cascade, median-X crop, 9:16 lock
- `analyze_and_edit.py` — crop filter in `_render_segment_ffmpeg()`
- `app.py` — `POST /api/crop_auto`
- `SegmentCard.tsx` — `CropTool`: drag handle, auto-detect button, reset button
- `usePipeline.ts` — `cropData`, `updateCrop()`, crop included in render payload

### Confirmed spec deviations
None recorded.

### Known risks
1. **Crop filter order.** In `_render_segment_ffmpeg()`, the crop filter must come
   BEFORE the scale filter. The vf string must read `crop=...,scale=...` not
   `scale=...,crop=...`. Wrong order produces black-padded or clipped output.

2. **Crop tool visibility.** The `CropTool` returns null when `isWider` is false —
   verified in code. Will only display for landscape sources. All current raw clips
   are portrait (2160×3840) so the tool is correctly hidden in the live UI.

### Testing checklist
- [ ] Baseline: render a segment with NO crop set → output identical to pre-Phase-1
- [x] `python -c "from scripts.smart_crop import compute_auto_crop; print('ok')"` — **PASSED**
- [x] `POST /api/crop_auto` returns `{ x, y, w, h, source_w, source_h }` — **PASSED**
- [x] `compute_auto_crop` values correct for all 3 source clips (check_crop.py 3/3) — **PASSED**
- [x] `CropTool` hidden for portrait-native sources (`isWider` check) — **VERIFIED IN CODE**
- [ ] **PENDING — needs browser + landscape clip:** Crop overlay drag UI visible in Review tab
- [ ] **PENDING — needs browser + landscape clip:** Dragging handle updates coordinate display
- [ ] **PENDING — needs landscape clip:** Render with crop set → output correctly cropped to 9:16

**Note on landscape clip:** All 3 current raw clips are portrait. To test the browser
UI, add a landscape clip to `raw_clips/`. Use `scripts/make_test_clip.py` to generate
a synthetic 3840×2160 test clip, or supply a real landscape source.

---

## Phase 2 — SAM Subject Isolation ✅ IMPLEMENTED

### What was built (commit c17b42d)
- `scripts/sam_helper.py` — SAM ViT-B, point prompt, auto-downloads `style/sam_vit_b.pth`
- `analyze_and_edit.py` — split-grade ffmpeg `filter_complex` in `_render_segment_ffmpeg()`
- `app.py` — `POST /api/sam_mask`
- `SegmentCard.tsx` — `SamTool`: click-to-segment, lime-green mask overlay, split-grade toggle
- `usePipeline.ts` — `samData`, `updateSamMask()`, `sam_mask` in render payload when `enabled=true`
- `src/lib/api.ts` — `SamMaskSettings` interface, `fetchSamMask()`

### Confirmed spec deviations
- Vite proxy timeout raised to **120000ms** (spec said 30000ms — CPU SAM takes 50–84s)

### Bugs fixed (do NOT revert)
**`use_sam` guard never triggered** — `analyze_and_edit.py` checked `sam_mask.get("ready")`
but the `SamMaskSettings` interface has no `ready` field, only `enabled`. SAM split-grade
was silently skipped on every render. Fixed: removed the `ready` check; `mask_b64`
presence + `enabled=true` is the correct guard condition.

### Known risks
1. **ffmpeg filter_complex syntax.** Highest-risk piece in the codebase. Before running
   any SAM-enabled render, print the full ffmpeg command from `_render_segment_ffmpeg()`.
   The command must match this structure exactly:

   ```
   ffmpeg ... -i <video_file> -i <mask.png>
     -filter_complex
       "[0:v]split[fg][bg];
        [fg]<grade_filter>[fg_graded];
        [bg]eq=saturation=0.3:brightness=0.85[bg_graded];
        [bg_graded][fg_graded][1:v]maskedmerge[v]"
     -map [v] -map 0:a ...
   ```

   If the mask input index is wrong or any pad label is unmatched, ffmpeg exits
   with returncode 1 and only a `[warn]` in the log — the segment is silently skipped.

2. **CPU SAM performance.** SAM ViT-B takes **50–84 seconds on CPU** (cold start ~40s +
   inference ~10–15s). The UI will appear frozen during this time. This is expected —
   not a bug. Subsequent clicks on the same session are faster (~10–15s) because the
   predictor is cached. Do not add a timeout shorter than 120s anywhere in the chain.

### Testing checklist
- [ ] Baseline: render a segment with NO sam_mask set → output identical to pre-Phase-2
- [x] `python -c "from scripts.sam_helper import run_sam; print('ok')"` — **PASSED**
- [x] SAM checkpoint at `style/sam_vit_b.pth` (358 MB) — **CONFIRMED**
- [x] `POST /api/sam_mask` returns `{ mask_b64, width, height }` — **PASSED** (50s CPU)
- [x] Vite proxy timeout set to 120000ms — **DONE** (`vite.config.ts`)
- [x] `filter_complex` structure verified (maskedmerge order correct) — **VERIFIED IN CODE**
- [x] `use_sam` guard fixed (`ready` → `mask_b64` presence) — **FIXED**
- [ ] **PENDING — needs browser:** Click subject in Review UI → green mask overlay appears
- [ ] **PENDING — needs browser + render:** Render with SAM enabled → subject visually distinct

---

## Phase 3 — ProPainter Inpaint Local ✅ IMPLEMENTED (fallback mode)

### What was built (commit 689ad82)
- `scripts/inpaint_worker.py` — ffmpeg extraction, ProPainter subprocess, progress JSON
- `app.py` — `POST /api/inpaint/start`, `GET /api/inpaint/status/<id>`, `POST /api/inpaint/cancel/<id>`
- `app.py` — `_inpaint_jobs` module-level dict + `_inpaint_lock`
- `src/components/InpaintTab.tsx` — DrawRegionModal canvas, segment rows, progress bar, Render button
- `src/components/MainContent.tsx` — Inpaint tab, 4th tab, disabled until `phase === "done"`
- `src/hooks/usePipeline.ts` — `inpaintJobs`, 5s polling, `beginInpaint()`, `removeInpaintJob()`, `renderWithInpainting()`
- `src/lib/api.ts` — `InpaintJobStatus`, `InpaintJob`, `startInpaintJob()`, `getInpaintStatus()`, `cancelInpaintJob()`

### Confirmed spec deviations (do NOT revert these)
| Original spec said | What was actually built | Why correct |
|--------------------|------------------------|-------------|
| `--mask` = directory of per-frame PNGs | `--mask` = single PNG | ProPainter's `read_mask()` tiles a single PNG internally via `flow_masks * length` |
| `--cpu` flag to force CPU mode | Flag omitted entirely | Does not exist in ProPainter's argparse |
| `--fp16` flag | Retained | Silently ignored on CPU — harmless |
| `--width 640 --height 1138` hardcoded | Dynamic cap (400px max) | See OOM bug below |
| Modify `PipelineContext.tsx` | File not modified | Project uses `usePipeline.ts` as state layer |
| `inpaint_phase` in `_pipeline_state` | Module-level `_inpaint_jobs` dict | Functionally equivalent, cleaner isolation |

### Bugs fixed (do NOT revert)
1. **ProPainter OOM** — `--resize_ratio 0.5` produced 1080×1920 input to RAFT correlation,
   requiring 5.67 GB VRAM. Crashed on 4 GB Quadro M1200. Fixed in `inpaint_worker.py`:
   dynamic resolution cap at **400px wide** (keeping 9:16 aspect). This fits in 4 GB.

2. **Portrait forcing broken** — `--width 640 --height 1138` was hardcoded in the
   ProPainter subprocess call regardless of source aspect ratio. Fixed in `inpaint_worker.py`
   as part of the same dynamic cap change: dimensions are now computed from source
   aspect ratio, capped at 400px on the long edge.

3. **Canvas aspect ratio hardcoded** in `InpaintTab.tsx` DrawRegionModal. The canvas
   was a fixed size regardless of the segment's actual dimensions. Fixed: canvas
   dimensions now set dynamically from `onLoadedMetadata` on the video element.

4. **No post-render navigation** in `InpaintTab.tsx` — after "Render with Inpainting"
   completed, the UI stayed on the Inpaint tab with no indication of completion.
   Fixed: `.then(() => onSkip())` added so the user is navigated to the Output tab
   automatically on success.

5. **Trim timestamp re-baseline** in `usePipeline.ts` — when substituting an inpainted
   clip, `renderWithInpainting()` now resets `start: 0, end: <inpainted_duration>`.
   The original timestamps were meaningless for the new file (which starts at 0).

### ProPainter paths — do not change these
```python
PROPAINTER_DIR    = Path(__file__).resolve().parent.parent / "ProPainter"
PROPAINTER_SCRIPT = PROPAINTER_DIR / "inference_propainter.py"
PROPAINTER_WEIGHTS = PROPAINTER_DIR / "weights"
```

### Known risks
1. **`output_path` must be a file, not a directory.** `status.json` written by
   `inpaint_worker.py` must contain the path to the actual `.mp4` file. If it points
   to a directory, `renderWithInpainting()` sends that as an ffmpeg `-i` argument
   and fails silently.

2. **RAM requirement for browser testing.** ProPainter at 400px requires ≥5 GB free
   RAM. Before opening the DrawRegionModal, kill Vite and any orphaned `python.exe`
   processes: `tasklist | findstr python` in Windows, then `taskkill /PID <pid> /F`.
   Start Flask fresh, then open the UI. Do not run Vite dev server simultaneously.

### Testing checklist
- [ ] Baseline: "Skip Inpaint →" goes to Output tab without modifying output video
- [x] `python -m scripts.inpaint_worker --help` — **PASSED**
- [x] `POST /api/inpaint/start` returns `{ job_id }` immediately (non-blocking) — **PASSED**
- [x] Status file appears at `output/inpaint_jobs/<job_id>.json` within 5s — **PASSED**
- [x] `GET /api/inpaint/status/<job_id>` returns progress updates during a run — **PASSED**
- [ ] **PENDING — needs browser:** Inpaint tab disabled while `phase !== "done"`
- [ ] **PENDING — needs browser + ≥5 GB free RAM:** DrawRegionModal opens, freehand drawing works
- [ ] **PENDING — needs browser + ≥5 GB free RAM:** "Render with Inpainting" completes and navigates to Output tab
- [ ] **PENDING:** `output_path` in status.json points to `.mp4` file (not directory)
- [ ] **PENDING:** Rendered output uses inpainted clip with correct start=0, end=duration

### Pre-browser checklist (run once before opening the UI)
```powershell
# 1. Free RAM — kill any orphaned python processes
tasklist | findstr python
# For each PID shown: taskkill /PID <pid> /F

# 2. Check free RAM — need ≥5 GB
# Task Manager → Performance → Memory

# 3. Start Flask only (NOT Vite dev server)
cd D:\video-agent
.venv\Scripts\python app.py

# 4. Open browser directly at http://localhost:5100
# Do not use `npm run dev` simultaneously
```

---

## Phase 4 — ProPainter Inpaint Remote (Google Drive + Colab) 🔲 NOT STARTED

**Phase 4 infrastructure (rclone + Colab notebook) can be set up NOW — it is independent of Phase 3 browser tests.**
The Phase 3 API layer is tested and green. The only untested part is the browser UI, which is blocked
by local RAM (≥5 GB needed, ~4 GB available). Colab is the reliable path to end-to-end ProPainter testing.
Setting up Phase 4 infra first unblocks Phase 3 validation — do not wait.

**Phase 4 code changes** (modifying `inpaint_worker.py`, `app.py`, `InpaintTab.tsx`) should still happen
after infrastructure is verified working (heartbeat appearing locally).

### Goal
Offload ProPainter to a Colab T4 GPU (2–4 min/segment vs 20–40 min local).
Google Drive is the shared filesystem — no HTTP between the app and Colab,
no ngrok, no exposed ports. The app writes job files to a locally-synced
folder. rclone pushes them to Drive. Colab picks them up, processes them,
writes results back. The app reads results from the same local folder.

### What does NOT change in Phase 4
- All Phase 1, 2, 3 code — unchanged
- `analyze_and_edit.py` — no changes
- `SegmentCard.tsx` — no changes
- `MainContent.tsx` — no changes (Inpaint tab already exists)
- `PipelineContext.tsx` — no changes
- `smart_crop.py`, `sam_helper.py` — no changes
- The Phase 3 polling loop, progress bar, and "Render with Inpainting" button
  are fully reused — remote jobs return the same status shape as local jobs

### What changes in Phase 4
- `scripts/inpaint_worker.py` — add `--mode remote` branch
- `app.py` — `mode` field on `/api/inpaint/start`; remote status reading;
  new `GET /api/inpaint/colab_status`
- `src/components/InpaintTab.tsx` — mode toggle; Colab indicator; "Waiting for Colab…" state
- `src/hooks/usePipeline.ts` — `mode` param on `beginInpaint()`
- `src/lib/api.ts` — `mode` on request type; `getColabStatus()`
- New file: `colab/propainter_worker.ipynb`

---

### Drive folder structure

Sync root: `D:\video-agent\output\inpainted\`

```
D:\video-agent\output\inpainted\
  heartbeat.json                    ← Colab writes every 60s
  jobs\
    pending\
      <job_id>\
        segment.mp4                 ← extracted clip (400px wide, 9:16)
        mask.png                    ← binary mask (white=remove)
        job.json                    ← metadata
    processing\
      <job_id>\
        segment.mp4
        mask.png
        job.json
        progress.json               ← Colab writes every 30s
        cancel.flag                 ← app writes to request abort
    done\
      <job_id>\
        result.mp4
        status.json
    failed\
      <job_id>\
        status.json
```

**`job.json`:**
```json
{
  "job_id": "abc123",
  "mode": "remote",
  "segment_index": 2,
  "fps": 30.0,
  "duration": 3.2,
  "width": 400,
  "height": 711,
  "created_at": "2026-02-26T10:00:00"
}
```

**`progress.json`:**
```json
{ "frames_done": 24, "frames_total": 96, "updated_at": "2026-02-26T10:03:00" }
```

**`status.json` (done):**
```json
{
  "status": "done",
  "output_path": "jobs/done/abc123/result.mp4",
  "duration": 3.2,
  "completed_at": "2026-02-26T10:05:00"
}
```

**`status.json` (failed):**
```json
{ "status": "failed", "error": "ProPainter OOM at frame 24", "failed_at": "..." }
```

**`heartbeat.json`:**
```json
{ "status": "online", "updated_at": "2026-02-26T10:04:55", "colab_gpu": "Tesla T4" }
```

---

### New environment variable

Add to `.env` and read in `app.py`:

```python
INPAINT_MODE   = os.environ.get("INPAINT_MODE", "local")
DRIVE_SYNC_DIR = Path(os.environ.get(
    "DRIVE_SYNC_DIR", r"D:\video-agent\output\inpainted"))
```

If `DRIVE_SYNC_DIR` does not exist when a remote job is submitted, return:
```json
{ "error": "Drive sync folder not found. Set DRIVE_SYNC_DIR in .env and run rclone." }
```

---

### Changes to `scripts/inpaint_worker.py`

Add `--mode` argument:
```python
parser.add_argument("--mode", choices=["local", "remote"], default="local")
```

**Local mode:** unchanged — runs ProPainter subprocess as Phase 3.

**Remote mode:** fire-and-forget. Only:
1. Extract segment clip from source via ffmpeg (same dynamic 400px cap as local)
2. Resize mask PNG to match clip dimensions
3. Write `segment.mp4`, `mask.png`, `job.json` to `DRIVE_SYNC_DIR/jobs/pending/<job_id>/`
4. Write initial `status.json` with `{"status": "pending"}` to same folder
5. Exit — no blocking wait

---

### Changes to `app.py`

**`POST /api/inpaint/start`** — add `mode` to request (default `"local"`):
```python
mode = data.get("mode", "local")
```
Pass `--mode <mode>` to subprocess. Store in `_inpaint_jobs[job_id]`.

**`GET /api/inpaint/status/<job_id>`** — remote path resolution:
```python
if job["mode"] == "remote":
    done_path     = DRIVE_SYNC_DIR / "jobs" / "done"       / job_id / "status.json"
    failed_path   = DRIVE_SYNC_DIR / "jobs" / "failed"     / job_id / "status.json"
    progress_path = DRIVE_SYNC_DIR / "jobs" / "processing" / job_id / "progress.json"
    pending_path  = DRIVE_SYNC_DIR / "jobs" / "pending"    / job_id / "status.json"
    # Read whichever exists, return same shape as local mode
```

**`POST /api/inpaint/cancel/<job_id>`** — remote:
- Job in `pending/`: delete folder (not yet picked up)
- Job in `processing/`: write `cancel.flag` (Colab checks and aborts)
- Clean up `done/` or `failed/` if present

**New endpoint:**
```python
@app.route("/api/inpaint/colab_status")
def api_colab_status():
    heartbeat_file = DRIVE_SYNC_DIR / "heartbeat.json"
    if not heartbeat_file.exists():
        return jsonify({"online": False, "reason": "no heartbeat file"})
    try:
        data = json.loads(heartbeat_file.read_text())
        updated = datetime.fromisoformat(data["updated_at"])
        age = (datetime.now(timezone.utc) - updated).total_seconds()
        return jsonify({
            "online":            age < 120,
            "gpu":               data.get("colab_gpu", ""),
            "last_seen_seconds": int(age),
        })
    except Exception as e:
        return jsonify({"online": False, "reason": str(e)})
```

---

### Changes to `src/components/InpaintTab.tsx`

Add mode selector at top of tab:
```tsx
// Poll GET /api/inpaint/colab_status every 30s while tab is active
// Remote button disabled when colabOnline === false

<div className="flex items-center gap-2 mb-4">
  <span className="text-sm text-muted-foreground">Process on:</span>
  <button onClick={() => setMode("local")}
    className={mode === "local" ? "gradient-primary-btn ..." : "..."}>
    Local
  </button>
  <button onClick={() => setMode("remote")} disabled={!colabOnline}
    className={mode === "remote" ? "gradient-primary-btn ..." : "..."}>
    Remote (Colab)
    {colabOnline
      ? <span className="text-xs text-green-400 ml-1">● online</span>
      : <span className="text-xs text-muted-foreground ml-1">○ offline</span>}
  </button>
</div>
```

When mode is `"remote"` and job status is `"pending"`, show
`"Waiting for Colab to pick up job…"` instead of `"Inpainting…"`.

Pass mode to `beginInpaint()`:
```tsx
onConfirm={(maskB64) => beginInpaint(segmentIndex, maskB64, mode)}
```

---

### Changes to `src/hooks/usePipeline.ts`

```typescript
const beginInpaint = useCallback(
  async (segmentIndex: number, maskB64: string, mode: "local" | "remote" = "local") => {
    const result = await startInpaintJob({
      segment_index: segmentIndex,
      video_path:    segments[segmentIndex].video_path,
      start:         segments[segmentIndex].trimStart,
      end:           segments[segmentIndex].trimEnd,
      mask_b64:      maskB64,
      mode,
    })
    // rest unchanged
  },
  [segments]
)
```

---

### Changes to `src/lib/api.ts`

```typescript
export interface StartInpaintRequest {
  segment_index: number
  video_path:    string
  start:         number
  end:           number
  mask_b64:      string
  mode:          "local" | "remote"
}

export async function getColabStatus(): Promise<{
  online: boolean
  gpu?: string
  last_seen_seconds: number
  reason?: string
}> {
  const res = await fetch("/api/inpaint/colab_status")
  if (!res.ok) return { online: false, last_seen_seconds: 999 }
  return res.json()
}
```

---

### New file: `colab/propainter_worker.ipynb`

Four cells. Run Cell 1 once per session. Run Cell 4 once to verify. Run Cell 3
to start the worker loop (leave running).

**Cell 1 — Setup:**
```python
from google.colab import drive
drive.mount('/content/drive')
!pip install -q einops kornia av

DRIVE_ROOT     = '/content/drive/MyDrive/video_agent_jobs'
PENDING_DIR    = f'{DRIVE_ROOT}/jobs/pending'
PROCESSING_DIR = f'{DRIVE_ROOT}/jobs/processing'
DONE_DIR       = f'{DRIVE_ROOT}/jobs/done'
FAILED_DIR     = f'{DRIVE_ROOT}/jobs/failed'
HEARTBEAT      = f'{DRIVE_ROOT}/heartbeat.json'

import os
for d in [PENDING_DIR, PROCESSING_DIR, DONE_DIR, FAILED_DIR]:
    os.makedirs(d, exist_ok=True)

if not os.path.exists('/content/ProPainter'):
    !git clone https://github.com/sczhou/ProPainter /content/ProPainter
    !cd /content/ProPainter && pip install -q -r requirements.txt

print("Setup complete")
```

**Cell 2 — Helpers:**
```python
import json, shutil, subprocess, time, re
from datetime import datetime, timezone
from pathlib import Path

def utcnow_str():
    return datetime.now(timezone.utc).isoformat()

def write_heartbeat():
    try:
        r = subprocess.run(['nvidia-smi','--query-gpu=name','--format=csv,noheader'],
                           capture_output=True, text=True)
        gpu = r.stdout.strip()
    except Exception:
        gpu = "unknown"
    Path(HEARTBEAT).write_text(json.dumps({
        "status": "online", "updated_at": utcnow_str(), "colab_gpu": gpu
    }))

def write_progress(job_id, frames_done, frames_total):
    Path(PROCESSING_DIR, job_id, 'progress.json').write_text(json.dumps({
        "frames_done": frames_done, "frames_total": frames_total,
        "updated_at": utcnow_str()
    }))

def check_cancel(job_id):
    return Path(PROCESSING_DIR, job_id, 'cancel.flag').exists()

def run_propainter(job_id, job_dir):
    meta    = json.loads((job_dir / 'job.json').read_text())
    out_dir = f'/content/propainter_out/{job_id}'
    os.makedirs(out_dir, exist_ok=True)

    cmd = [
        'python', '/content/ProPainter/inference_propainter.py',
        '--video',  str(job_dir / 'segment.mp4'),
        '--mask',   str(job_dir / 'mask.png'),
        '--output', out_dir,
        '--width',  str(meta.get('width',  400)),
        '--height', str(meta.get('height', 711)),
        '--fp16', '--save_video',
    ]

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True)
    frames_total = int(meta.get('fps', 30) * meta.get('duration', 3))

    for line in proc.stdout:
        print(line, end='')
        m = re.search(r'(\d+)/(\d+)', line)
        if m and ('Processing' in line or 'Inpainting' in line):
            write_progress(job_id, int(m.group(1)), int(m.group(2)))
        if check_cancel(job_id):
            proc.terminate()
            return None, "Cancelled by user"

    proc.wait()
    if proc.returncode != 0:
        return None, f"ProPainter exited {proc.returncode}"

    mp4s = list(Path(out_dir).glob('*.mp4'))
    if not mp4s:
        return None, "No output MP4 found"
    return str(mp4s[0]), None
```

**Cell 3 — Worker loop (run and leave running):**
```python
import time

print("Worker started — watching for jobs...")
write_heartbeat()
last_heartbeat = time.time()

while True:
    if time.time() - last_heartbeat > 60:
        write_heartbeat()
        last_heartbeat = time.time()

    jobs = sorted(Path(PENDING_DIR).iterdir()) if Path(PENDING_DIR).exists() else []
    jobs = [j for j in jobs if j.is_dir()]

    if not jobs:
        time.sleep(30)
        continue

    job_dir  = jobs[0]
    job_id   = job_dir.name
    proc_dir = Path(PROCESSING_DIR) / job_id
    print(f"\n[{utcnow_str()}] Picked up: {job_id}")
    shutil.move(str(job_dir), str(proc_dir))

    try:
        output_mp4, error = run_propainter(job_id, proc_dir)
        if error:
            raise RuntimeError(error)

        done_dir    = Path(DONE_DIR) / job_id
        done_dir.mkdir(parents=True, exist_ok=True)
        result_path = done_dir / 'result.mp4'
        shutil.copy(output_mp4, result_path)

        r = subprocess.run(['ffprobe','-v','error','-show_entries','format=duration',
                            '-of','csv=p=0', str(result_path)],
                           capture_output=True, text=True)
        duration = float(r.stdout.strip()) if r.stdout.strip() else 0.0

        (done_dir / 'status.json').write_text(json.dumps({
            "status": "done",
            "output_path": f"jobs/done/{job_id}/result.mp4",
            "duration": duration,
            "completed_at": utcnow_str(),
        }))
        print(f"[{utcnow_str()}] Done — {duration:.1f}s")

    except Exception as e:
        print(f"[{utcnow_str()}] FAILED: {e}")
        failed_dir = Path(FAILED_DIR) / job_id
        failed_dir.mkdir(parents=True, exist_ok=True)
        (failed_dir / 'status.json').write_text(json.dumps({
            "status": "failed", "error": str(e), "failed_at": utcnow_str()
        }))

    finally:
        if proc_dir.exists():
            shutil.rmtree(str(proc_dir), ignore_errors=True)
```

**Cell 4 — Verify setup (run once):**
```python
for folder in [PENDING_DIR, PROCESSING_DIR, DONE_DIR, FAILED_DIR]:
    Path(folder).mkdir(parents=True, exist_ok=True)
    print(f"OK: {folder}")

write_heartbeat()
print(f"Heartbeat written")

r = subprocess.run(['python','/content/ProPainter/inference_propainter.py','--help'],
                   capture_output=True, text=True)
print("ProPainter:", "OK" if r.returncode == 0 else f"FAILED:\n{r.stderr[:300]}")

r = subprocess.run(['nvidia-smi','--query-gpu=name,memory.total','--format=csv,noheader'],
                   capture_output=True, text=True)
print("GPU:", r.stdout.strip())
```

---

### rclone setup — one-time, not a code task

```powershell
winget install Rclone.Rclone
rclone config
# → name: videoagent_drive, type: drive, dedicated Google account OAuth

rclone ls videoagent_drive:video_agent_jobs/

# Start before every inpainting session (separate terminal, leave running)
rclone bisync videoagent_drive:video_agent_jobs `
  D:\video-agent\output\inpainted `
  --transfers=4 --poll-interval=15s --create-empty-src-dirs
```

---

### Phase 4 testing checklist

**Infrastructure first — before writing any code:**
- [ ] rclone installed and `rclone ls videoagent_drive:video_agent_jobs/` succeeds
- [ ] `D:\video-agent\output\inpainted\jobs\` folder exists locally
- [ ] Colab Cell 4 runs without error (Drive + ProPainter + GPU all accessible)
- [ ] `heartbeat.json` appears locally after running Colab Cell 4
- [ ] rclone bisync running: file written locally appears in Drive within 20s

**Code — run in order, stop at first failure:**
- [ ] Baseline: all Phase 3 local tests still pass after Phase 4 code merged
- [ ] `python -m scripts.inpaint_worker --mode remote --help` succeeds
- [ ] `POST /api/inpaint/start` with `mode=remote` writes job folder and returns `{ job_id }` immediately
- [ ] `pending/<job_id>/segment.mp4`, `mask.png`, `job.json` all present
- [ ] `GET /api/inpaint/colab_status` returns `{ online: false }` when Colab not running
- [ ] `GET /api/inpaint/colab_status` returns `{ online: true, gpu: "Tesla T4" }` when running
- [ ] Inpaint tab shows correct online/offline indicator
- [ ] Remote mode button disabled when Colab offline
- [ ] Real job submitted → Colab picks it up and processes end-to-end
- [ ] `progress.json` appears in `processing/<job_id>/` during run → UI progress bar updates
- [ ] `result.mp4` in `done/<job_id>/` → "Render with Inpainting" substitutes correctly
- [ ] Cancel while in `pending/` → folder deleted, no orphaned files
- [ ] Cancel while in `processing/` → `cancel.flag` written, Colab aborts, folder cleaned

---

### Operational notes — daily use

**Before an inpainting session:**
1. Start rclone bisync in a terminal (leave running)
2. Open Colab → Cell 1 → Cell 3 (worker loop)
3. Wait for green "● online" in Inpaint tab (up to 2 min for first heartbeat)

**If Colab disconnects mid-job:**
The job stays in `processing/` indefinitely. Manual recovery: delete
`D:\video-agent\output\inpainted\jobs\processing\<job_id>\`
then resubmit. rclone propagates the deletion to Drive.

**Future improvement (not in scope):**
Add stale-job timeout to `GET /api/inpaint/status` — if a job has been in
`processing/` for >60 min with no `progress.json` update, return
`{ status: "failed", error: "Colab session timed out" }` automatically.

---

## File change summary — Phase 4 only

### New files
| File | What it is |
|------|-----------|
| `colab/propainter_worker.ipynb` | Colab notebook — 4 cells |

### Modified files
| File | What changes |
|------|-------------|
| `scripts/inpaint_worker.py` | `--mode remote` branch: extract clip, write job folder, exit |
| `app.py` | `mode` on `/api/inpaint/start`; remote status reading; `GET /api/inpaint/colab_status` |
| `src/components/InpaintTab.tsx` | Mode selector; Colab indicator; "Waiting for Colab…" state |
| `src/hooks/usePipeline.ts` | `mode` param on `beginInpaint()` |
| `src/lib/api.ts` | `mode` on `StartInpaintRequest`; `getColabStatus()` |

### Files that do NOT change in Phase 4
Everything else. If you find yourself editing `analyze_and_edit.py`,
`SegmentCard.tsx`, `MainContent.tsx`, `PipelineContext.tsx`, `smart_crop.py`,
or `sam_helper.py` — stop and re-read this spec.