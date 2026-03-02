# VideoAgent — Claude Code Project Guide

## Project Overview

**VideoAgent** is a local AI-powered video reel compiler. It takes a folder of raw 4K portrait clips (performance/event footage), runs an AI pipeline to score and select the best segments, then lets you review, trim, colour grade, and render a final compilation — all from a web UI.

**Primary use case:** Social media reels (TikTok / Instagram Reels / YouTube Shorts) — vertical format, ~30s output.

---

## Architecture

```
video_agent/
├── src/                          # React frontend (this repo)
│   ├── context/
│   │   └── PipelineContext.tsx   # ALL shared state — read this first
│   ├── components/
│   │   ├── DashboardHeader.tsx   # Live status pill + elapsed timer
│   │   ├── PipelineSidebar.tsx   # Pipeline config + Run button
│   │   ├── MainContent.tsx       # Tab container: Log / Review / Output / Inpaint
│   │   ├── SegmentCard.tsx       # Inline-expand card: video + trim + grade + crop + SAM
│   │   └── InpaintTab.tsx        # ProPainter inpaint tab (skippable)
│   ├── hooks/
│   │   └── usePipeline.ts        # Pipeline state hook (crop, SAM, inpaint, transitions)
│   ├── lib/
│   │   └── api.ts                # Typed fetch wrappers for all Flask endpoints
│   └── pages/
│       └── Index.tsx             # Root — wraps everything in PipelineProvider
│
└── [Python backend]
    ├── app.py                    # Flask server
    ├── scripts/
    │   ├── analyze_and_edit.py   # Main pipeline entry point + ffmpeg render
    │   ├── editing_brain.py      # Narrative-aware segment selection
    │   ├── transitions.py        # Transition catalog (14 types, Phase 1+2)
    │   ├── llm_planner.py        # LM Studio LLM reordering (advisory only)
    │   ├── semantic_siglip.py    # SigLIP + LAION aesthetic enrichment
    │   ├── smart_crop.py         # Subject-tracking 9:16 crop computation
    │   ├── sam_helper.py         # SAM ViT-B point-prompt mask generation
    │   └── inpaint_worker.py     # ProPainter subprocess wrapper
    ├── raw_clips/                # Source video files
    ├── style_profiles/           # Template JSON files
    └── ProPainter/               # Installed at D:\video-agent\ProPainter\
```

---

## Tech Stack

| Layer | Tech |
|-------|------|
| Framework | React 18 + TypeScript |
| Build | Vite |
| Styling | Tailwind CSS v3 + shadcn/ui |
| Components | Radix UI primitives |
| State | React Context (`PipelineContext`) + `usePipeline.ts` hook |
| Routing | React Router v6 |
| Icons | Lucide React |
| Backend | Python Flask (separate process) |

---

## Design System

Dark navy + lime green palette. Always use CSS variables — never hardcode colours.

### Key CSS variables (`src/index.css`)
```
--background       dark navy base
--foreground       near-white text
--primary          lime green (hsl 82 80% 52%)
--primary-foreground  dark text on primary buttons
--muted-foreground  grey secondary text
--destructive      red (reject/error)
--border           subtle border
```

### Utility classes
```
glass-surface      frosted glass panel
glass-card         lighter frosted card
surface-elevated   raised surface
surface-sunken     recessed input
glow-primary       green glow shadow
gradient-primary-btn  lime green gradient
text-gradient      lime gradient text
```

### Fonts
- **Body:** `Outfit` — headings, labels, UI text
- **Mono:** `JetBrains Mono` — file paths, timecodes, log output

---

## State Management

Read `PipelineContext.tsx` and `usePipeline.ts` before touching any component.

### Key state
```typescript
phase: PipelinePhase        // idle | running | reviewing | rendering | done | error
segments: Segment[]
log: LogLine[]
params: PipelineParams
activeTab: "Log"|"Review"|"Output"|"Inpaint"
elapsed: number
outputPath: string | null

// Per-segment overrides (keyed by segment index)
trimData: Record<number, TrimSettings>
gradeData: Record<number, GradeSettings>
transitionData: Record<number, string>
cropData: Record<number, CropSettings>
samData: Record<number, SamMaskSettings>
inpaintJobs: Record<number, InpaintJob>
```

---

## Flask API Contract

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/run` | POST | Start pipeline |
| `/api/stream` | GET | SSE log stream |
| `/api/status` | GET | Phase + segment counts |
| `/api/review` | POST | Trigger render |
| `/api/templates` | GET | Template names |
| `/api/clips` | GET | Raw clips list |
| `/video/<path>` | GET | Stream video — **HTTP 206 required** |
| `/api/crop_auto` | POST | Auto-detect 9:16 crop region |
| `/api/sam_mask` | POST | SAM point-prompt mask (~5–30s) |
| `/api/inpaint/start` | POST | Start ProPainter job (non-blocking) |
| `/api/inpaint/status/<id>` | GET | Poll job progress |
| `/api/inpaint/cancel/<id>` | POST | Kill job + cleanup |

---

## Segment Data Shape

```typescript
interface Segment {
  id: string;
  video_path: string;
  start: number;
  end: number;
  style_score: number;
  buffer: boolean;
  tags: string[];
  narrative_position?: "opener" | "middle" | "closer";
  transition_in?: string;
  status: "pending" | "accepted" | "rejected";
  trimStart: number;
  trimEnd: number;
  grade: GradeSettings;
  crop?: CropSettings;
  sam_mask?: SamMaskSettings;
}

interface GradeSettings {
  brightness: number;   // -50 to +50
  contrast: number;
  saturation: number;
  vibrance: number;
  temp: number;
  lut: string;
}

interface CropSettings {
  x: number;
  y: number;            // always 0
  w: number;            // source_height * 9/16, even number
  h: number;
  auto: boolean;
}

interface SamMaskSettings {
  point_x: number;      // 0.0–1.0
  point_y: number;      // 0.0–1.0
  mask_b64: string;
  ready: boolean;
  enabled: boolean;     // user toggle
  timestamp: number;
}
```

---

## Transition Types (14 total)

Defined in `scripts/transitions.py`. Auto-assigned by `editing_brain.py`.

**Phase 1 (insert clips):** `cut`, `jump_cut`, `flash_white`, `flash_black`, `dip_black`, `dip_white`

**Phase 2 (xfade):** `dissolve`, `fade_black`, `wipe_up`, `wipe_left`, `zoom_in`, `zoom_out`, `push_up`, `slide_left`

---

## ProPainter Integration

Installed path is configurable via `PROPAINTER_DIR` env var.
Linux default: `/workspace/ProPainter`
Windows example: `set PROPAINTER_DIR=D:\video-agent\ProPainter`

Critical runtime notes:
- `--cpu` flag does NOT exist — omit it
- `--mask` takes a **single PNG** not a directory
- Run at `--width 640` (CPU time ~20–30 min per 3s segment)
- `output_path` in job status must be a `.mp4` file path, not a directory
- When substituting inpainted clips: reset `start=0, end=<inpainted_duration>`

---

## Key UX Rules

- Proxy is the **default quality** — never change this default
- Segment previews are **inline expand only** — never modal
- Crop tool hidden if source is already 9:16 or narrower
- Inpaint tab disabled until `phase === "done"`, always has "Skip Inpaint →" visible
- `/api/sam_mask` needs 30s proxy timeout in `vite.config.ts`

### Segment card visual states
```
border-l-primary/70    accepted
border-l-destructive   rejected (opacity-40)
border-l-blue-500/50   buffer
ring-1 ring-primary/30 keyboard focused
```

---

## What NOT to Do

- Don't bypass `PipelineContext` / `usePipeline.ts`
- Don't hardcode colours — use design tokens
- Don't add new UI libraries
- Don't put grade/trim/crop preview logic in Flask
- Don't run multiple phases simultaneously
- Don't touch "do not change" files from FEATURES.md without explicit instruction

---

## Common Tasks

### Add a pipeline sidebar option
1. Add field to `PipelineParams` in `PipelineContext.tsx`
2. Add default in `useState`
3. Add control in `PipelineSidebar.tsx`
4. Include in `runPipeline()` fetch body

### Add a LUT preset
1. `SegmentCard.tsx` — add to `LUTS` array + `LUT_CSS` map
2. `analyze_and_edit.py` — add to `LUT_FFMPEG_FILTERS`

### Add a transition type
1. `transitions.py` — add to `ALL_TRANSITIONS`, correct phase frozenset, `XFADE_MAP` if Phase 2
2. `SegmentCard.tsx` — add to `TRANSITION_OPTIONS`

### Add a tab
1. `MainContent.tsx` — add to `TABS` array
2. Create panel component
3. Add `activeTab === "X" && <XPanel />` in content section
4. Update `activeTab` type union in `PipelineContext.tsx`

---

## Session Memory Protocol

This project uses `SESSIONS.md` as persistent memory across VS Code sessions.

### START of every session — paste this prompt:
```
Read CLAUDE.md, then read the last entry in SESSIONS.md.
Summarise the current state of the project in 3 sentences,
list any known broken items, and tell me what we should
work on first today.
```

### END of every session — paste this prompt:
```
Update SESSIONS.md with a new entry for this session.
Follow the format in the file exactly. Be specific about
file names and line numbers. Make "Next session starts with"
a single concrete command or action, not a vague goal.
```

### If something breaks unexpectedly:
```
Before fixing this, add a "Broken" note to the current
SESSIONS.md entry with the error, the file, and the line
number if known.
```

Never skip the end-of-session update — even a "nothing worked" entry
is more valuable than a gap in the log.

---

## Portability Rules

VideoAgent runs on Windows (dev) and Ubuntu/Vast.ai (deploy). Follow these rules to keep it portable:

1. **All file paths via env vars** — use `os.environ.get("VAR", "/workspace/default")`. Never hardcode `D:\`, `C:\`, or `/workspace/...` in source code. Linux `/workspace/...` paths are the defaults; Windows users override via `.env`.

2. **`.env.example` is the canonical reference** — every env var the code reads must have an entry there with a comment explaining its purpose. Update it whenever you add a new `os.environ.get()` call.

3. **Never commit `.env` or model weights** — `.env` is in `.gitignore`. So are `style/*.pth` and `style/*.pt`. The `output/`, `raw_clips/`, and `logs/` directories are also excluded.

4. **`deploy_vastai.sh` is the single setup command** — for any new server, `git clone` the repo then `bash deploy_vastai.sh`. The script is idempotent (safe to re-run) and prints PASS/FAIL for each of 12 steps.

5. **New external tool = new env var** — when adding a new AI tool (model, external repo, binary), always add: (a) an `os.environ.get()` in the Python script, (b) an entry in `.env.example`, and (c) a step in `deploy_vastai.sh`.