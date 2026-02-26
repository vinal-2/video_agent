"""
Video Agent — Flask web UI
Place app.py and index.html both in:  D:\video-agent\

Run:   python app.py
Open:  http://localhost:5000
"""

import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import traceback
from pathlib import Path

from flask import Flask, Response, jsonify, request, send_from_directory

# ── Paths ─────────────────────────────────────────────────────────────────────
# app.py lives in D:\video-agent\ — that IS the project root
BASE_DIR     = Path(__file__).resolve().parent   # D:\video-agent\
RAW_CLIPS    = BASE_DIR / "raw_clips"
OUTPUT_DIR   = BASE_DIR / "output"
LOGS_DIR     = BASE_DIR / "logs"
STYLE_DIR    = BASE_DIR / "style_profiles"
COMMANDS_TXT = BASE_DIR / "Commands.txt"
REACT_DIST   = BASE_DIR / "Web" / "video_agent" / "dist"
APP_PORT     = int(os.environ.get("VIDEO_AGENT_PORT", "5100"))

OUTPUT_DIR.mkdir(exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)

UI_DIR = REACT_DIST if REACT_DIST.exists() else BASE_DIR / "Web"
if not UI_DIR.exists():
    UI_DIR = BASE_DIR

# Serve static files (index.html) from the built UI directory (falls back to legacy root HTML)
app = Flask(__name__, static_folder=str(UI_DIR), static_url_path="")


# ── Global pipeline state ─────────────────────────────────────────────────────

_pipeline_state = {
    "running":           False,
    "phase":             "idle",
    "log_lines":         [],
    "selected_segments": [],
    "reviewed_segments": [],
    "last_output":       None,
    "last_error":        None,
    "error_detail":      None,
    "clip_count":        0,
    "segment_counts":    {"selected": 0, "accepted": 0, "buffer": 0},
    "ffmpeg_progress":   None,
}
_state_lock: threading.Lock = threading.Lock()
_active_process: subprocess.Popen | None = None

# BUG FIX: per-client subscriber queues instead of a single shared queue.
# With a shared queue each get() removes the item — only one connected client
# ever sees each log line.  Now every SSE client gets its own queue and _emit
# fans out to all of them.
_sse_subscribers: list[queue.Queue] = []
_sse_lock: threading.Lock = threading.Lock()
_cancel_requested: bool = False  # set by api_cancel, read by _run_pipeline/_run_render


def _emit(line: str):
    with _state_lock:
        _pipeline_state["log_lines"].append(line)
    with _sse_lock:
        for q in _sse_subscribers:
            q.put(line)


def _refresh_clip_count() -> int:
    """Scan raw_clips/ and update cached clip count."""
    count = 0
    if RAW_CLIPS.exists():
        count = len(list(RAW_CLIPS.glob("*.mp4")))
    with _state_lock:
        _pipeline_state["clip_count"] = count
    return count


def _update_segment_counts(selected_segments: int | None = None,
                           accepted_segments: int | None = None,
                           buffer_segments: int | None = None) -> None:
    """Keep simple segment metrics for the status endpoint."""
    selected = selected_segments
    if selected is None:
        selected = len(_pipeline_state.get("selected_segments") or [])
    buffers = buffer_segments
    if buffers is None:
        buffers = sum(1 for seg in (_pipeline_state.get("selected_segments") or []) if seg.get("buffer"))
    accepted = accepted_segments
    if accepted is None:
        accepted = _pipeline_state.get("segment_counts", {}).get("accepted", selected)
    counts = {
        "selected": selected,
        "accepted": accepted,
        "buffer": buffers,
    }
    counts["pending"] = max(counts["selected"] - counts["accepted"], 0)
    _pipeline_state["segment_counts"] = counts


def _collect_warnings() -> list[str]:
    warnings: list[str] = []
    try:
        _, _, free = shutil.disk_usage(BASE_DIR)
        free_gb = free / (1024 ** 3)
        if free_gb < 5:
            warnings.append(f"Low disk space: {free_gb:.1f} GB free on {BASE_DIR.drive}")
    except Exception:
        warnings.append("Unable to read disk space information.")
    with _state_lock:
        last_error = _pipeline_state.get("last_error")
    if last_error:
        warnings.append(f"Pipeline error: {last_error}")
    return warnings


def _build_status_payload() -> dict:
    with _state_lock:
        state = {k: v for k, v in _pipeline_state.items() if k != "log_lines"}
        state["log_line_count"] = len(_pipeline_state["log_lines"])
    if not state.get("clip_count"):
        state["clip_count"] = _refresh_clip_count()
    state["warnings"] = _collect_warnings()
    return state


def _set_error(message: str, detail: str | None = None, *, assume_locked: bool = False):
    payload = detail or message
    if assume_locked:
        _pipeline_state["last_error"] = message
        _pipeline_state["error_detail"] = payload
        return
    with _state_lock:
        _pipeline_state["last_error"] = message
        _pipeline_state["error_detail"] = payload


_PROGRESS_RE = re.compile(r"(\d+(?:\.\d+)?)%")


def _parse_progress(line: str) -> float | None:
    if "frame_index" not in line and "frame=" not in line:
        return None
    match = _PROGRESS_RE.search(line)
    if not match:
        return None
    try:
        return float(match.group(1))
    except (TypeError, ValueError):
        return None


def _register_process(proc: subprocess.Popen | None) -> None:
    global _active_process
    with _state_lock:
        _active_process = proc


def _cancel_active_process() -> bool:
    """Attempt to terminate the currently running subprocess."""
    proc: subprocess.Popen | None
    with _state_lock:
        proc = _active_process
    if not proc:
        return False
    if proc.poll() is not None:
        _register_process(None)
        return False
    try:
        proc.terminate()
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
    finally:
        _register_process(None)
    return True


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    if (UI_DIR / "index.html").exists():
        return send_from_directory(str(UI_DIR), "index.html")
    if (UI_DIR / "Index.html").exists():
        return send_from_directory(str(UI_DIR), "Index.html")
    return send_from_directory(str(BASE_DIR), "index.html")


@app.route("/assets/<path:filename>")
def serve_assets(filename: str):
    assets_dir = UI_DIR / "assets"
    if assets_dir.exists():
        return send_from_directory(str(assets_dir), filename)
    return send_from_directory(str(UI_DIR), filename)


@app.route("/api/status")
def api_status():
    return jsonify(_build_status_payload())


@app.route("/api/clips")
def api_clips():
    clips = []
    if RAW_CLIPS.exists():
        for f in sorted(RAW_CLIPS.glob("*.mp4")):
            clips.append({"name": f.name, "size_mb": round(f.stat().st_size / 1e6, 1)})
    with _state_lock:
        _pipeline_state["clip_count"] = len(clips)
    return jsonify(clips)


@app.route("/api/commands")
def api_commands():
    text = ""
    if COMMANDS_TXT.exists():
        text = COMMANDS_TXT.read_text(encoding="utf-8", errors="replace")
    return jsonify({"text": text, "exists": COMMANDS_TXT.exists()})


@app.route("/api/templates")
def api_templates():
    templates = []
    if STYLE_DIR.exists():
        for f in sorted(STYLE_DIR.glob("*.json")):
            templates.append(f.stem)
    if not templates:
        templates = ["travel_reel"]
    return jsonify(templates)


@app.route("/api/logs")
def api_logs():
    with _state_lock:
        return jsonify(_pipeline_state["log_lines"])


@app.route("/api/stream")
def api_stream():
    def event_stream():
        client_q: queue.Queue = queue.Queue()
        with _sse_lock:
            _sse_subscribers.append(client_q)
        try:
            # Replay existing log lines so a late-connecting client catches up
            with _state_lock:
                existing = list(_pipeline_state["log_lines"])
            for line in existing:
                yield f"data: {json.dumps(line)}\n\n"
            while True:
                try:
                    line = client_q.get(timeout=30)
                    yield f"data: {json.dumps(line)}\n\n"
                except queue.Empty:
                    yield 'data: {"ping":true}\n\n'
        finally:
            # Clean up when client disconnects
            with _sse_lock:
                try:
                    _sse_subscribers.remove(client_q)
                except ValueError:
                    pass

    return Response(
        event_stream(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/api/run", methods=["POST"])
def api_run():
    params = request.get_json(force=True) or {}
    env    = _build_env(params)

    # BUG FIX: single lock acquisition eliminates TOCTOU race where two
    # concurrent requests could both pass the `running` check before either
    # sets running=True, starting two pipeline threads simultaneously.
    with _state_lock:
        if _pipeline_state["running"]:
            return jsonify({"error": "Pipeline already running"}), 409
        _pipeline_state.update({
            "running": True, "phase": "analysing",
            "log_lines": [], "selected_segments": [],
            "reviewed_segments": [], "last_output": None, "last_error": None,
            "error_detail": None,
            "ffmpeg_progress": None,
        })
        _update_segment_counts(0, 0, 0)

    threading.Thread(target=_run_pipeline, args=(env,), daemon=True).start()
    return jsonify({"ok": True})


@app.route("/api/review", methods=["POST"])
def api_review():
    data     = request.get_json(force=True) or {}
    reviewed = data.get("segments", [])
    params   = data.get("params", {})

    if not reviewed:
        return jsonify({"error": "No segments provided"}), 400

    # BUG FIX: single lock acquisition eliminates TOCTOU race (same as api_run).
    with _state_lock:
        if _pipeline_state["running"]:
            return jsonify({"error": "Pipeline already running"}), 409
        _pipeline_state.update({
            "running": True, "phase": "rendering",
            "reviewed_segments": reviewed,
            "log_lines": [], "last_error": None,
            "error_detail": None,
            "ffmpeg_progress": 0.0,
        })
        _update_segment_counts(accepted_segments=len(reviewed))

    env = _build_env(params)
    threading.Thread(target=_run_render, args=(reviewed, env), daemon=True).start()
    return jsonify({"ok": True})


@app.route("/api/cancel", methods=["POST"])
def api_cancel():
    global _cancel_requested
    # Set the flag BEFORE killing the process so _run_pipeline sees it
    # immediately when proc.wait() returns, preventing it from overwriting
    # phase back to "error" after we've already set it to "idle" here.
    _cancel_requested = True
    cancelled = _cancel_active_process()
    with _state_lock:
        _pipeline_state["running"] = False
        _pipeline_state["phase"] = "idle"
        _pipeline_state["ffmpeg_progress"] = None
        _set_error("Run cancelled by user", assume_locked=True)
    _emit("[warn] Pipeline cancelled by user")
    return jsonify({"ok": True, "cancelled": cancelled})


@app.route("/output/<path:filename>")
def serve_output(filename):
    return send_from_directory(str(OUTPUT_DIR), filename)


@app.route("/api/crop_auto", methods=["POST"])
def api_crop_auto():
    """
    Detect the best static 9:16 crop offset for a segment using cv2 cascades.
    Body: { video_path: str, start: float, end: float }
    Returns: { x, y, w, h, source_w, source_h }
    """
    body = request.get_json(force=True) or {}
    video_path = body.get("video_path", "")
    start      = float(body.get("start", 0))
    end        = float(body.get("end", 0))

    if not video_path:
        return jsonify({"error": "video_path required"}), 400

    # Resolve: accept a bare filename (from raw_clips/) or an absolute path
    p = Path(video_path)
    if not p.is_absolute():
        p = RAW_CLIPS / p.name
    if not p.exists():
        return jsonify({"error": f"Video not found: {p}"}), 404

    try:
        from scripts.smart_crop import compute_auto_crop
        result = compute_auto_crop(str(p), start, end)
        return jsonify(result)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/output/latest")
def api_output_latest():
    with _state_lock:
        last = _pipeline_state["last_output"]
    if last and Path(last).exists():
        p = Path(last)
        return jsonify({
            "path":    "/output/" + p.name,
            "name":    p.name,
            "size_mb": round(p.stat().st_size / 1e6, 1),
        })
    return jsonify({"path": None})


# ── Helpers ───────────────────────────────────────────────────────────────────

def _build_env(params: dict) -> dict:
    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUNBUFFERED", "1")  # prevent block-buffering in subprocess
    mappings = {
        "template":      "STYLE_TEMPLATE",
        "quality":       "RENDER_QUALITY",
        "buffer":        "BUFFER_SEGMENTS",
        "llm":           "ENABLE_LLM_PLANNER",
        "vision":        "VISION_TAGGER_MODEL",
        "vision_max":    "VISION_TAGGER_MAX_SEGMENTS",
        "disable_cache": "DISABLE_CACHE",
    }
    for key, env_var in mappings.items():
        if key in params and params[key] is not None:
            env[env_var] = str(params[key])
    return env


def _run_pipeline(env: dict):
    global _cancel_requested
    _cancel_requested = False
    try:
        _emit("── Starting pipeline ──")
        cmd = [sys.executable, "-m", "scripts.analyze_and_edit", "--ui-mode"]
        proc = subprocess.Popen(
            cmd, cwd=str(BASE_DIR), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1,
        )
        _register_process(proc)

        segments_json = None
        capture_json  = False
        json_lines    = []

        for line in proc.stdout:
            line = line.rstrip()
            if line == "<<SEGMENTS_JSON_START>>":
                capture_json = True
                continue
            if line == "<<SEGMENTS_JSON_END>>":
                capture_json = False
                try:
                    parsed = json.loads("".join(json_lines))
                    if parsed:
                        segments_json = parsed
                        # Transition phase immediately — don't wait for proc.wait().
                        # PyTorch/HuggingFace teardown can hold the subprocess alive
                        # for minutes after printing all output, which would block
                        # proc.wait() and keep the UI stuck at "analysing".
                        with _state_lock:
                            _pipeline_state["selected_segments"] = segments_json
                            _pipeline_state["phase"] = "reviewing"
                            _pipeline_state["running"] = False
                            buffer_count = sum(1 for seg in segments_json if seg.get("buffer"))
                            _update_segment_counts(len(segments_json), None, buffer_count)
                        _emit(f"\n── {len(segments_json)} segments ready for review ──")
                        _emit("Switch to the Review tab — accept or reject, then click Render.")
                except Exception as e:
                    _emit(f"[warn] Could not parse segment JSON: {e}")
                json_lines = []
                continue
            if capture_json:
                json_lines.append(line)
                continue
            _emit(line)

        proc.wait()

        # BUG FIX: if api_cancel was called while we were running, it already
        # set phase="idle". Do not overwrite that with "error" here.
        if _cancel_requested:
            return

        # Only report errors if we never successfully captured segments.
        if not segments_json:
            with _state_lock:
                if proc.returncode != 0:
                    _pipeline_state["phase"]      = "error"
                    _set_error("Pipeline exited with errors — check log", assume_locked=True)
                    _update_segment_counts(0, 0, 0)
                else:
                    _pipeline_state["phase"]      = "error"
                    _set_error("No segments returned — check log", assume_locked=True)
                    _update_segment_counts(0, 0, 0)

    except Exception as exc:
        if _cancel_requested:
            return
        detail = traceback.format_exc()
        _emit(f"[error] {exc}")
        with _state_lock:
            _pipeline_state["phase"] = "error"
            _set_error(str(exc), detail=detail, assume_locked=True)
    finally:
        with _state_lock:
            _pipeline_state["running"] = False


def _run_render(segments: list, env: dict):
    global _cancel_requested
    _cancel_requested = False
    try:
        _emit(f"── Rendering {len(segments)} accepted segments ──")

        seg_file = OUTPUT_DIR / "_pending_segments.json"
        seg_file.write_text(json.dumps(segments), encoding="utf-8")
        with _state_lock:
            _pipeline_state["ffmpeg_progress"] = 0.0

        cmd = [sys.executable, "-m", "scripts.analyze_and_edit",
               "--render-only", str(seg_file)]
        proc = subprocess.Popen(
            cmd, cwd=str(BASE_DIR), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1,
        )
        _register_process(proc)  # BUG FIX: was missing — cancel had no effect during render

        output_path = None
        for line in proc.stdout:
            line = line.rstrip()
            if line.startswith("<<OUTPUT_PATH>>"):
                output_path = line.split("<<OUTPUT_PATH>>")[1].strip()
                continue
            progress = _parse_progress(line)
            if progress is not None:
                with _state_lock:
                    _pipeline_state["ffmpeg_progress"] = max(0.0, min(progress / 100.0, 1.0))
            _emit(line)

        proc.wait()

        # BUG FIX: skip phase update if user cancelled
        if not _cancel_requested:
            if output_path and Path(output_path).exists():
                with _state_lock:
                    _pipeline_state["last_output"] = output_path
                    _pipeline_state["phase"]       = "done"
                    _pipeline_state["ffmpeg_progress"] = 1.0
                _emit(f"\n✓ Render complete → {Path(output_path).name}")
            else:
                with _state_lock:
                    _pipeline_state["phase"]      = "error"
                    _set_error("Render failed — check log", assume_locked=True)
                    _pipeline_state["ffmpeg_progress"] = None

        seg_file.unlink(missing_ok=True)

    except Exception as exc:
        if not _cancel_requested:
            detail = traceback.format_exc()
            _emit(f"[error] {exc}")
            with _state_lock:
                _pipeline_state["phase"] = "error"
                _set_error(str(exc), detail=detail, assume_locked=True)
                _pipeline_state["ffmpeg_progress"] = None
    finally:
        _register_process(None)
        with _state_lock:
            _pipeline_state["running"] = False


# ── Video streaming with Range Request support ────────────────────────────────
# Required for browser video seeking to work. Without this, trim handles
# and scrubbing silently break.
# BUG FIX: moved before __main__ block; removed redundant `import re as _re`
# (re is already imported at the top of the file).

@app.route("/video/<path:filepath>")
def serve_video(filepath):
    """Stream video with HTTP 206 Partial Content for seeking support."""
    # Resolve the file — could be in raw_clips/ or anywhere on disk
    # Try absolute path first (segments store full paths), then relative
    candidate = Path(filepath)
    if not candidate.is_absolute():
        candidate = BASE_DIR / filepath
    if not candidate.exists():
        # Last try: just the filename in raw_clips/
        candidate = RAW_CLIPS / Path(filepath).name
    if not candidate.exists():
        return "Not found", 404

    file_size = candidate.stat().st_size
    range_header = request.headers.get('Range')

    if range_header:
        # Parse "bytes=start-end"
        m = re.match(r'bytes=(\d+)-(\d*)', range_header)
        if m:
            start = int(m.group(1))
            end   = int(m.group(2)) if m.group(2) else file_size - 1
            end   = min(end, file_size - 1)
            length = end - start + 1

            def generate():
                with open(candidate, 'rb') as f:
                    f.seek(start)
                    remaining = length
                    chunk = 65536
                    while remaining > 0:
                        data = f.read(min(chunk, remaining))
                        if not data:
                            break
                        remaining -= len(data)
                        yield data

            resp = Response(
                generate(),
                status=206,
                mimetype='video/mp4',
                headers={
                    'Content-Range':  f'bytes {start}-{end}/{file_size}',
                    'Accept-Ranges':  'bytes',
                    'Content-Length': str(length),
                    'Cache-Control':  'no-cache',
                }
            )
            return resp

    # Full file (no Range header)
    def generate_full():
        with open(candidate, 'rb') as f:
            while True:
                data = f.read(65536)
                if not data:
                    break
                yield data

    return Response(
        generate_full(),
        mimetype='video/mp4',
        headers={
            'Accept-Ranges':  'bytes',
            'Content-Length': str(file_size),
            'Cache-Control':  'no-cache',
        }
    )


if __name__ == "__main__":
    print(f"Project root:  {BASE_DIR}")
    print(f"index.html:    {BASE_DIR / 'index.html'} (exists: {(BASE_DIR / 'index.html').exists()})")
    print(f"Video Agent UI -> http://localhost:{APP_PORT}")
    app.run(host="0.0.0.0", port=APP_PORT, debug=False, threaded=True)
