# Windows → Linux Path Audit

**Migration:** `D:\video-agent\` (Windows) → `/workspace/videoagent/` (Vast.ai RTX 3090 Linux)
**ProPainter location:** `/workspace/ProPainter/` — NOT inside the videoagent folder

---

## Legend

| Symbol | Meaning |
|--------|---------|
| BUG    | Code that will actively fail on Linux — fix required |
| DOCS   | Windows path in comment/docstring only — no code fix needed |
| INFO   | Platform-dependent but guarded by env var — needs operator action |
| OK     | No path issues |
| N/A    | Not imported by production; standalone utility |

---

## Python — backend scripts

### `app.py`

| Line | Severity | Issue | Fix |
|------|----------|-------|-----|
| 3 | DOCS | Docstring: `D:\video-agent\` | — |
| 24–25 | DOCS | Comments: `D:\video-agent\` | — |
| 123 | **BUG** | `BASE_DIR.drive` — `Path.drive` returns `""` on Linux (Windows-only attribute). Display is wrong; `shutil.disk_usage` still works. | Replace `{BASE_DIR.drive}` with `{BASE_DIR}` |
| 469 | **BUG** | `Path(__file__).resolve().parent / "ProPainter" / "inference_propainter.py"` → resolves to `/workspace/videoagent/ProPainter/...` but ProPainter is at `/workspace/ProPainter/` | Use `Path(os.environ.get("PROPAINTER_DIR", "/workspace/ProPainter")) / "inference_propainter.py"` |
| 470 | **BUG** | Error message `{BASE_DIR / 'ProPainter'}` shows wrong path | Use `{os.environ.get('PROPAINTER_DIR', '/workspace/ProPainter')}` |

### `scripts/inpaint_worker.py`

| Line | Severity | Issue | Fix |
|------|----------|-------|-----|
| 10–11 | DOCS | Docstring: `D:\\video-agent\\ProPainter\\` | — |
| 49 | **BUG** | `PROPAINTER_DIR = BASE_DIR / "ProPainter"` → `/workspace/videoagent/ProPainter` (wrong) | `PROPAINTER_DIR = Path(os.environ.get("PROPAINTER_DIR", "/workspace/ProPainter"))` |
| 50 | **BUG** | `PROPAINTER_SCRIPT = PROPAINTER_DIR / "inference_propainter.py"` — derived from bug above | Auto-fixed by fixing line 49 |
| 51 | **BUG** | `PROPAINTER_WEIGHTS = PROPAINTER_DIR / "weights"` — derived from bug above | Auto-fixed by fixing line 49 |

### `scripts/semantic_siglip.py`

| Line | Severity | Issue | Fix |
|------|----------|-------|-----|
| 264 | DOCS | Comment: `set MOONDREAM_MODEL_DIR=C:\path\to\model\folder` | — |
| 297–299 | **INFO** | Fallback model path: `Path.home() / ".cache" / "lm-studio" / "models" / "moondream" / "moondream-2b-2025-04-14-4bit"`. This path is Windows/LM Studio specific and will not exist on Vast.ai. **Guarded**: only executed when `VISION_TAGGER_MODEL=moondream` AND `MOONDREAM_MODEL_DIR` env var is not set. | Set `MOONDREAM_MODEL_DIR=/path/to/moondream/gguf-folder` in env if using moondream on Vast.ai. No code change required — the env var override already exists. |

### `scripts/model_path.py`

| Line | Severity | Issue | Fix |
|------|----------|-------|-----|
| 5 | N/A | `MODEL_ROOT = Path.home() / ".cache" / "lm-studio" / "models" / ...` — Windows/LM Studio path, will not exist on Vast.ai | Standalone test script — not imported by production. No fix needed. |
| 25 | N/A | `"file:///C:/Users/vinal/Downloads/..."` — hardcoded absolute Windows path | Same — standalone test only. |

### `scripts/analyze_and_edit.py`

| Lines | Severity | Issue |
|-------|----------|-------|
| All | OK | All paths use `Path(__file__).resolve().parent.parent` — platform-independent |
| 744–748 | OK | `as_posix()` comment says "required for Windows"; on Linux it's a no-op — still correct |

### `scripts/editing_brain.py`
| Lines | Severity | Issue |
|-------|----------|-------|
| All | OK | All paths use `Path(__file__).resolve().parent.parent` |

### `scripts/sam_helper.py`
| Lines | Severity | Issue |
|-------|----------|-------|
| 29 | OK | `SAM_CHECKPOINT = BASE_DIR / "style" / "sam_vit_b.pth"` — `style/` is inside videoagent dir |

### `scripts/semantic_aesthetic.py`
| Lines | Severity | Issue |
|-------|----------|-------|
| 96 | OK | `weights_path = Path(__file__).resolve().parent.parent / "style" / "aesthetic_mlp.pth"` — correct |

### `scripts/llm_planner.py`
| Lines | Severity | Issue |
|-------|----------|-------|
| All | OK | Uses `http://localhost:1234` — a URL, not a filesystem path. LM Studio availability on Vast.ai is a separate configuration concern, not a path migration bug. |

### `scripts/color_grade.py`
| Lines | Severity | Issue |
|-------|----------|-------|
| All | OK | `STYLE_PATH = BASE_DIR / "style" / "style_profile.json"` — correct |

### `scripts/smart_crop.py`
| Lines | Severity | Issue |
|-------|----------|-------|
| All | OK | Uses `cv2.data.haarcascades` — platform-independent |

### `scripts/pipeline_logger.py`
| Lines | Severity | Issue |
|-------|----------|-------|
| All | OK | Takes `logs_dir` as parameter — no hardcoded paths |

### `scripts/transitions.py`
| Lines | Severity | Issue |
|-------|----------|-------|
| All | OK | No filesystem paths |

### `scripts/whisper_helper.py`
| Lines | Severity | Issue |
|-------|----------|-------|
| All | OK | No filesystem paths (temp path derived from `video_path.parent`) |

### `scripts/check_crop.py`
| Lines | Severity | Issue |
|-------|----------|-------|
| 5 | DOCS | Docstring: `D:\\video-agent\\scripts\\` | — |
| All code | OK | Uses `Path(__file__).resolve().parent.parent` |

### `scripts/build_style_profile_from_videos.py`
| Lines | Severity | Issue |
|-------|----------|-------|
| 10 | DOCS | Docstring: `D:\\video-agent` | — |
| All code | OK | Uses `Path(__file__).resolve().parent.parent` |

### `scripts/analyze_reference_video.py`
| Lines | Severity | Issue |
|-------|----------|-------|
| All | OK | Uses `Path(__file__).resolve().parent.parent` |

### `scripts/archive/` (all files)
| Severity | Issue |
|----------|-------|
| N/A | Archive scripts are not imported by any production code. Not audited in detail. |

---

## Python — root

### `app.py` (continued from above — full file reviewed)
All routes and helpers: OK (all use `BASE_DIR` or relative paths derived from it).
Exceptions already noted in the table above.

### `git_raw_links.py`
| Lines | Severity | Issue |
|-------|----------|-------|
| All | OK | GitHub URLs only — no filesystem paths |

---

## Frontend — TypeScript / React

All frontend source files (`Web/video_agent/src/**`) contain browser-side code that communicates with Flask over HTTP. No filesystem paths are expected or present.

| File | Severity | Issue |
|------|----------|-------|
| `src/lib/api.ts` | OK | HTTP API calls only |
| `src/hooks/usePipeline.ts` | OK | HTTP API calls only |
| `src/components/InpaintTab.tsx` | OK | HTTP API calls only |
| `src/components/SegmentCard.tsx` | OK | HTTP API calls only |
| `src/components/MainContent.tsx` | OK | HTTP API calls only |
| `src/components/PipelineSidebar.tsx` | OK | HTTP API calls only |
| `src/components/DashboardHeader.tsx` | OK | No paths |
| `src/pages/Index.tsx` | OK | No paths |
| `src/index.css` | OK | No paths |
| `src/App.tsx` | OK | No paths |

---

## Config files

| File | Severity | Issue |
|------|----------|-------|
| `Web/video_agent/vite.config.ts` | OK | Proxy target `http://127.0.0.1:5100` — dev-only; production serves from Flask directly |
| `Web/video_agent/package.json` | OK | No filesystem paths |
| `Web/video_agent/tsconfig*.json` | OK | Relative paths only |
| `Web/video_agent/tailwind.config.ts` | OK | No filesystem paths |
| `Web/video_agent/postcss.config.js` | OK | No filesystem paths |
| `Web/video_agent/index.html` | OK | No filesystem paths |
| `requirements.txt` | OK | Package names only |
| `index.html` | OK | No filesystem paths |

---

## Documentation / operator files

| File | Severity | Issue |
|------|----------|-------|
| `Commands.txt` | DOCS | Windows-specific commands (`set`, PowerShell, `D:\video-agent`). Ops reference only — no code change needed, but a Linux equivalent should be written separately. |
| `CLAUDE.md` | DOCS | Contains `D:\video-agent\` in architecture notes |
| `SESSIONS.md` | DOCS | Contains `D:\video-agent\` in session notes |

---

## Summary of required code fixes

Three files need code changes. All other issues are documentation-only or informational.

### Fix 1 — `app.py:123`
```python
# BEFORE
warnings.append(f"Low disk space: {free_gb:.1f} GB free on {BASE_DIR.drive}")
# AFTER
warnings.append(f"Low disk space: {free_gb:.1f} GB free on {BASE_DIR}")
```

### Fix 2 — `app.py:469–470`
```python
# BEFORE
if not (Path(__file__).resolve().parent / "ProPainter" / "inference_propainter.py").exists():
    return jsonify({"error": f"ProPainter not found at {BASE_DIR / 'ProPainter'}"}), 501
# AFTER
_propainter_dir = Path(os.environ.get("PROPAINTER_DIR", "/workspace/ProPainter"))
if not (_propainter_dir / "inference_propainter.py").exists():
    return jsonify({"error": f"ProPainter not found at {_propainter_dir}"}), 501
```

### Fix 3 — `scripts/inpaint_worker.py:49`
```python
# BEFORE
PROPAINTER_DIR = BASE_DIR / "ProPainter"
# AFTER
PROPAINTER_DIR = Path(os.environ.get("PROPAINTER_DIR", "/workspace/ProPainter"))
```

### Operator action required (no code change)
- If using `VISION_TAGGER_MODEL=moondream` on Vast.ai: set `MOONDREAM_MODEL_DIR=/path/to/moondream-2b-2025-04-14-4bit`
- Default `PROPAINTER_DIR` is `/workspace/ProPainter` — matches Vast.ai layout
- If ProPainter is at a different path: set `PROPAINTER_DIR=/custom/path`
