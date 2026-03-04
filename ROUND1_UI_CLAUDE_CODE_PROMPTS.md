# VideoAgent — Round 1 UI Overhaul
## Claude Code Prompt Sequence

**Scope:** Frontend only. No backend changes. No new AI models.  
**Goal:** Transform the existing React UI from functional to cinematic professional tool.  
**Location:** D:\video-agent\Web\video_agent\  
**Build:** Vite + React + Tailwind  
**Research source:** videoagent_deep_research.md (Goal 3)

Work through these prompts in order. Run `npm run build` after each one.
Do not start the next prompt until the current one builds without errors.

---

## Context Block — Paste First Every Session

```
You are working on VideoAgent — a Flask/React video pipeline tool.

LOCAL: D:\video-agent (Windows development)
SERVER: /workspace/videoagent (Vast.ai Ubuntu, deploy when ready)
REPO: GitHub (commit after each completed prompt)

Frontend: Web/video_agent/src/ (React + Vite + Tailwind)
Backend: app.py (Flask, port 5100) — DO NOT TOUCH in this round
Build command: cd Web/video_agent && npm run build

Design direction (from research doc):
- Dark cinematic professional tool aesthetic
- Primary bg: #0f0f0f (near-black, not pure black)
- Panels: #1a1a1a, Elevated: #242424
- Accent: #e8a040 (deep amber — cinematic, not tech-startup)
- Text: #e8e8e8 primary, #888888 muted
- Status colors: emerald (accepted), blue (inpainted), amber (failed), zinc (pending)
- Fonts: Space Grotesk (headers), Inter (body), JetBrains Mono (timestamps/data)
- Subtle film grain texture on backgrounds
- No pure black, no purple gradients, no generic AI aesthetics

Current tabs: Log, Review, Output, Inpaint
Current pain points to fix:
- Failed inpaint segments lock up (can't redraw after failure)
- No timeline view — segments listed vertically only
- Segment thumbnails too small
- No drag-to-reorder
- Progress feedback minimal (no ETA)
- Output preview small with no fullscreen

everything-claude-code is installed — use /plan before major tasks.
Read CLAUDE.md and SESSIONS.md before starting.
```

---

## Prompt 1 — Design System Foundation

**What this does:** Installs shadcn/ui, configures the cinematic dark theme, sets up fonts, and establishes CSS variables. This is the foundation everything else builds on.

```
/plan

Task: Set up the VideoAgent design system foundation.

Read the current Web/video_agent/src/ structure first. 
Read Web/video_agent/package.json and tailwind.config.js (or tailwind.config.ts).
Report what Tailwind version is installed and whether PostCSS is configured.

Then do the following in order:

1. UPGRADE TAILWIND TO FULL POSTCSS SETUP (if not already)
   If Tailwind is using the CDN/play script, migrate to proper PostCSS:
   npm install -D tailwindcss postcss autoprefixer
   npx tailwindcss init -p
   Configure content paths to include src/**/*.{js,jsx,ts,tsx}

2. INSTALL SHADCN/UI
   npx shadcn-ui@latest init
   Choose: dark theme, zinc color base, CSS variables: yes
   Then add these components:
   npx shadcn-ui@latest add button dialog dropdown-menu tabs slider
   npx shadcn-ui@latest add scroll-area progress tooltip popover sheet badge

3. INSTALL FONTS
   In Web/video_agent/index.html, add to <head>:
   <link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;600;700&family=Inter:wght@400;500;600&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">

4. SET UP CSS VARIABLES
   In Web/video_agent/src/index.css (or globals.css), add:

   :root {
     --bg-primary: #0f0f0f;
     --bg-secondary: #1a1a1a;
     --bg-tertiary: #242424;
     --bg-elevated: #2a2a2a;
     --accent-primary: #e8a040;
     --accent-secondary: #d47830;
     --accent-muted: rgba(232, 160, 64, 0.15);
     --text-primary: #e8e8e8;
     --text-secondary: #888888;
     --text-muted: #555555;
     --border-subtle: rgba(255,255,255,0.06);
     --border-active: rgba(232,160,64,0.4);
     --status-accepted: #34d399;
     --status-inpainted: #60a5fa;
     --status-failed: #fbbf24;
     --status-pending: #52525b;
     --status-running: #a78bfa;
     --font-display: 'Space Grotesk', sans-serif;
     --font-body: 'Inter', sans-serif;
     --font-mono: 'JetBrains Mono', monospace;
   }

   body {
     background-color: var(--bg-primary);
     color: var(--text-primary);
     font-family: var(--font-body);
   }

   /* Film grain overlay */
   body::before {
     content: '';
     position: fixed;
     inset: 0;
     pointer-events: none;
     z-index: 9999;
     opacity: 0.025;
     background-image: url("data:image/svg+xml,%3Csvg viewBox='0 0 256 256' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='noise'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23noise)'/%3E%3C/svg%3E");
   }

5. UPDATE tailwind.config to extend theme with CSS variables:
   theme: {
     extend: {
       colors: {
         accent: 'var(--accent-primary)',
         'accent-secondary': 'var(--accent-secondary)',
         'bg-primary': 'var(--bg-primary)',
         'bg-secondary': 'var(--bg-secondary)',
         'bg-tertiary': 'var(--bg-tertiary)',
       },
       fontFamily: {
         display: ['Space Grotesk', 'sans-serif'],
         body: ['Inter', 'sans-serif'],
         mono: ['JetBrains Mono', 'monospace'],
       }
     }
   }

6. UPDATE App.jsx/tsx root element background to use var(--bg-primary)
   Remove any existing hardcoded background colors on root.

After all changes: npm run build
Report: what installed successfully, what (if anything) failed, build output.
Do NOT change any component logic or page content yet — design system only.
```

---

## Prompt 2 — App Shell & Navigation Redesign

**What this does:** Rebuilds the outer app shell — top bar, sidebar, tab navigation — with the cinematic aesthetic. No tab content changes yet.

```
/plan

Task: Redesign the VideoAgent app shell.

Read the current App.jsx/tsx and the main layout component.
Identify: how tabs are rendered, where the sidebar lives, where the top bar is.
Report the current structure before making any changes.

Then redesign the shell:

TOP BAR (full width, 48px height)
- Left: VideoAgent wordmark in Space Grotesk 600, with a small amber ⬡ icon before it
- Center: Pipeline status pill — shows current phase (Analyzing / Reviewing / Rendering / Done)
  with a pulsing amber dot when active, static gray dot when idle
- Right: Clips detected count badge | Logs count badge | GPU stats placeholder (just text "GPU: --" for now)
- Background: var(--bg-secondary) with 1px bottom border var(--border-subtle)
- Subtle box-shadow: 0 1px 0 var(--border-subtle)

LEFT SIDEBAR (220px wide, full height)
- Keep all existing controls (template selector, quality buttons, options toggles)
- Redesign visually:
  - Section labels in Space Grotesk 500, 11px, uppercase letter-spacing: 0.1em, color: var(--text-muted)
  - Controls use var(--bg-tertiary) backgrounds with var(--border-subtle) borders
  - Active/selected state uses var(--accent-muted) background with var(--accent-primary) left border (3px)
  - "Run Pipeline" button: full width, var(--accent-primary) background, Space Grotesk 600, 
    subtle glow effect: box-shadow: 0 0 20px rgba(232,160,64,0.3)
    Hover: slightly brighter, glow intensifies

TAB BAR (sits below top bar, above content)
- Horizontal tabs: Log | Review | Output | Inpaint
- Tab style: 
  - Inactive: text-secondary, no underline, transparent bg
  - Active: text-primary, 2px amber bottom border, Space Grotesk 500
  - Hover: text-primary transition 150ms
- Tabs sit flush left with content area
- Small badge on Log tab showing log count
- Small badge on Inpaint tab showing pending count

CONTENT AREA
- Background: var(--bg-primary)
- Padding: 24px
- Takes remaining width after sidebar

GENERAL RULES
- No rounded corners larger than 6px on structural elements (keep it tight, professional)
- All transitions: 150ms ease
- Scrollbars: thin, amber thumb on dark track (WebKit scrollbar styling)
- Use motion/react for tab content transitions: opacity 0→1, y 4→0, duration 150ms

Do not change what's inside any tab — only the shell/chrome around it.
npm run build after. Show me the diff.
```

---

## Prompt 3 — Review Tab: Segment Cards Redesign

**What this does:** Rebuilds the segment list in the Review tab with larger thumbnails, score breakdown, better status indicators, and the accept/reject flow redesigned.

```
/plan

Task: Redesign the Review tab segment cards.

Read the current Review tab component fully. 
Identify: how segments are listed, what data each card shows, how accept/reject works,
how the video preview works. Report structure before changes.

Redesign the segment cards:

SEGMENT CARD (replaces current card design)
Layout: horizontal card, full width
- Left: video thumbnail 160×90px (16:9), with a subtle play-on-hover overlay
  - Bottom-left corner: duration badge (e.g. "2.3s") in JetBrains Mono, 11px
  - Top-left corner: segment number badge (#1, #2...)
- Middle (flex-grow): 
  - Top row: filename + timestamp range in JetBrains Mono 12px text-muted
  - Second row: score bar cluster — 3 small horizontal bars side by side:
    Aesthetic | Motion | Audio — each 60px wide, 4px tall, 
    filled with amber gradient proportional to score (0-1),
    label above in 10px text-muted
  - Third row (if inpainted): blue badge "Inpainted · DiffuEraser" or "Inpainted · LaMa"
  - Third row (if failed): amber badge with failure reason truncated to 60 chars, 
    "Retry" button inline — this MUST remain clickable even after failure
- Right (80px): action buttons stacked vertically:
  - Accept ✓ (emerald) 
  - Reject ✗ (zinc, becomes red on hover)
  - Draw Region (only on Inpaint tab — skip here)

STATUS STATES — card left border (4px) indicates status:
- Accepted: emerald left border
- Rejected: transparent (dimmed card overall, opacity: 0.5)
- Pending: zinc left border
- Failed: amber left border — card is FULLY INTERACTIVE, not locked

SCORE BARS
- Use motion/react to animate bars filling on mount (0 → score, 600ms ease-out)
- Tooltip on hover showing exact score value

FAILED STATE FIX (critical)
- Currently failed segments lock up. Fix: ensure the "Retry" / "Draw Region" 
  buttons remain fully interactive regardless of status
- Render action buttons based on tab context, not segment status

SELECTION
- Clicking a card (not a button) expands an inline preview below it
- Preview: video player (full segment, autoplay, loop), larger at 480px wide
- Click again to collapse
- Use AnimatePresence from motion/react for smooth expand/collapse

EMPTY STATE
- If no segments: centered illustration (simple SVG camera icon in amber) 
  + "Run the pipeline to detect segments" in Space Grotesk

npm run build after. Show me what changed.
```

---

## Prompt 4 — Timeline View Component

**What this does:** Adds a horizontal timeline view above the segment cards in the Review tab. Segments shown as proportional blocks. Drag to reorder. Click to focus. No beat grid yet (that's Round 3).

```
/plan

Task: Build a horizontal timeline view for the Review tab.

Install dependencies first:
npm install @dnd-kit/core @dnd-kit/sortable @dnd-kit/modifiers @dnd-kit/utilities
npm install motion

Read the current Review tab component and segment data structure.
Understand what data is available per segment: file, start, end, duration, status, score.

Build a SegmentTimeline component at:
Web/video_agent/src/components/SegmentTimeline.tsx (or .jsx)

TIMELINE CONTAINER
- Full width, 120px height, horizontally scrollable
- Background: var(--bg-secondary)
- Border-bottom: 1px solid var(--border-subtle)
- Padding: 12px 16px
- Shows total duration label top-right: "Total: 34.2s" in JetBrains Mono

SEGMENT BLOCKS (inside SortableContext from dnd-kit)
- Each block width = (segment.duration / totalDuration) * containerWidth
- Minimum width: 40px (so very short clips are still visible)
- Height: 72px
- Gap between blocks: 3px
- Rounded: 4px corners

BLOCK CONTENT
- Background: color based on status:
  Accepted: rgba(52, 211, 153, 0.15) with emerald left border 2px
  Inpainted: rgba(96, 165, 250, 0.15) with blue left border 2px
  Failed: rgba(251, 191, 36, 0.15) with amber left border 2px
  Pending: var(--bg-tertiary) with zinc left border 2px
  Running: rgba(167, 139, 250, 0.15) with purple left border 2px, pulsing animation
- Top: segment number (#1) in 10px JetBrains Mono text-muted
- Middle: duration "2.3s" in 12px JetBrains Mono text-primary
- Bottom: file basename truncated, 10px text-muted
- For very narrow blocks (<60px): show only number

DRAG TO REORDER
- Use @dnd-kit/sortable with horizontalListSortingStrategy
- DragOverlay: semi-transparent copy of the block, amber border, slight rotation
- On drag end: call onReorder(newOrder) prop — parent updates segment order
- Use motion/react layout prop on each block for smooth position animation

ACTIVE/FOCUSED STATE
- Clicking a block calls onSelect(segmentIndex)
- Selected block: white border 1px, brighter background, no animation
- Scroll the corresponding segment card into view in the list below

BEAT GRID PLACEHOLDER
- Under the blocks, a 20px strip reserved for beat markers
- Currently shows: evenly spaced faint vertical lines every ~60px
- Label: "Beat sync — drop a music track to align cuts" in 10px text-muted
- This strip will be replaced by WaveSurfer in Round 3

INTEGRATION
- Add SegmentTimeline above the segment card list in Review tab
- Pass: segments array, onReorder callback, onSelect callback, selectedIndex
- onReorder should update the segment order in parent state
- When order changes in timeline, card list should reorder to match

npm run build after. 
Test: can you drag segment 3 to position 1? Does the card list reorder?
Show me the diff.
```

---

## Prompt 5 — Inpaint Tab Overhaul

**What this does:** Rebuilds the Inpaint tab — fixes the failed segment lockup bug, enlarges the canvas, adds the engine selector redesign, and improves progress feedback.

```
/plan

Task: Overhaul the Inpaint tab.

Install Fabric.js:
npm install fabric

Read the current InpaintTab component in full.
Read api.ts or wherever inpaint API calls are made.
Identify: how the mask canvas works, how engine selection works,
how job status is polled, what happens when a job fails.
Report the current structure before any changes.

FAILED SEGMENT BUG FIX (do this first, isolated)
Find where failed segments disable or hide the "Draw Region" button.
Remove any condition that hides/disables Draw Region based on failure status.
A failed segment must show: amber "Failed" badge + failure reason + "Draw Region" button + engine selector.
The user must be able to redraw and resubmit without reloading.
Test this fix in isolation before continuing.

ENGINE SELECTOR REDESIGN
Replace current buttons with a proper selector:
4 options in a 2×2 grid (or horizontal row):
Each option is a selectable card (not just a button):
- DiffuEraser (AI) — "~10 min/clip · Best quality · Generative reconstruction"
- LaMa (Fast) — "~2 min/clip · Good quality · Pattern-based fill"  
- LaMa + E2FGVI — "~5 min/clip · High quality · Temporal smoothing"
- ProPainter (Legacy) — "~25 min/clip · Slowest · Optical flow"

Selected card: var(--accent-muted) background, var(--accent-primary) border 1px, 
amber checkmark top-right.
Unselected: var(--bg-tertiary) background, subtle border.
Card title: Space Grotesk 500. Description: Inter 12px text-muted.

INPAINT CANVAS — REPLACE WITH FABRIC.JS
Replace the current canvas implementation with Fabric.js.

The canvas component (InpaintCanvas.jsx/tsx) should:
- Show the segment's video frame as background (extract first frame via API or use thumbnail)
- Fabric.js free drawing mode with:
  - Brush color: rgba(255, 80, 80, 0.65) (red overlay, clearly visible on dark footage)
  - Configurable brush size: slider 5px–80px, default 30px
  - Smooth line cap: round
- Toolbar below canvas:
  - Brush size slider (show current size in px, JetBrains Mono)
  - Undo button (⌘Z / Ctrl+Z keyboard shortcut too)
  - Clear All button (with confirmation)
  - Before/After toggle: show original frame or frame+mask overlay
- Export: white-on-black PNG mask for the API call
- Canvas dimensions: scale to fit container width while maintaining video aspect ratio
- CRITICAL: always call canvas.dispose() on unmount

PROGRESS FEEDBACK IMPROVEMENT
When a job is running, show:
- Phase label: "Extracting frames..." / "Propagating masks..." / "Inpainting..." 
  (parsed from status.status field)
- Progress bar with percentage
- Frame counter: "156 / 240 frames" 
- Estimated time: if progress > 10% and elapsed time known, show "~3 min remaining"
  (calculated as: elapsed / progress * (1 - progress))
- Elapsed time counter updating live: "2:34 elapsed"
- All in JetBrains Mono, compact layout

SEGMENT LIST IN INPAINT TAB
Same card design as Review tab (from Prompt 3) but:
- Right side shows engine selector inline (collapsed to icon row when not selected)
- Shows inpaint result thumbnail if job is done (small preview next to original)
- "Render with inpainting" button at bottom: 
  Only shows count of DONE jobs, e.g. "Render with inpainting (7 clips)"
  Disabled if 0 done jobs

npm run build after. 
Specifically verify: can you click Draw Region on a previously-failed segment?
Show me the diff.
```

---

## Prompt 6 — Output Tab & Video Preview

**What this does:** Rebuilds the Output tab with a proper video preview, download button, and render progress.

```
/plan

Task: Redesign the Output tab.

Read the current Output tab component in full.
Identify: how the final video is previewed, how download works, 
what render progress looks like. Report before changes.

OUTPUT TAB LAYOUT

RENDER PROGRESS SECTION (shows when rendering is active)
- Full-width progress bar, amber fill, animated shimmer during active render
- Phase: "Scoring clips..." / "Selecting segments..." / "Rendering..." / "Complete"
- Percentage + elapsed time
- Log preview: last 3 log lines in JetBrains Mono 11px text-muted, 
  auto-scrolling, below progress bar

VIDEO PREVIEW SECTION (shows when output exists)
- Video player: 16:9 aspect ratio, full container width (max 960px)
- Custom controls bar below video (not browser default controls):
  - Play/pause button (amber icon)
  - Scrub bar: thin amber progress line, clickable
  - Current time / Total time in JetBrains Mono
  - Volume: small slider
  - Fullscreen button
- Below player:
  - File info: filename, file size, duration, resolution — in JetBrains Mono 12px text-muted
  - Download button: prominent, Space Grotesk 600, amber accent
    "↓ Download · event_concert_compilation.mp4 · 35 MB"

EMPTY STATE (no output yet)
- Centered: large "▷" icon in amber, Space Grotesk text "No output yet"
- "Run the pipeline to generate your reel" subtext
- Subtle pulsing animation on the icon

RENDER HISTORY (if multiple outputs exist)
- Below main preview: "Previous renders" collapsible section
- List of previous output files with timestamp, size, duration
- Click to load into main preview

npm run build after. Show me the diff.
```

---

## Prompt 7 — Log Tab Redesign

**What this does:** Rebuilds the Log tab to look like a proper terminal/console — color-coded, searchable, with phase markers.

```
/plan

Task: Redesign the Log tab.

Read the current Log tab component.
Understand: how logs stream in, what data format they use, how they're displayed.

Redesign as a proper terminal-style log viewer:

LOG CONTAINER
- Background: #0a0a0a (slightly darker than app bg)
- Font: JetBrains Mono 12px
- Full height of content area, auto-scrolling (scroll to bottom on new entries)
- Thin scrollbar, amber thumb

LOG LINE FORMAT
[HH:MM:SS] [PHASE] message

COLOR CODING by prefix/level:
- INFO / general: text-secondary (#888888)
- SUCCESS / done: var(--status-accepted) emerald
- WARNING: var(--status-failed) amber  
- ERROR: #f87171 red
- Phase markers (===): var(--accent-primary) amber, bold, with horizontal rule
- File paths / numbers: var(--status-inpainted) blue
- Model names / technical terms: var(--text-primary) white

PHASE MARKERS
When a new pipeline phase starts, insert a visual separator:
─────────────── SCORING CLIPS ───────────────
in amber, centered, with the phase name

LOG TOOLBAR (top of log area)
- Left: "● Live" indicator (pulsing dot + text when pipeline running, gray when idle)
- Center: search input — filter log lines in real-time
- Right: "Clear" button | "Copy all" button | auto-scroll toggle

AUTO-SCROLL
- Enabled by default
- If user scrolls up: auto-scroll pauses, shows "↓ New entries" button at bottom
- Clicking "↓ New entries" resumes auto-scroll

STATS BAR (below log, 32px)
- Shows: Total lines | Errors (red count) | Warnings (amber count) | Start time
- All in JetBrains Mono 11px

npm run build after. Show me the diff.
```

---

## Prompt 8 — Motion, Polish & Final Details

**What this does:** Adds the micro-animations, transitions, and final polish passes across the whole UI.

```
/plan

Task: Add motion and polish to VideoAgent UI.

Ensure motion is installed: npm install motion
Read all components touched in Prompts 1-7.

Add the following in order:

1. TAB TRANSITIONS
   Wrap each tab's content in:
   <motion.div
     key={activeTab}
     initial={{ opacity: 0, y: 6 }}
     animate={{ opacity: 1, y: 0 }}
     exit={{ opacity: 0 }}
     transition={{ duration: 0.15 }}
   >
   Use AnimatePresence around the tab content switcher.

2. SEGMENT CARD MOUNT ANIMATION
   Each segment card on initial render:
   initial={{ opacity: 0, x: -8 }}
   animate={{ opacity: 1, x: 0 }}
   transition={{ delay: index * 0.04, duration: 0.2 }}
   (stagger by index — feels like cards loading in sequence)

3. STATUS CHANGE ANIMATION
   When a segment's status changes (pending → running → done/failed):
   Flash the status indicator with a brief amber pulse, then settle to final color.
   Use motion keyframes:
   animate={{ opacity: [1, 0.5, 1] }} transition={{ duration: 0.4 }}

4. PROGRESS BAR
   All progress bars: use motion div with width transition, not CSS transition.
   Animated shimmer on active bars:
   CSS animation: shimmer 1.5s infinite — gradient sweep left to right.

5. JOB COMPLETION
   When render completes, show a brief full-width amber banner:
   "✓ Render complete — event_concert_compilation.mp4"
   AnimatePresence: slides down from top, holds 3 seconds, slides back up.

6. SCORE BAR ANIMATION (in segment cards)
   On mount, score bars animate from 0 to their value:
   width: 0 → scoreValue%
   duration: 0.6s ease-out
   stagger the 3 bars with 0.1s delay each

7. SCROLLBAR STYLING (global)
   Add to index.css:
   ::-webkit-scrollbar { width: 6px; height: 6px; }
   ::-webkit-scrollbar-track { background: var(--bg-secondary); }
   ::-webkit-scrollbar-thumb { background: rgba(232,160,64,0.3); border-radius: 3px; }
   ::-webkit-scrollbar-thumb:hover { background: rgba(232,160,64,0.6); }

8. FOCUS STATES
   All interactive elements: outline: 2px solid var(--accent-primary); outline-offset: 2px;
   on :focus-visible only (not on mouse click)

9. HOVER MICRO-INTERACTIONS (CSS only)
   Buttons: brightness(1.1) on hover, 100ms transition
   Cards: translateY(-1px) on hover, subtle shadow increase, 100ms transition
   Thumbnail: scale(1.02) on hover within its container (overflow hidden)

10. FINAL CONSISTENCY PASS
    - Check all hardcoded colors — replace with CSS variables where appropriate
    - Ensure Space Grotesk is used for all headings, Inter for all body text
    - Ensure JetBrains Mono for all timestamps, file sizes, technical data
    - Remove any remaining default browser button/input styling

npm run build after.
Run: grep -r "bg-white\|bg-gray\|text-black\|#ffffff\|#000000" src/ 
     to catch any non-design-system colors. Fix what you find.

Commit message: "feat: Round 1 UI overhaul — cinematic dark theme, timeline, Fabric.js canvas, motion"
git add -A && git commit -m "feat: Round 1 UI overhaul — cinematic dark theme, timeline, Fabric.js canvas, motion"
git push origin main
```

---

## After All Prompts — Sync to Server

```bash
cd /workspace/videoagent
git pull origin main
cd Web/video_agent && npm install && npm run build
cd ../..
source .venv/bin/activate
python3 app.py
```

Open tunnel and verify in browser:
```powershell
ssh -p <PORT> -L 5100:localhost:5100 root@<IP> -N
```

---

## Quick Reference — What Each Prompt Changes

| Prompt | Files Changed | Risk |
|--------|--------------|------|
| 1 — Design system | index.css, tailwind.config, package.json | Low |
| 2 — App shell | App.jsx, layout components | Medium |
| 3 — Segment cards | ReviewTab.jsx, SegmentCard.jsx | Medium |
| 4 — Timeline | NEW: SegmentTimeline.jsx, ReviewTab.jsx | Medium |
| 5 — Inpaint tab | InpaintTab.jsx, NEW: InpaintCanvas.jsx | High |
| 6 — Output tab | OutputTab.jsx | Low |
| 7 — Log tab | LogTab.jsx | Low |
| 8 — Motion/polish | All components (additive only) | Low |

**If any prompt breaks the build:** Roll back that file with `git checkout -- <file>` and report the error. Do not proceed to the next prompt until the build is clean.
