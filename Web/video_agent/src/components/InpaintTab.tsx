import { useRef, useState, useCallback, useEffect } from "react";
import { SkipForward, Paintbrush, Loader2, CheckCircle2, AlertCircle, RotateCcw, PlayCircle } from "lucide-react";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog";
import { Progress } from "@/components/ui/progress";
import type { Segment, OutputInfo, InpaintJob, TrimState } from "@/lib/api";
import { getColabStatus } from "@/lib/api";
import type { SegmentDecision } from "@/hooks/usePipeline";

// ── DrawRegionModal ────────────────────────────────────────────────────────────
//
// Canvas over the segment thumbnail. User draws a freehand removal region.
// On confirm, the canvas is exported as a binary PNG (white=remove, black=keep).

interface DrawRegionModalProps {
  open: boolean;
  segment: Segment;
  trim: TrimState;
  onConfirm: (maskB64: string) => void;
  onClose: () => void;
}

const CANVAS_W = 360;  // display width — height is computed from video AR

const DrawRegionModal = ({ open, segment, trim, onConfirm, onClose }: DrawRegionModalProps) => {
  const canvasRef   = useRef<HTMLCanvasElement>(null);
  const paintingRef = useRef(false);
  const [hasStrokes, setHasStrokes] = useState(false);
  // Actual video pixel dimensions — determined when video metadata loads.
  // Canvas internal resolution is set to match so the mask AR is correct.
  const [videoNative, setVideoNative] = useState<{ w: number; h: number } | null>(null);

  const canvasH = videoNative
    ? Math.round(CANVAS_W * videoNative.h / videoNative.w)
    : 640; // fallback before metadata loads

  const fileName = segment.video_path?.split(/[/\\]/).pop() ?? "";
  const midTime  = (trim.start + trim.end) / 2;
  const videoUrl = `/video/${encodeURIComponent(fileName)}#t=${midTime.toFixed(2)}`;

  // Reset state when modal opens
  useEffect(() => {
    if (!open) return;
    setVideoNative(null);
    setHasStrokes(false);
  }, [open]);

  // Clear canvas whenever canvasH changes (video metadata arrived)
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    setHasStrokes(false);
  }, [canvasH]);

  const getPos = (e: React.MouseEvent<HTMLCanvasElement> | React.TouchEvent<HTMLCanvasElement>) => {
    const canvas = canvasRef.current;
    if (!canvas) return { x: 0, y: 0 };
    const rect = canvas.getBoundingClientRect();
    const scaleX = canvas.width  / rect.width;
    const scaleY = canvas.height / rect.height;
    const clientX = "touches" in e ? e.touches[0].clientX : e.clientX;
    const clientY = "touches" in e ? e.touches[0].clientY : e.clientY;
    return {
      x: (clientX - rect.left)  * scaleX,
      y: (clientY - rect.top)   * scaleY,
    };
  };

  const startPaint = (e: React.MouseEvent<HTMLCanvasElement>) => {
    e.preventDefault();
    paintingRef.current = true;
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext("2d");
    if (!ctx) return;
    const { x, y } = getPos(e);
    ctx.beginPath();
    ctx.moveTo(x, y);
  };

  const paint = (e: React.MouseEvent<HTMLCanvasElement>) => {
    if (!paintingRef.current) return;
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext("2d");
    if (!ctx) return;
    const { x, y } = getPos(e);
    ctx.lineTo(x, y);
    ctx.strokeStyle = "white";
    ctx.lineWidth = 28;
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    ctx.stroke();
    setHasStrokes(true);
  };

  const stopPaint = () => { paintingRef.current = false; };

  const clearCanvas = () => {
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext("2d");
    if (!ctx || !canvas) return;
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    setHasStrokes(false);
  };

  const confirmMask = () => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    // Export as PNG — strokes are white on transparent bg → binarise:
    // white pixels (alpha>0) stay white, transparent → black
    const offscreen = document.createElement("canvas");
    offscreen.width  = canvas.width;
    offscreen.height = canvas.height;
    const ctx2 = offscreen.getContext("2d");
    if (!ctx2) return;
    // Fill black background first
    ctx2.fillStyle = "black";
    ctx2.fillRect(0, 0, offscreen.width, offscreen.height);
    // Draw white strokes on top
    ctx2.drawImage(canvas, 0, 0);
    const dataUrl = offscreen.toDataURL("image/png");
    const b64     = dataUrl.split(",")[1];
    onConfirm(b64);
  };

  return (
    <Dialog open={open} onOpenChange={(v) => { if (!v) onClose(); }}>
      <DialogContent className="glass-surface border-border/60 max-w-md">
        <DialogHeader>
          <DialogTitle className="text-foreground">Draw removal region</DialogTitle>
        </DialogHeader>
        <p className="text-xs text-muted-foreground">
          Paint over the area you want to remove. ProPainter will fill it in.
        </p>

        {/* Stacked: video thumbnail below, canvas on top.
            Container height is set from the real video AR so the mask
            pixel coordinates align with what ProPainter will process. */}
        <div
          className="relative rounded overflow-hidden bg-black select-none"
          style={{ width: CANVAS_W, height: canvasH, maxWidth: "100%" }}
        >
          <video
            src={videoUrl}
            className="absolute inset-0 w-full h-full object-contain pointer-events-none"
            preload="metadata"
            muted
            playsInline
            onLoadedMetadata={(e) => {
              const v = e.currentTarget;
              if (v.videoWidth && v.videoHeight) {
                setVideoNative({ w: v.videoWidth, h: v.videoHeight });
              }
            }}
          />
          <canvas
            ref={canvasRef}
            width={CANVAS_W}
            height={canvasH}
            className="absolute inset-0 w-full h-full cursor-crosshair"
            style={{ opacity: 0.6 }}
            onMouseDown={startPaint}
            onMouseMove={paint}
            onMouseUp={stopPaint}
            onMouseLeave={stopPaint}
          />
        </div>

        <DialogFooter className="flex gap-2">
          <button
            onClick={clearCanvas}
            className="flex items-center gap-1 text-xs px-3 py-2 rounded border border-border/50 text-muted-foreground hover:border-border/80 transition-colors"
          >
            <RotateCcw className="w-3.5 h-3.5" />
            Clear
          </button>
          <button
            onClick={confirmMask}
            disabled={!hasStrokes}
            className="flex items-center gap-2 px-4 py-2 rounded-lg gradient-primary-btn text-primary-foreground text-xs font-semibold disabled:opacity-50 transition-colors"
          >
            <Paintbrush className="w-3.5 h-3.5" />
            Confirm region
          </button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};

// ── Helpers ────────────────────────────────────────────────────────────────────

function formatSeconds(s: number | null): string {
  if (s === null) return "";
  if (s < 60)  return `${s}s`;
  return `${Math.floor(s / 60)}m ${s % 60}s`;
}

// ── InpaintTab ────────────────────────────────────────────────────────────────

interface InpaintTabProps {
  segments: Segment[];
  segmentStates: Record<number, SegmentDecision | undefined>;
  trimData: Record<number, TrimState>;
  inpaintJobs: Record<string, InpaintJob>;
  outputInfo: OutputInfo | null;
  phase: string;
  onBeginInpaint: (
    segmentIndex: number,
    maskB64: string,
    mode: "local" | "remote",
  ) => Promise<string>;
  onCancelJob: (jobId: string) => Promise<void>;
  onSkip: () => void;
  onRenderWithInpainting: () => Promise<void>;
  running: boolean;
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
  const [drawingIndex, setDrawingIndex]     = useState<number | null>(null);
  const [submitting, setSubmitting]         = useState<Record<number, boolean>>({});
  const [renderBusy, setRenderBusy]         = useState(false);
  const [mode, setMode]                     = useState<"local" | "remote">("local");
  const [colabOnline, setColabOnline]       = useState(false);
  const [colabGpu, setColabGpu]             = useState<string>("");

  // Poll Colab status every 30s while this tab is mounted
  useEffect(() => {
    let cancelled = false;
    const poll = async () => {
      try {
        const s = await getColabStatus();
        if (!cancelled) {
          setColabOnline(s.online);
          setColabGpu(s.gpu ?? "");
        }
      } catch { /* ignore */ }
    };
    poll();
    const id = setInterval(poll, 30_000);
    return () => { cancelled = true; clearInterval(id); };
  }, []);

  // Build a lookup: segmentIndex → most recent InpaintJob
  const jobBySegment = Object.values(inpaintJobs).reduce<Record<number, InpaintJob>>(
    (acc, job) => {
      const existing = acc[job.segmentIndex];
      if (!existing) return { ...acc, [job.segmentIndex]: job };
      // keep more recent (higher status priority: done > running > pending)
      return acc;
    },
    {},
  );

  // Accepted segments only (same ones that were rendered)
  const acceptedSegments = segments
    .map((seg, idx) => ({ seg, idx }))
    .filter(({ idx }) => segmentStates[idx] !== "rejected");

  const doneJobs = Object.values(inpaintJobs).filter((j) => j.status.status === "done");
  const canRenderWithInpaint = doneJobs.length > 0 && !running && !renderBusy;

  const handleConfirmMask = useCallback(async (segIdx: number, maskB64: string) => {
    if (!segments[segIdx]) return;
    setDrawingIndex(null);
    setSubmitting((prev) => ({ ...prev, [segIdx]: true }));
    try {
      await onBeginInpaint(segIdx, maskB64, mode);
    } catch (err) {
      console.error("inpaint start failed", err);
    } finally {
      setSubmitting((prev) => ({ ...prev, [segIdx]: false }));
    }
  }, [segments, onBeginInpaint, mode]);

  const handleRender = () => {
    setRenderBusy(true);
    onRenderWithInpainting()
      .then(() => onSkip())   // navigate to Output tab once render is queued
      .catch((err) => console.error(err))
      .finally(() => setRenderBusy(false));
  };

  if (phase !== "done" && !outputInfo) {
    return (
      <div className="h-full flex items-center justify-center text-muted-foreground text-sm">
        Inpaint tab is available after rendering — complete the Review step first.
      </div>
    );
  }

  const drawingSegment = drawingIndex !== null ? segments[drawingIndex] : null;
  const drawingTrim    = drawingIndex !== null
    ? (trimData[drawingIndex] ?? { start: drawingSegment!.start, end: drawingSegment!.end })
    : { start: 0, end: 0 };

  return (
    <div className="h-full overflow-y-auto px-6 py-4 space-y-5">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold text-foreground">Inpaint cleanup</h2>
          <p className="text-sm text-muted-foreground">
            Draw regions to remove from individual clips. Powered by ProPainter.
          </p>
        </div>
        <button
          onClick={onSkip}
          className="flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground border border-border/50 hover:border-border/80 px-3 py-2 rounded-lg transition-colors"
        >
          Skip Inpaint
          <SkipForward className="w-4 h-4" />
        </button>
      </div>

      {/* Mode selector */}
      <div className="flex items-center gap-2">
        <span className="text-xs text-muted-foreground">Process on:</span>
        <button
          onClick={() => setMode("local")}
          className={`px-3 py-1.5 rounded text-xs font-medium transition-colors border ${
            mode === "local"
              ? "gradient-primary-btn text-primary-foreground border-transparent"
              : "border-border/50 text-muted-foreground hover:border-border/80"
          }`}
        >
          Local
        </button>
        <button
          onClick={() => colabOnline && setMode("remote")}
          disabled={!colabOnline}
          className={`flex items-center gap-1.5 px-3 py-1.5 rounded text-xs font-medium transition-colors border disabled:opacity-50 disabled:cursor-not-allowed ${
            mode === "remote"
              ? "gradient-primary-btn text-primary-foreground border-transparent"
              : "border-border/50 text-muted-foreground hover:border-border/80"
          }`}
        >
          Remote (Colab)
          {colabOnline
            ? <span className="text-green-400">● {colabGpu || "online"}</span>
            : <span className="text-muted-foreground">○ offline</span>}
        </button>
      </div>

      {/* Output reference video */}
      {outputInfo?.path && (
        <div className="rounded-lg overflow-hidden border border-border/40 bg-black">
          <p className="text-[10px] text-muted-foreground px-3 pt-2 pb-1 font-mono">Rendered output</p>
          <video
            src={outputInfo.path}
            controls
            className="w-full max-h-48 object-contain"
            preload="metadata"
          />
        </div>
      )}

      {/* Segment list */}
      <div className="space-y-2">
        {acceptedSegments.map(({ seg, idx }) => {
          const fileName = seg.video_path?.split(/[/\\]/).pop() ?? "Unknown";
          const trim     = trimData[idx] ?? { start: seg.start, end: seg.end };
          const midTime  = (trim.start + trim.end) / 2;
          const thumbUrl = `/video/${encodeURIComponent(fileName)}#t=${midTime.toFixed(2)}`;
          const job      = jobBySegment[idx];
          const isSub    = submitting[idx] ?? false;

          return (
            <div key={idx} className="glass-card border border-border/50 rounded-lg p-3 flex items-center gap-3">
              {/* Thumbnail */}
              <div
                className="flex-shrink-0 rounded overflow-hidden bg-black"
                style={{ width: 40, aspectRatio: "9/16" }}
              >
                <video
                  src={thumbUrl}
                  className="w-full h-full object-contain"
                  preload="metadata"
                  muted
                  playsInline
                />
              </div>

              {/* Info */}
              <div className="flex-1 min-w-0">
                <p className="text-sm font-medium text-foreground truncate">{fileName}</p>
                <p className="text-[10px] font-mono text-muted-foreground">
                  {trim.start.toFixed(2)}s – {trim.end.toFixed(2)}s
                </p>

                {/* Progress bar */}
                {job && job.status.status === "running" && (
                  <div className="mt-1 space-y-0.5">
                    <Progress value={Math.round(job.status.progress * 100)} className="h-1" />
                    <p className="text-[9px] font-mono text-muted-foreground">
                      {job.status.frames_done}/{job.status.frames_total} frames
                      {job.status.estimated_seconds !== null && ` · ${formatSeconds(job.status.estimated_seconds)} left`}
                    </p>
                  </div>
                )}

                {/* Remote pending hint */}
                {job && job.status.status === "pending" && mode === "remote" && (
                  <p className="text-[9px] font-mono text-muted-foreground mt-0.5">
                    Waiting for Colab to pick up job…
                  </p>
                )}

                {/* Error */}
                {job?.status.status === "failed" && (
                  <p className="text-[10px] text-destructive mt-0.5 truncate">
                    {job.status.error ?? "Failed"}
                  </p>
                )}
              </div>

              {/* Status + action */}
              <div className="flex items-center gap-2 flex-shrink-0">
                {/* Status pill */}
                {job && (
                  <span className={`flex items-center gap-1 text-[10px] font-mono px-1.5 py-0.5 rounded-full border ${
                    job.status.status === "done"    ? "border-status-online/40 text-status-online" :
                    job.status.status === "running" ? "border-amber-400/40 text-amber-400" :
                    job.status.status === "failed"  ? "border-destructive/40 text-destructive" :
                                                      "border-border/40 text-muted-foreground"
                  }`}>
                    {job.status.status === "done"    && <CheckCircle2 className="w-3 h-3" />}
                    {job.status.status === "running" && <Loader2 className="w-3 h-3 animate-spin" />}
                    {job.status.status === "failed"  && <AlertCircle className="w-3 h-3" />}
                    {job.status.status === "pending" && <Loader2 className="w-3 h-3 animate-spin" />}
                    {job.status.status}
                  </span>
                )}

                {/* Cancel running job */}
                {job && (job.status.status === "running" || job.status.status === "pending") && (
                  <button
                    onClick={() => onCancelJob(job.jobId)}
                    className="text-[10px] px-2 py-1 rounded border border-border/40 text-muted-foreground hover:border-destructive/50 hover:text-destructive transition-colors"
                  >
                    Cancel
                  </button>
                )}

                {/* Draw / Redraw button */}
                {(!job || job.status.status === "failed" || job.status.status === "done") && (
                  <button
                    onClick={() => setDrawingIndex(idx)}
                    disabled={isSub}
                    className="flex items-center gap-1 text-[10px] px-2 py-1.5 rounded border border-border/40 text-muted-foreground hover:border-primary/40 hover:text-primary/70 disabled:opacity-50 transition-colors"
                  >
                    {isSub ? (
                      <Loader2 className="w-3 h-3 animate-spin" />
                    ) : (
                      <Paintbrush className="w-3 h-3" />
                    )}
                    {job?.status.status === "done" ? "Redraw" : "Draw region"}
                  </button>
                )}
              </div>
            </div>
          );
        })}
      </div>

      {/* Render with inpainting */}
      <div className="pt-2 border-t border-border/30">
        <button
          onClick={handleRender}
          disabled={!canRenderWithInpaint}
          className="flex items-center gap-2 px-4 py-2.5 rounded-lg gradient-primary-btn text-primary-foreground font-semibold disabled:opacity-50 transition-colors"
        >
          <PlayCircle className="w-4 h-4" />
          {renderBusy ? "Rendering…" : `Render with inpainting (${doneJobs.length} clip${doneJobs.length !== 1 ? "s" : ""})`}
        </button>
        <p className="mt-1.5 text-[10px] text-muted-foreground">
          Segments with completed inpaint jobs will use the inpainted clip. All others render as normal.
        </p>
      </div>

      {/* Draw region modal */}
      {drawingSegment && (
        <DrawRegionModal
          open={drawingIndex !== null}
          segment={drawingSegment}
          trim={drawingTrim}
          onConfirm={(b64) => {
            if (drawingIndex !== null) handleConfirmMask(drawingIndex, b64);
          }}
          onClose={() => setDrawingIndex(null)}
        />
      )}
    </div>
  );
};

export default InpaintTab;
