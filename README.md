# VideoAgent

> Local AI-powered video reel compiler for social media content

VideoAgent takes a folder of raw portrait clips, runs an AI pipeline to score and select the best segments, then lets you review, trim, colour grade, and render a final compilation — all from a browser UI running entirely on your laptop.

![Stack](https://img.shields.io/badge/React-18-61DAFB?style=flat-square&logo=react) ![TypeScript](https://img.shields.io/badge/TypeScript-5-3178C6?style=flat-square&logo=typescript) ![Vite](https://img.shields.io/badge/Vite-5-646CFF?style=flat-square&logo=vite) ![Python](https://img.shields.io/badge/Python-Flask-000000?style=flat-square&logo=flask)

---

## What it does

1. **Analyse** — scans your raw clips for motion quality, blur, and scene boundaries
2. **Score** — uses SigLIP visual similarity + LAION aesthetic scoring against a style template
3. **Plan** — LLM selects the best ~30s of segments across all clips
4. **Review** — you preview each clip, accept/reject, trim handles, and apply a colour grade
5. **Render** — ffmpeg compiles the final reel with your edits baked in

---

## Architecture

```
video_agent/          ← this repo (React frontend)
├── src/
│   ├── context/      ← PipelineContext: all shared state
│   ├── components/   ← DashboardHeader, PipelineSidebar, MainContent, SegmentCard
│   └── pages/        ← Index (entry point)
│
python_backend/       ← Flask API + AI pipeline (separate)
├── app.py
├── pipeline/
└── raw_clips/        ← drop your footage here
```

The React frontend talks to the Flask backend over a local HTTP API. Both run on `localhost` — nothing leaves your machine.

---

## Prerequisites

**Frontend**
- Node.js 18+ or Bun

**Backend**
- Python 3.10+
- ffmpeg (must be on PATH)
- CUDA-capable GPU recommended (Nvidia Quadro M1200 or better)

---

## Getting started

### 1. Clone and install

```bash
git clone https://github.com/vinal-2/video_agent.git
cd video_agent
bun install        # or: npm install
```

### 2. Start the Python backend

```bash
cd ../python_backend   # wherever your Flask app lives
pip install -r requirements.txt
python app.py
# → running on http://localhost:5000
```

### 3. Configure the Vite proxy

In `vite.config.ts`, add:

```ts
server: {
  proxy: {
    '/api':   'http://localhost:5000',
    '/video': 'http://localhost:5000',
  }
}
```

### 4. Start the dev server

```bash
bun run dev
# → http://localhost:5173
```

---

## Usage

### Running a pipeline

1. **Select a template** — style profile the AI scores against (Travel Reel, Social Clip, etc.)
2. **Pick quality** — start with **Proxy** for fast iteration; upgrade to High/4K for final renders
3. **Toggle options** — LLM Planner (GPT-4 edit planning), Vision Tagger (moondream captions), Clear Cache
4. **Click Run Pipeline** — watch the log stream in real time

### Reviewing segments

After the pipeline completes, the **Review** tab opens automatically.

| Key | Action |
|-----|--------|
| `A` | Accept focused segment |
| `R` | Reject (auto-advances to next) |
| `Enter` | Expand/collapse clip preview |
| `Space` | Play/pause video |
| `↑ ↓` | Navigate between segments |
| `Escape` | Collapse expanded card |

Click any segment to expand it inline and access:

- **Video preview** with transport controls
- **Trim handles** — drag to set in/out points (0.5s snap)
- **Colour grade** — Brightness, Contrast, Saturation, Vibrance, Temperature sliders
- **LUT presets** — Cinema, Golden Hour, Cool Blue, Faded, Punchy, Mono, Teal+Orange

### Rendering

Click **Render →** once you're happy with your selection. The final video appears in the **Output** tab with a download button.

---

## Flask API

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/run` | POST | Start the pipeline |
| `/api/stream` | GET | SSE log stream |
| `/api/status` | GET | Current phase + selected segments |
| `/api/review` | POST | Trigger final render with trim/grade data |
| `/api/templates` | GET | Available style templates |
| `/api/clips` | GET | Clips in raw_clips/ |
| `/video/<path>` | GET | Video streaming with Range Request support |

The `/video/<path>` route **must** return HTTP 206 Partial Content responses — this is what allows the browser to seek in the preview player. See `app.py` for the implementation.

---

## Development without a backend

`PipelineContext` has a built-in simulation mode. If `/api/run` fails (no backend running), it automatically falls back to a mock pipeline that streams fake log lines and loads sample segments after ~5 seconds. This lets you work on the UI independently.

---

## Project structure

```
src/
├── context/
│   └── PipelineContext.tsx     # Shared state, API calls, simulation
├── components/
│   ├── DashboardHeader.tsx     # Logo + live status pill + elapsed timer
│   ├── PipelineSidebar.tsx     # Config panel + Run button
│   ├── MainContent.tsx         # Log / Review / Output tabs
│   └── SegmentCard.tsx         # Inline-expand card with video + trim + grade
├── pages/
│   ├── Index.tsx               # Root page (wraps PipelineProvider)
│   └── NotFound.tsx
├── index.css                   # Design tokens + utility classes
└── App.tsx                     # Router setup
```

---

## Tech stack

- **React 18** + **TypeScript 5**
- **Vite 5** — dev server + build
- **Tailwind CSS v3** — utility-first styling
- **shadcn/ui** — Radix UI component primitives
- **Lucide React** — icons
- **TanStack Query** — available for future data fetching
- **React Router v6** — routing

---

## Design system

The UI uses a **dark navy + lime green** palette. All colours are CSS variables in `src/index.css` — never hardcode hex values in components.

Key utility classes: `glass-surface`, `glass-card`, `surface-elevated`, `gradient-primary-btn`, `glow-primary`, `text-gradient`

Fonts: **Outfit** (UI text) + **JetBrains Mono** (data, timecodes, log output)

---

## Contributing / extending

See [`CLAUDE.md`](./CLAUDE.md) for a full developer guide including how to add new LUT presets, pipeline options, tabs, and the do/don't rules for this codebase.

---

## License

Private project — not for redistribution.
