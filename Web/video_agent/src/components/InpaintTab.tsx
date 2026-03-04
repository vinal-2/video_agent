import { useCallback, useEffect, useRef, useState } from "react";
import {
  SkipForward,
  Paintbrush,
  Loader2,
  CheckCircle2,
  AlertCircle,
  PlayCircle,
  X,
} from "lucide-react";
import type { Segment, OutputInfo, InpaintJob, TrimState, InpaintEngine } from "@/lib/api";
import type { SegmentDecision } from "@/hooks/usePipeline";
import InpaintCanvas from "@/components/InpaintCanvas";

// ── Engine options ─────────────────────────────────────────────────────────────

const ENGINES: Array<{
  value:  InpaintEngine;
  label:  string;
  time:   string;
  qual:   string;
  method: string;
}> = [
  { value: "diffueraser", label: "DiffuEraser",   time: "~10 min/clip", qual: "Best quality", method: "Generative reconstruction" },
  { value: "lama",        label: "LaMa",          time: "~2 min/clip",  qual: "Good quality", method: "Pattern-based fill"         },
  { value: "lama+e2fgvi", label: "LaMa + E2FGVI", time: "~5 min/clip",  qual: "High quality", method: "Temporal smoothing"         },
  { value: "propainter",  label: "ProPainter",    time: "~25 min/clip", qual: "Slowest",      method: "Optical flow"               },
];

// ── Helpers ────────────────────────────────────────────────────────────────────

function fmtSeconds(s: number): string {
  if (s < 60) return `${s}s`;
  return `${Math.floor(s / 60)}m ${s % 60}s`;
}

function useElapsed(active: boolean): string {
  const [secs, setSecs] = useState(0);
  const ref = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    if (active) {
      setSecs(0);
      ref.current = setInterval(() => setSecs((s) => s + 1), 1000);
    } else {
      if (ref.current) clearInterval(ref.current);
    }
    return () => { if (ref.current) clearInterval(ref.current); };
  }, [active]);

  return active ? fmtSeconds(secs) : "";
}

function parsePhaseLabel(status: string): string {
  if (status === "pending") return "Queued...";
  if (status === "running") return "Inpainting...";
  if (status === "done")    return "Complete";
  if (status === "failed")  return "Failed";
  return status;
}

// ── Per-job progress widget ────────────────────────────────────────────────────

const JobProgress = ({ job }: { job: InpaintJob }) => {
  const isRunning = job.status.status === "running" || job.status.status === "pending";
  const elapsed   = useElapsed(isRunning);
  const pct       = Math.round(job.status.progress * 100);

  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between">
        <span style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--text-secondary)" }}>
          {parsePhaseLabel(job.status.status)}
        </span>
        {elapsed && (
          <span style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--text-muted)" }}>
            {elapsed} elapsed
          </span>
        )}
      </div>

      {job.status.status === "running" && (
        <>
          <div className="relative overflow-hidden rounded-full" style={{ height: 4, background: "var(--bg-tertiary)" }}>
            <div
              className="h-full rounded-full transition-all"
              style={{ width: `${pct}%`, background: "linear-gradient(90deg, var(--accent-secondary), var(--accent-primary))" }}
            />
          </div>
          <div className="flex items-center justify-between">
            {job.status.frames_total > 0 && (
              <span style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--text-muted)" }}>
                {job.status.frames_done} / {job.status.frames_total} frames
              </span>
            )}
            {job.status.estimated_seconds !== null && (
              <span style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--text-muted)" }}>
                ~{fmtSeconds(job.status.estimated_seconds)} left
              </span>
            )}
          </div>
        </>
      )}

      {job.status.status === "failed" && job.status.error && (
        <p
          className="text-[10px] truncate"
          style={{ fontFamily: "var(--font-mono)", color: "#f87171" }}
          title={job.status.error}
        >
          {job.status.error.slice(0, 80)}
        </p>
      )}
    </div>
  );
};

// ── InpaintTab ────────────────────────────────────────────────────────────────

interface InpaintTabProps {
  segments:               Segment[];
  segmentStates:          Record<number, SegmentDecision | undefined>;
  trimData:               Record<number, TrimState>;
  inpaintJobs:            Record<string, InpaintJob>;
  outputInfo:             OutputInfo | null;
  phase:                  string;
  onBeginInpaint:         (segIdx: number, maskB64: string, engine: InpaintEngine) => Promise<string>;
  onCancelJob:            (jobId: string) => Promise<void>;
  onSkip:                 () => void;
  onRenderWithInpainting: () => Promise<void>;
  running:                boolean;
}

const InpaintTab = ({
  segments,
  segmentStates,
  trimData,
  inpaintJobs,
  outputInfo,
  phase,
  onBeginInpaint,
  onCancelJob,
  onSkip,
  onRenderWithInpainting,
  running,
}: InpaintTabProps) => {
  const [drawingIndex, setDrawingIndex] = useState<number | null>(null);
  const [submitting, setSubmitting]     = useState<Record<number, boolean>>({});
  const [renderBusy, setRenderBusy]     = useState(false);
  const [engine, setEngine]             = useState<InpaintEngine>("diffueraser");

  const jobBySegment = Object.values(inpaintJobs).reduce<Record<number, InpaintJob>>(
    (acc, job) => {
      if (!acc[job.segmentIndex]) return { ...acc, [job.segmentIndex]: job };
      return acc;
    },
    {},
  );

  const acceptedSegments = segments
    .map((seg, idx) => ({ seg, idx }))
    .filter(({ idx }) => segmentStates[idx] !== "rejected");

  const doneJobs  = Object.values(inpaintJobs).filter((j) => j.status.status === "done");
  const canRender = doneJobs.length > 0 && !running && !renderBusy;

  const handleConfirmMask = useCallback(async (segIdx: number, maskB64: string) => {
    setDrawingIndex(null);
    setSubmitting((prev) => ({ ...prev, [segIdx]: true }));
    try {
      await onBeginInpaint(segIdx, maskB64, engine);
    } catch (err) {
      console.error("inpaint start failed", err);
    } finally {
      setSubmitting((prev) => ({ ...prev, [segIdx]: false }));
    }
  }, [onBeginInpaint, engine]);

  const handleRender = () => {
    setRenderBusy(true);
    onRenderWithInpainting()
      .then(() => onSkip())
      .catch((err) => console.error(err))
      .finally(() => setRenderBusy(false));
  };

  if (phase !== "done" && !outputInfo) {
    return (
      <div className="h-full flex flex-col items-center justify-center gap-3" style={{ color: "var(--text-secondary)" }}>
        <Paintbrush className="w-8 h-8" style={{ color: "var(--text-muted)" }} />
        <p className="text-sm">Inpaint tab is available after rendering.</p>
        <p className="text-xs" style={{ color: "var(--text-muted)" }}>Complete the Review step and render first.</p>
      </div>
    );
  }

  return (
    <div className="h-full overflow-y-auto px-6 py-5 space-y-6">

      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-base font-semibold" style={{ fontFamily: "var(--font-display)", color: "var(--text-primary)" }}>
            Inpaint cleanup
          </h2>
          <p className="text-xs mt-0.5" style={{ color: "var(--text-secondary)" }}>
            Draw regions to remove from clips. The selected engine fills them in.
          </p>
        </div>
        <button
          onClick={onSkip}
          className="flex items-center gap-1.5 px-3 py-2 rounded text-xs transition-colors"
          style={{ fontFamily: "var(--font-display)", border: "1px solid var(--border-subtle)", color: "var(--text-secondary)" }}
        >
          Skip Inpaint
          <SkipForward className="w-3.5 h-3.5" />
        </button>
      </div>

      {/* Engine selector — 4 cards, 2×2 grid */}
      <div>
        <p
          className="mb-3 uppercase"
          style={{ fontFamily: "var(--font-display)", fontSize: 11, fontWeight: 500, letterSpacing: "0.1em", color: "var(--text-muted)" }}
        >
          Inpaint Engine
        </p>
        <div className="grid grid-cols-2 gap-2">
          {ENGINES.map(({ value, label, time, qual, method }) => {
            const active = engine === value;
            return (
              <button
                key={value}
                onClick={() => setEngine(value)}
                className="relative text-left p-3 rounded transition-all"
                style={{
                  background: active ? "var(--accent-muted)"   : "var(--bg-tertiary)",
                  border:     active ? "1px solid var(--accent-primary)" : "1px solid var(--border-subtle)",
                  borderLeft: active ? "3px solid var(--accent-primary)" : "1px solid var(--border-subtle)",
                }}
              >
                {active && (
                  <span
                    className="absolute top-2 right-2 w-4 h-4 rounded-full flex items-center justify-center"
                    style={{ background: "var(--accent-primary)" }}
                  >
                    <svg width="8" height="8" viewBox="0 0 8 8" fill="none">
                      <path d="M1 4l2 2 4-4" stroke="var(--bg-primary)" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
                    </svg>
                  </span>
                )}
                <p className="font-medium" style={{ fontFamily: "var(--font-display)", fontSize: 13, color: active ? "var(--accent-primary)" : "var(--text-primary)" }}>
                  {label}
                </p>
                <p className="mt-0.5" style={{ fontSize: 11, color: "var(--text-muted)" }}>{time} · {qual}</p>
                <p className="mt-0.5" style={{ fontSize: 10, color: "var(--text-muted)", fontStyle: "italic" }}>{method}</p>
              </button>
            );
          })}
        </div>
      </div>

      {/* Segment list */}
      <div className="space-y-3">
        {acceptedSegments.map(({ seg, idx }) => {
          const fileName  = seg.video_path?.split(/[/\\]/).pop() ?? "Unknown";
          const trim      = trimData[idx] ?? { start: seg.start, end: seg.end };
          const midTime   = (trim.start + trim.end) / 2;
          const thumbUrl  = `/video/${encodeURIComponent(fileName)}#t=${midTime.toFixed(2)}`;
          const job       = jobBySegment[idx];
          const isSub     = submitting[idx] ?? false;
          const isDrawing = drawingIndex === idx;

          const statusColor =
            job?.status.status === "done"    ? "var(--status-accepted)"  :
            job?.status.status === "failed"  ? "var(--status-failed)"    :
            job?.status.status === "running" ? "var(--accent-primary)"   :
            job?.status.status === "pending" ? "var(--status-running)"   :
            "transparent";

          return (
            <div
              key={idx}
              className="rounded overflow-hidden"
              style={{ background: "var(--bg-secondary)", border: "1px solid var(--border-subtle)", borderLeft: `3px solid ${statusColor}` }}
            >
              <div className="flex items-center gap-3 p-3">
                {/* Thumbnail */}
                <div className="flex-shrink-0 rounded overflow-hidden" style={{ width: 48, height: 72, background: "#000" }}>
                  <video src={thumbUrl} className="w-full h-full object-cover" preload="metadata" muted playsInline />
                </div>

                {/* Info + progress */}
                <div className="flex-1 min-w-0 space-y-2">
                  <div>
                    <p className="text-sm font-medium truncate" style={{ fontFamily: "var(--font-mono)", color: "var(--text-primary)" }}>
                      {fileName}
                    </p>
                    <p style={{ fontSize: 10, fontFamily: "var(--font-mono)", color: "var(--text-muted)" }}>
                      {trim.start.toFixed(2)}s – {trim.end.toFixed(2)}s
                    </p>
                  </div>

                  {job && (
                    <div className="flex items-center gap-2">
                      <span
                        className="flex items-center gap-1 px-2 py-0.5 rounded text-[10px]"
                        style={{ fontFamily: "var(--font-mono)", background: `${statusColor}20`, color: statusColor }}
                      >
                        {job.status.status === "done"    && <CheckCircle2 className="w-3 h-3" />}
                        {(job.status.status === "running" || job.status.status === "pending") && <Loader2 className="w-3 h-3 animate-spin" />}
                        {job.status.status === "failed"  && <AlertCircle className="w-3 h-3" />}
                        {job.status.status}
                      </span>
                      {job.engine && (
                        <span style={{ fontSize: 10, color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}>{job.engine}</span>
                      )}
                    </div>
                  )}

                  {job && <JobProgress job={job} />}
                </div>

                {/* Actions */}
                <div className="flex flex-col items-end gap-2 flex-shrink-0">
                  {job && (job.status.status === "running" || job.status.status === "pending") && (
                    <button
                      onClick={() => onCancelJob(job.jobId)}
                      className="flex items-center gap-1 px-2 py-1.5 rounded text-[10px] transition-colors"
                      style={{ border: "1px solid var(--border-subtle)", color: "var(--text-secondary)" }}
                    >
                      <X className="w-3 h-3" />
                      Cancel
                    </button>
                  )}

                  {/* Draw / Redraw — always available */}
                  <button
                    onClick={() => setDrawingIndex(isDrawing ? null : idx)}
                    disabled={isSub}
                    className="flex items-center gap-1 px-2 py-1.5 rounded text-[10px] transition-colors"
                    style={{
                      border:     `1px solid ${isDrawing ? "var(--accent-primary)" : "var(--border-subtle)"}`,
                      background: isDrawing ? "var(--accent-muted)" : "var(--bg-tertiary)",
                      color:      isDrawing ? "var(--accent-primary)" : "var(--text-secondary)",
                    }}
                  >
                    {isSub ? <Loader2 className="w-3 h-3 animate-spin" /> : <Paintbrush className="w-3 h-3" />}
                    {isDrawing ? "Cancel" : job?.status.status === "done" ? "Redraw" : "Draw region"}
                  </button>
                </div>
              </div>

              {/* Inline canvas */}
              {isDrawing && (
                <div className="border-t p-4" style={{ borderColor: "var(--border-subtle)", background: "var(--bg-tertiary)" }}>
                  <InpaintCanvas
                    videoPath={seg.video_path ?? ""}
                    midTime={midTime}
                    onConfirm={(b64) => handleConfirmMask(idx, b64)}
                    onCancel={() => setDrawingIndex(null)}
                  />
                </div>
              )}
            </div>
          );
        })}
      </div>

      {/* Render footer */}
      <div className="pt-4" style={{ borderTop: "1px solid var(--border-subtle)" }}>
        <button
          onClick={handleRender}
          disabled={!canRender}
          className="flex items-center gap-2 px-5 py-3 rounded gradient-primary-btn disabled:opacity-50 transition-all"
          style={{
            fontFamily: "var(--font-display)",
            fontWeight: 600,
            fontSize: 14,
            color: "var(--bg-primary)",
            boxShadow: canRender ? "0 0 20px rgba(232,160,64,0.3)" : "none",
          }}
        >
          <PlayCircle className="w-4 h-4" />
          {renderBusy
            ? "Rendering…"
            : `Render with inpainting (${doneJobs.length} clip${doneJobs.length !== 1 ? "s" : ""})`
          }
        </button>
        <p className="mt-1.5 text-[10px]" style={{ color: "var(--text-muted)" }}>
          Completed inpaint clips replace originals. All others render as normal.
        </p>
      </div>
    </div>
  );
};

export default InpaintTab;
