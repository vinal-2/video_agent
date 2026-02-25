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
│   │   ├── MainContent.tsx       # Tab container: Log / Review / Output
│   │   └── SegmentCard.tsx       # Inline-expand card: video + trim + grade
│   └── pages/
│       └── Index.tsx             # Root — wraps everything in PipelineProvider
│
└── [Python backend — separate repo/folder]
    ├── app.py                    # Flask server
    ├── pipeline/                 # AI scoring, segmentation, ffmpeg render
    └── raw_clips/                # Source video files
```

---

## Tech Stack

| Layer | Tech |
|-------|------|
| Framework | React 18 + TypeScript |
| Build | Vite |
| Styling | Tailwind CSS v3 + shadcn/ui |
| Components | Radix UI primitives (all already installed) |
| State | React Context (`PipelineContext`) — no Redux/Zustand |
| Routing | React Router v6 |
| Icons | Lucide React |
| Backend | Python Flask (separate process, not in this repo) |

---

## Design System

The design uses a **dark navy + lime green** palette. Always use existing CSS variables and utility classes — never hardcode colours.

### Key CSS variables (defined in `src/index.css`)
```
--background       dark navy base
--foreground       near-white text
--primary          lime green (#9ef060 approx, hsl 82 80% 52%)
--primary-foreground  dark (text on primary buttons)
--muted-foreground  grey secondary text
--destructive      red (for reject/error states)
--border           subtle border colour
--status-online    green dot
--status-idle      amber dot
```

### Utility classes (use these, don't reinvent them)
```
glass-surface      frosted glass panel background
glass-card         lighter frosted card
surface-elevated   raised surface with subtle gradient
surface-sunken     recessed input background
glow-primary       green glow shadow
glow-primary-strong  stronger green glow (for Run button)
gradient-primary-btn  lime green gradient background
text-gradient      lime gradient text
dot-grid           background dot pattern
scanline           subtle scanline overlay
```

### Fonts
- **Body:** `Outfit` (sans-serif) — headings, labels, UI text
- **Monospace:** `JetBrains Mono` — file paths, timecodes, log output, numeric values

---

## State Management

**Everything goes through `PipelineContext`.** Read `src/context/PipelineContext.tsx` before touching any component.

### Key state
```typescript
phase: PipelinePhase        // idle | running | reviewing | rendering | done | error
segments: Segment[]         // AI-selected clips, mutable (status, trimStart/End, grade)
log: LogLine[]              // streaming log output
params: PipelineParams      // sidebar config (template, quality, toggles)
activeTab: "Log"|"Review"|"Output"
elapsed: number             // seconds since pipeline started
outputPath: string | null   // final rendered video path
```

### Updating segments
```typescript
// Always use updateSegment — never mutate directly
const { updateSegment } = usePipeline();
updateSegment(seg.id, { status: "accepted" });
updateSegment(seg.id, { trimStart: 12.5, trimEnd: 18.0 });
updateSegment(seg.id, { grade: { ...seg.grade, brightness: 10 } });
```

---

## Flask API Contract

The frontend talks to a Flask backend. All endpoints are relative (proxied via Vite in dev).

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/run` | POST | Start pipeline. Body: `{ template, quality, buffer, llm, vision, vision_max, disable_cache }` |
| `/api/stream` | GET | SSE stream of log lines. Each event is JSON `{ text: string }` or `{ ping: true }` |
| `/api/status` | GET | Returns `{ phase, running, selected_segments?, output_path? }` |
| `/api/review` | POST | Trigger final render. Body: `{ segments: Segment[], params }` |
| `/api/templates` | GET | Returns `string[]` of available template names |
| `/api/clips` | GET | Returns list of clips in raw_clips/ |
| `/video/<path>` | GET | Stream video file with **HTTP Range Request support** (required for seeking) |

### Dev mode (no backend)
`PipelineContext` has a built-in simulation. If `/api/run` fails, it falls back to `simulatePipeline()` which streams fake log lines and loads mock segments after ~5 seconds. Use this to develop UI without the Python backend running.

---

## Segment Data Shape

```typescript
interface Segment {
  id: string;
  video_path: string;       // full path or filename
  start: number;            // original clip start (seconds)
  end: number;              // original clip end (seconds)
  style_score: number;      // 0–1 AI similarity score
  buffer: boolean;          // true = buffer segment (lower priority)
  tags: string[];           // AI vision tags e.g. ["motion", "performer"]
  // mutable:
  status: "pending" | "accepted" | "rejected";
  trimStart: number;        // user-adjusted trim point
  trimEnd: number;
  grade: GradeSettings;     // per-segment colour grade
}

interface GradeSettings {
  brightness: number;   // -50 to +50
  contrast: number;
  saturation: number;
  vibrance: number;
  temp: number;         // colour temperature (warm/cool)
  lut: string;          // "none" | "cinema" | "golden" | "cool" | "fade" | "punch" | "mono" | "teal_org"
}
```

---

## Key UX Behaviours

### Review tab keyboard shortcuts
| Key | Action |
|-----|--------|
| `A` | Accept focused segment |
| `R` | Reject focused segment (auto-advances) |
| `Enter` | Expand / collapse card |
| `Space` | Play / pause video in expanded card |
| `↑ ↓` | Navigate between segments |
| `Escape` | Collapse expanded card |

### Segment card inline expand
- Click card header → expands **below** the card (Notion-style, not modal)
- Only one card open at a time
- Left side: video player + transport controls + trim rail
- Right side: colour grade sliders + LUT preset grid
- Trim handles snap to **0.5s** intervals
- Grade sliders apply **CSS filters** to the video element as live preview
- Real grade values are sent to Flask at render time for ffmpeg processing

### Segment card visual states
```
border-l-primary/70    accepted (green left border)
border-l-destructive   rejected (red left border, opacity-40)
border-l-blue-500/50   buffer segment
ring-1 ring-primary/30 keyboard focused
```

### Quality defaults
**Proxy is the default quality** — optimised for fast iteration. User explicitly upgrades to High/4K when ready for final render.

---

## Vite Dev Proxy

To avoid CORS issues during development, add this to `vite.config.ts`:

```typescript
export default defineConfig({
  // ...existing config
  server: {
    proxy: {
      '/api': 'http://localhost:5000',
      '/video': 'http://localhost:5000',
    }
  }
});
```

---

## Common Tasks

### Add a new pipeline option to the sidebar
1. Add field to `PipelineParams` interface in `PipelineContext.tsx`
2. Add default value in the `useState` initialiser
3. Add UI control in `PipelineSidebar.tsx` using `update({ newField: value })`
4. Include in the fetch body inside `runPipeline()`

### Add a new LUT preset
1. Add entry to `LUTS` array in `SegmentCard.tsx`
2. Add CSS filter string to `LUT_CSS` map
3. Add ffmpeg filter equivalent in Flask's render route

### Add a new tab
1. Add to `TABS` array in `MainContent.tsx`
2. Add panel component
3. Add `activeTab === "NewTab" && <NewPanel />` in the content section

### Change accent colour
Edit the `--primary` and `--ring` HSL values in `src/index.css`. The gradient utilities reference these variables so everything updates consistently.

---

## Flask Range Request (Critical)

The `/video/<path>` route **must** implement HTTP 206 Partial Content responses. Without this, the browser video element cannot seek and trim handles break silently.

```python
@app.route("/video/<path:filepath>")
def serve_video(filepath):
    # Parse Range header → return 206 with Content-Range
    # See app.py for full implementation
```

---

## What NOT to Do

- **Don't** bypass `PipelineContext` — no component-level fetch calls for pipeline data
- **Don't** use `inline styles` for colours — use Tailwind classes with the design tokens
- **Don't** add new UI libraries — Radix + shadcn + lucide covers everything needed
- **Don't** put grade/trim logic in Flask — preview is CSS filters client-side, render uses the values server-side
- **Don't** default quality to anything other than Proxy — fast iteration is the priority
- **Don't** open segment previews in a modal — inline expand only (Notion-style)
