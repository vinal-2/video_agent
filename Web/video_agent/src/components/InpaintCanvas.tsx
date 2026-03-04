import { useCallback, useEffect, useRef, useState } from "react";
import { RotateCcw, Eye, EyeOff, Minus, Plus } from "lucide-react";

// ── InpaintCanvas ──────────────────────────────────────────────────────────────
//
// Freehand mask painter. White strokes on transparent canvas = removal region.
// Exports a black-background / white-strokes PNG as base64 for the inpaint API.

interface InpaintCanvasProps {
  videoPath: string;
  midTime: number;
  onConfirm: (maskB64: string) => void;
  onCancel: () => void;
}

const MAX_UNDO_STEPS = 20;

const InpaintCanvas = ({ videoPath, midTime, onConfirm, onCancel }: InpaintCanvasProps) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const canvasRef    = useRef<HTMLCanvasElement>(null);
  const paintingRef  = useRef(false);
  const historyRef   = useRef<ImageData[]>([]);  // undo stack

  const [brushSize, setBrushSize]   = useState(30);
  const [hasStrokes, setHasStrokes] = useState(false);
  const [showMask, setShowMask]     = useState(true);    // before/after toggle
  const [nativeSize, setNativeSize] = useState<{ w: number; h: number } | null>(null);

  const fileName = videoPath.split(/[/\\]/).pop() ?? "";
  const videoUrl = `/video/${encodeURIComponent(fileName)}#t=${midTime.toFixed(2)}`;

  // Canvas display dimensions — fit container, maintain video AR
  const displayW = containerRef.current?.clientWidth ?? 480;
  const displayH = nativeSize ? Math.round(displayW * nativeSize.h / nativeSize.w) : Math.round(displayW * 16 / 9);

  // ── Canvas coordinate helpers ──────────────────────────────────────────────

  const getPos = (e: React.MouseEvent<HTMLCanvasElement>): { x: number; y: number } => {
    const canvas = canvasRef.current;
    if (!canvas) return { x: 0, y: 0 };
    const rect   = canvas.getBoundingClientRect();
    const scaleX = canvas.width  / rect.width;
    const scaleY = canvas.height / rect.height;
    return {
      x: (e.clientX - rect.left) * scaleX,
      y: (e.clientY - rect.top)  * scaleY,
    };
  };

  // Save current canvas state to undo stack
  const saveSnapshot = useCallback(() => {
    const canvas = canvasRef.current;
    const ctx    = canvas?.getContext("2d");
    if (!canvas || !ctx) return;
    const snap = ctx.getImageData(0, 0, canvas.width, canvas.height);
    historyRef.current = [...historyRef.current.slice(-MAX_UNDO_STEPS + 1), snap];
  }, []);

  // ── Paint handlers ─────────────────────────────────────────────────────────

  const startPaint = (e: React.MouseEvent<HTMLCanvasElement>) => {
    e.preventDefault();
    paintingRef.current = true;
    saveSnapshot();
    const canvas = canvasRef.current;
    const ctx    = canvas?.getContext("2d");
    if (!ctx) return;
    const { x, y } = getPos(e);
    ctx.beginPath();
    ctx.moveTo(x, y);
    // Draw a dot on click
    ctx.arc(x, y, brushSize / 2, 0, Math.PI * 2);
    ctx.fillStyle = "rgba(255,80,80,0.65)";
    ctx.fill();
    setHasStrokes(true);
  };

  const paint = (e: React.MouseEvent<HTMLCanvasElement>) => {
    if (!paintingRef.current) return;
    const canvas = canvasRef.current;
    const ctx    = canvas?.getContext("2d");
    if (!ctx) return;
    const { x, y } = getPos(e);
    ctx.lineTo(x, y);
    ctx.strokeStyle = "rgba(255,80,80,0.65)";
    ctx.lineWidth   = brushSize;
    ctx.lineCap     = "round";
    ctx.lineJoin    = "round";
    ctx.stroke();
    setHasStrokes(true);
  };

  const stopPaint = () => { paintingRef.current = false; };

  // ── Undo (Ctrl+Z) ──────────────────────────────────────────────────────────

  const undo = useCallback(() => {
    const canvas = canvasRef.current;
    const ctx    = canvas?.getContext("2d");
    if (!canvas || !ctx) return;
    if (historyRef.current.length === 0) {
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      setHasStrokes(false);
      return;
    }
    const prev = historyRef.current[historyRef.current.length - 1];
    historyRef.current = historyRef.current.slice(0, -1);
    ctx.putImageData(prev, 0, 0);
    const blank = ctx.getImageData(0, 0, canvas.width, canvas.height);
    const hasAny = blank.data.some((v, i) => (i + 1) % 4 === 0 && v > 0);
    setHasStrokes(hasAny);
  }, []);

  // ── Keyboard shortcut ──────────────────────────────────────────────────────

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === "z") { e.preventDefault(); undo(); }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [undo]);

  // ── Clear canvas ───────────────────────────────────────────────────────────

  const clearCanvas = () => {
    if (!window.confirm("Clear all strokes?")) return;
    const canvas = canvasRef.current;
    const ctx    = canvas?.getContext("2d");
    if (!ctx || !canvas) return;
    historyRef.current = [];
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    setHasStrokes(false);
  };

  // ── Export mask ────────────────────────────────────────────────────────────

  const exportMask = () => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const offscreen = document.createElement("canvas");
    offscreen.width  = canvas.width;
    offscreen.height = canvas.height;
    const ctx2 = offscreen.getContext("2d")!;
    // Black background
    ctx2.fillStyle = "black";
    ctx2.fillRect(0, 0, offscreen.width, offscreen.height);
    // Composite strokes as white (source pixels where alpha>0 become white)
    const src = canvas.getContext("2d")!.getImageData(0, 0, canvas.width, canvas.height);
    for (let i = 0; i < src.data.length; i += 4) {
      if (src.data[i + 3] > 0) {
        src.data[i]     = 255;
        src.data[i + 1] = 255;
        src.data[i + 2] = 255;
        src.data[i + 3] = 255;
      }
    }
    ctx2.putImageData(src, 0, 0);
    const b64 = offscreen.toDataURL("image/png").split(",")[1];
    onConfirm(b64);
  };

  return (
    <div className="space-y-3" ref={containerRef}>
      {/* Toolbar */}
      <div className="flex items-center gap-3 flex-wrap">
        {/* Brush size */}
        <div className="flex items-center gap-2">
          <button
            onClick={() => setBrushSize((s) => Math.max(5, s - 5))}
            className="w-6 h-6 rounded flex items-center justify-center transition-colors"
            style={{ background: "var(--bg-tertiary)", border: "1px solid var(--border-subtle)", color: "var(--text-secondary)" }}
          >
            <Minus className="w-3 h-3" />
          </button>
          <span style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--text-secondary)", minWidth: 36, textAlign: "center" }}>
            {brushSize}px
          </span>
          <button
            onClick={() => setBrushSize((s) => Math.min(80, s + 5))}
            className="w-6 h-6 rounded flex items-center justify-center transition-colors"
            style={{ background: "var(--bg-tertiary)", border: "1px solid var(--border-subtle)", color: "var(--text-secondary)" }}
          >
            <Plus className="w-3 h-3" />
          </button>
        </div>

        {/* Brush slider */}
        <input
          type="range"
          min={5}
          max={80}
          value={brushSize}
          onChange={(e) => setBrushSize(Number(e.target.value))}
          className="flex-1"
          style={{ accentColor: "var(--accent-primary)", maxWidth: 120 }}
        />

        {/* Undo */}
        <button
          onClick={undo}
          title="Undo (Ctrl+Z)"
          className="flex items-center gap-1 px-2 py-1.5 rounded text-xs transition-colors"
          style={{ background: "var(--bg-tertiary)", border: "1px solid var(--border-subtle)", color: "var(--text-secondary)" }}
        >
          <RotateCcw className="w-3 h-3" />
          Undo
        </button>

        {/* Clear */}
        <button
          onClick={clearCanvas}
          className="flex items-center gap-1 px-2 py-1.5 rounded text-xs transition-colors"
          style={{ background: "var(--bg-tertiary)", border: "1px solid var(--border-subtle)", color: "var(--text-secondary)" }}
        >
          Clear
        </button>

        {/* Before/After toggle */}
        <button
          onClick={() => setShowMask((v) => !v)}
          className="flex items-center gap-1 px-2 py-1.5 rounded text-xs transition-colors ml-auto"
          style={{ background: "var(--bg-tertiary)", border: "1px solid var(--border-subtle)", color: showMask ? "var(--accent-primary)" : "var(--text-secondary)" }}
        >
          {showMask ? <Eye className="w-3 h-3" /> : <EyeOff className="w-3 h-3" />}
          {showMask ? "Mask on" : "Original"}
        </button>
      </div>

      {/* Canvas */}
      <div
        className="relative overflow-hidden rounded"
        style={{ width: "100%", height: displayH, background: "#000" }}
      >
        {/* Background video frame */}
        <video
          src={videoUrl}
          className="absolute inset-0 w-full h-full object-contain pointer-events-none"
          preload="metadata"
          muted
          playsInline
          onLoadedMetadata={(e) => {
            const v = e.currentTarget;
            if (v.videoWidth && v.videoHeight) {
              setNativeSize({ w: v.videoWidth, h: v.videoHeight });
            }
          }}
        />

        {/* Drawing canvas */}
        <canvas
          ref={canvasRef}
          width={nativeSize?.w ?? 640}
          height={nativeSize?.h ?? Math.round((nativeSize?.w ?? 640) * 16 / 9)}
          className="absolute inset-0 w-full h-full cursor-crosshair"
          style={{ opacity: showMask ? 1 : 0, transition: "opacity 150ms" }}
          onMouseDown={startPaint}
          onMouseMove={paint}
          onMouseUp={stopPaint}
          onMouseLeave={stopPaint}
        />
      </div>

      {/* Actions */}
      <div className="flex items-center justify-end gap-2">
        <button
          onClick={onCancel}
          className="px-3 py-2 rounded text-xs transition-colors"
          style={{ background: "var(--bg-tertiary)", border: "1px solid var(--border-subtle)", color: "var(--text-secondary)" }}
        >
          Cancel
        </button>
        <button
          onClick={exportMask}
          disabled={!hasStrokes}
          className="flex items-center gap-1.5 px-4 py-2 rounded text-xs font-semibold gradient-primary-btn disabled:opacity-50"
          style={{ color: "var(--bg-primary)", fontFamily: "var(--font-display)" }}
        >
          Confirm region
        </button>
      </div>
    </div>
  );
};

export default InpaintCanvas;
