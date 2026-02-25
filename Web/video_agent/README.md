# Video Agent Web UI

This folder contains the React + Vite single-page app that powers the Video Agent dashboard. It talks to the Flask backend (`app.py`) for pipeline status, logs, template metadata, and rendering actions.

## Prerequisites

- Node 18+ (or Bun if you prefer, but all scripts use npm by default)
- The Video Agent backend running locally (defaults to `http://localhost:5100`)

## Quick Start

```bash
cd Web/video_agent
npm install          # first time only
npm run dev          # starts Vite on http://localhost:8080 with API proxying to 5100
```

For a production build served by Flask:

```bash
npm run build        # outputs dist/
# then from repo root
python -m app        # app.py will detect dist/ and serve it
```

## Key Files

- `src/pages/Index.tsx` – layout shell that wires the header, sidebar, and main panels
- `src/hooks/usePipeline.ts` – handles polling `/api/status`, SSE logs, and run/render actions
- `src/components/` – modular UI elements (header, sidebar, main content, etc.)
- `src/lib/api.ts` – typed helpers for REST calls

## Branding

Meta tags and share images now reference the local `public/logo.jpg` asset so the app displays the Video Agent branding everywhere (no third-party logos).
