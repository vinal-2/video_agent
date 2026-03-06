import { useRef, useState, useEffect, useCallback } from "react";
import { Film, Play, Pause, ChevronDown, Crop, RotateCcw, Loader2, Crosshair } from "lucide-react";
import * as SliderPrimitive from "@radix-ui/react-slider";
import { Slider } from "@/components/ui/slider";
import type { Segment, GradeSettings, TrimState, CropSettings, SamMaskSettings } from "@/lib/api";
import { fetchCropAuto, fetchSamMask } from "@/lib/api";
import type { SegmentDecision } from "@/hooks/usePipeline";

// CSS filter approximations for each LUT — live preview only.
// Actual render uses ffmpeg LUT files server-side.
const LUT_CSS: Record<string, string> = {
  none:     "",
  cinema:   "contrast(1.1) saturate(0.85) brightness(0.95)",
  golden:   "saturate(1.2) sepia(0.15)",
  cool:     "saturate(0.9) hue-rotate(15deg)",
  fade:     "contrast(0.85) saturate(0.7) brightness(1.1)",
  punch:    "contrast(1.2) saturate(1.3)",
  mono:     "grayscale(1)",
  teal_org: "saturate(1.3) hue-rotate(-15deg)",
};

const LUTS = ["none", "cinema", "golden", "cool", "fade", "punch", "mono", "teal_org"] as const;

const TRANSITIONS: Array<{ value: string; label: string; group: string }> = [
  { value: "cut",         label: "Cut",         group: "Cut"    },
  { value: "jump_cut",    label: "Jump cut",    group: "Cut"    },
  { value: "flash_white", label: "Flash white", group: "Insert" },
  { value: "flash_black", label: "Flash black", group: "Insert" },
  { value: "dip_black",   label: "Dip black",   group: "Insert" },
  { value: "dip_white",   label: "Dip white",   group: "Insert" },
  { value: "dissolve",    label: "Dissolve",    group: "Blend"  },
  { value: "fade_black",  label: "Fade black",  group: "Blend"  },
  { value: "wipe_up",     label: "Wipe up",     group: "Blend"  },
  { value: "wipe_left",   label: "Wipe left",   group: "Blend"  },
  { value: "zoom_in",     label: "Zoom in",     group: "Blend"  },
  { value: "zoom_out",    label: "Zoom out",    group: "Blend"  },
  { value: "push_up",     label: "Push up",     group: "Blend"  },
  { value: "slide_left",  label: "Slide left",  group: "Blend"  },
];

const GRADE_SLIDERS: Array<{ key: keyof Omit<GradeSettings, "lut">; label: string }> = [
  { key: "brightness", label: "Brightness" },
  { key: "contrast",   label: "Contrast"   },
  { key: "saturation", label: "Saturation" },
  { key: "temp",       label: "Temp"       },
];

function buildCssFilter(grade: GradeSettings): string {
  const parts: string[] = [];
  if (grade.brightness !== 0) parts.push(`brightness(${(1 + grade.brightness / 100).toFixed(2)})`);
  if (grade.contrast   !== 0) parts.push(`contrast(${(1 + grade.contrast   / 100).toFixed(2)})`);
  if (grade.saturation !== 0) parts.push(`saturate(${(1 + grade.saturation / 100).toFixed(2)})`);
  if (grade.temp > 0) parts.push(`sepia(${(grade.temp / 100).toFixed(2)})`);
  else if (grade.temp < 0) parts.push(`hue-rotate(${(grade.temp * 2).toFixed(0)}deg)`); // negative → blue shift
  // LUT: split multi-function strings so they don't stack as a single token
  const lutCss = LUT_CSS[grade.lut] ?? "";
  if (lutCss) {
    // Each LUT string may contain multiple filter functions; split on ") " boundary
    lutCss.split(/(?<=\))\s+/).forEach((f) => { if (f.trim()) parts.push(f.trim()); });
  }
  return parts.join(" ");
}

// ── Transition preview ────────────────────────────────────────────────────────

const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

type TransitionAnim = {
  dur: number;         // transition duration ms
  overlay?: string;   // flash/dip overlay colour
  // [initial style (before playing), final style (end of animation)]
  prevAnim: [React.CSSProperties, React.CSSProperties];
  currAnim: [React.CSSProperties, React.CSSProperties];
};

// Each entry: initial styles are applied before prev plays (no CSS transition).
// On switch, final styles are applied WITH CSS transition so the browser animates.
// Both videos are stacked (position: absolute) in one container — required for
// wipe/push/slide effects where clips overlap during the transition.
const EFFECTS: Record<string, TransitionAnim> = {
  cut:         { dur: 40,  prevAnim: [{ opacity: 1 }, { opacity: 1 }],                                                    currAnim: [{ opacity: 0 }, { opacity: 1 }] },
  jump_cut:    { dur: 40,  prevAnim: [{ opacity: 1 }, { opacity: 1 }],                                                    currAnim: [{ opacity: 0 }, { opacity: 1 }] },
  dissolve:    { dur: 300, prevAnim: [{ opacity: 1 }, { opacity: 0 }],                                                    currAnim: [{ opacity: 0 }, { opacity: 1 }] },
  fade_black:  { dur: 400, overlay: "#000", prevAnim: [{ opacity: 1 }, { opacity: 0 }],                                   currAnim: [{ opacity: 0 }, { opacity: 1 }] },
  dip_black:   { dur: 500, overlay: "#000", prevAnim: [{ opacity: 1 }, { opacity: 0 }],                                   currAnim: [{ opacity: 0 }, { opacity: 1 }] },
  dip_white:   { dur: 500, overlay: "#fff", prevAnim: [{ opacity: 1 }, { opacity: 0 }],                                   currAnim: [{ opacity: 0 }, { opacity: 1 }] },
  flash_white: { dur: 100, overlay: "#fff", prevAnim: [{ opacity: 1 }, { opacity: 0 }],                                   currAnim: [{ opacity: 0 }, { opacity: 1 }] },
  flash_black: { dur: 100, overlay: "#000", prevAnim: [{ opacity: 1 }, { opacity: 0 }],                                   currAnim: [{ opacity: 0 }, { opacity: 1 }] },
  // Wipe: curr reveals over prev using clip-path (100% inset = hidden, 0% = fully visible)
  wipe_up:     { dur: 200, prevAnim: [{ opacity: 1 }, { opacity: 1 }],                                                    currAnim: [{ opacity: 1, clipPath: "inset(100% 0 0 0)" }, { opacity: 1, clipPath: "inset(0% 0 0 0)" }] },
  wipe_left:   { dur: 200, prevAnim: [{ opacity: 1 }, { opacity: 1 }],                                                    currAnim: [{ opacity: 1, clipPath: "inset(0 0 0 100%)" }, { opacity: 1, clipPath: "inset(0 0 0 0%)" }] },
  // Zoom: curr scales in over prev
  zoom_in:     { dur: 300, prevAnim: [{ opacity: 1 }, { opacity: 0 }],                                                    currAnim: [{ opacity: 0, transform: "scale(1.3)" }, { opacity: 1, transform: "scale(1)" }] },
  zoom_out:    { dur: 300, prevAnim: [{ opacity: 1, transform: "scale(1)" }, { opacity: 0, transform: "scale(0.8)" }],    currAnim: [{ opacity: 0 }, { opacity: 1 }] },
  // Push/slide: both clips translate together (overflow:hidden clips them)
  push_up:     { dur: 250, prevAnim: [{ opacity: 1, transform: "translateY(0%)" }, { opacity: 1, transform: "translateY(-100%)" }],   currAnim: [{ opacity: 1, transform: "translateY(100%)" }, { opacity: 1, transform: "translateY(0%)" }] },
  slide_left:  { dur: 250, prevAnim: [{ opacity: 1, transform: "translateX(0%)" }, { opacity: 1, transform: "translateX(-100%)" }],   currAnim: [{ opacity: 1, transform: "translateX(100%)" }, { opacity: 1, transform: "translateX(0%)" }] },
};

const TransitionPreview = ({
  prevVideoUrl,
  prevTrimEnd,
  currVideoUrl,
  currTrimStart,
  transition,
}: {
  prevVideoUrl: string;
  prevTrimEnd: number;
  currVideoUrl: string;
  currTrimStart: number;
  transition: string;
}) => {
  const prevRef    = useRef<HTMLVideoElement>(null);
  const currRef    = useRef<HTMLVideoElement>(null);
  const runningRef = useRef(false);
  const WINDOW     = 1.5;

  const [prevStyle, setPrevStyle] = useState<React.CSSProperties>({ opacity: 1 });
  const [currStyle, setCurrStyle] = useState<React.CSSProperties>({ opacity: 0 });
  const [overlayOp, setOverlayOp] = useState(0);
  const [active, setActive]       = useState(false);

  // Seek thumbnails to preview positions whenever key values change
  useEffect(() => {
    if (runningRef.current) return;
    const prev = prevRef.current;
    const curr = currRef.current;
    if (prev) prev.currentTime = Math.max(0, prevTrimEnd - WINDOW);
    if (curr) curr.currentTime = currTrimStart;
    setPrevStyle({ opacity: 1 });
    setCurrStyle({ opacity: 0 });
    setOverlayOp(0);
  }, [transition, prevTrimEnd, currTrimStart]);

  useEffect(() => () => { runningRef.current = false; }, []);

  const startPreview = useCallback(() => {
    const prev = prevRef.current;
    const curr = currRef.current;
    if (!prev || !curr || runningRef.current) return;
    runningRef.current = true;
    setActive(true);

    const effect = EFFECTS[transition] ?? EFFECTS.cut;

    const run = async () => {
      // Apply initial styles (no CSS transition — instant snap to position)
      setPrevStyle({ ...effect.prevAnim[0] });
      setCurrStyle({ ...effect.currAnim[0] });
      setOverlayOp(0);

      // Seek and play prev clip
      prev.currentTime = Math.max(0, prevTrimEnd - WINDOW);
      curr.currentTime = currTrimStart;
      await prev.play().catch(() => {});
      await sleep(WINDOW * 1000);
      prev.pause();

      // Two animation frames ensure React rendered initial styles before
      // adding CSS transition — otherwise the browser skips the animation
      await new Promise<void>((r) => requestAnimationFrame(() => requestAnimationFrame(r)));

      // Apply final styles WITH CSS transition → browser animates
      const css = `all ${effect.dur}ms ease`;
      setPrevStyle({ ...effect.prevAnim[1], transition: css });
      setCurrStyle({ ...effect.currAnim[1], transition: css });

      // Flash/dip overlay
      if (effect.overlay) {
        setOverlayOp(0.92);
        setTimeout(() => setOverlayOp(0), effect.dur * 0.4);
      }

      await sleep(effect.dur + 30);

      // Play curr clip (remove transition prop first so future style changes are instant)
      curr.currentTime = currTrimStart;
      setCurrStyle({ ...effect.currAnim[1] });
      await curr.play().catch(() => {});
      await sleep(WINDOW * 1000);
      curr.pause();

      // Reset to thumbnail state
      setPrevStyle({ opacity: 1 });
      setCurrStyle({ opacity: 0 });
      setOverlayOp(0);
      prev.currentTime = Math.max(0, prevTrimEnd - WINDOW);
      curr.currentTime = currTrimStart;
      runningRef.current = false;
      setActive(false);
    };

    run();
  }, [transition, prevTrimEnd, currTrimStart]);

  const effect = EFFECTS[transition] ?? EFFECTS.cut;

  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between">
        <p className="text-xs text-muted-foreground">Preview cut</p>
        <button
          onClick={startPreview}
          disabled={active}
          className="text-[10px] px-2 py-1 rounded border border-border/40 text-muted-foreground hover:border-primary/40 hover:text-primary/70 disabled:opacity-50 transition-colors"
        >
          {active ? "Playing…" : "▶ Preview"}
        </button>
      </div>
      {/* Single stacked container — both clips overlaid so wipe/push/dissolve render accurately */}
      <div className="flex justify-center">
        <div
          className="relative rounded overflow-hidden bg-black flex-shrink-0"
          style={{ width: "66px", aspectRatio: "9/16" }}
        >
          <video
            ref={prevRef}
            src={prevVideoUrl}
            className="absolute inset-0 w-full h-full object-contain"
            style={{ ...prevStyle, zIndex: 1 }}
            preload="metadata"
            muted
            playsInline
          />
          <video
            ref={currRef}
            src={currVideoUrl}
            className="absolute inset-0 w-full h-full object-contain"
            style={{ ...currStyle, zIndex: 2 }}
            preload="metadata"
            muted
            playsInline
          />
          {effect.overlay && (
            <div
              className="absolute inset-0 pointer-events-none"
              style={{
                background: effect.overlay,
                opacity: overlayOp,
                transition: `opacity ${effect.dur * 0.4}ms ease`,
                zIndex: 10,
              }}
            />
          )}
        </div>
      </div>
      <p className="text-center text-[9px] font-mono text-primary/60">
        {transition.replace(/_/g, " ")}
      </p>
    </div>
  );
};

// ── Crop tool ─────────────────────────────────────────────────────────────────
//
// Shows a thumbnail of the segment's midframe with a draggable 9:16 crop box.
// Only rendered when source is wider than 9:16 (landscape / square footage).
// Overlay: dark mask covers the excluded regions; the box is draggable left/right.

interface CropToolProps {
  segment: Segment;
  trim: TrimState;
  crop: CropSettings | null;
  onCropChange: (crop: CropSettings | null) => void;
}

const CropTool = ({ segment, trim, crop, onCropChange }: CropToolProps) => {
  // All hooks must be declared unconditionally — early return is AFTER these.
  const containerRef = useRef<HTMLDivElement>(null);
  const dragStartRef = useRef<{ pointerX: number; cropX: number } | null>(null);
  const [loading, setLoading]   = useState(false);
  const [localX, setLocalX]     = useState<number | null>(null);

  const fileName   = segment.video_path?.split(/[/\\]/).pop() ?? "";
  const midTime    = (trim.start + trim.end) / 2;
  const thumbUrl   = `/video/${encodeURIComponent(fileName)}#t=${midTime.toFixed(2)}`;

  const sourceW = crop?.source_w ?? 0;
  const sourceH = crop?.source_h ?? 0;
  const isWider = sourceW > 0 && sourceH > 0 && sourceW / sourceH > 9 / 16 + 0.01;

  // ── Auto-detect ──────────────────────────────────────────────────────────
  const handleAutoDetect = useCallback(async () => {
    if (!fileName) return;
    setLoading(true);
    try {
      const result = await fetchCropAuto(fileName, trim.start, trim.end);
      onCropChange(result);
      setLocalX(null);
    } catch (err) {
      console.error("crop_auto failed", err);
    } finally {
      setLoading(false);
    }
  }, [fileName, trim.start, trim.end, onCropChange]);

  // Trigger auto-detect on first expand when source dimensions are unknown.
  // The effect runs once (empty deps) — the ref guards against repeat calls.
  const autoDetectRunRef = useRef(false);
  useEffect(() => {
    if (!crop && !autoDetectRunRef.current && fileName) {
      autoDetectRunRef.current = true;
      handleAutoDetect();
    }
  }, [crop, fileName, handleAutoDetect]);

  // Don't render at all for already-9:16 footage (after hooks)
  if (!isWider && !loading && !crop) return null;

  // ── Drag logic ───────────────────────────────────────────────────────────

  const onPointerDown = (e: React.PointerEvent) => {
    if (!crop || !containerRef.current) return;
    e.preventDefault();
    e.currentTarget.setPointerCapture(e.pointerId);
    dragStartRef.current = { pointerX: e.clientX, cropX: crop.x };
  };

  const onPointerMove = (e: React.PointerEvent) => {
    if (!dragStartRef.current || !crop || !containerRef.current) return;
    const rect       = containerRef.current.getBoundingClientRect();
    const thumbW     = rect.width;
    const scaleX     = crop.source_w / thumbW; // source pixels per thumbnail pixel
    const deltaSource = (e.clientX - dragStartRef.current.pointerX) * scaleX;
    const newX       = Math.max(0, Math.min(
      Math.round(dragStartRef.current.cropX + deltaSource),
      crop.source_w - crop.w,
    ));
    // Update visual immediately, commit to parent
    setLocalX(newX);
    onCropChange({ ...crop, x: newX, auto: false });
  };

  const onPointerUp = () => {
    dragStartRef.current = null;
    setLocalX(null);
  };

  // ── Render ───────────────────────────────────────────────────────────────
  const displayCrop = crop ? { ...crop, x: localX ?? crop.x } : null;

  // Calculate box position as % of source width for the overlay
  const boxLeft  = displayCrop && displayCrop.source_w
    ? (displayCrop.x / displayCrop.source_w) * 100
    : null;
  const boxRight = displayCrop && displayCrop.source_w
    ? ((displayCrop.source_w - displayCrop.x - displayCrop.w) / displayCrop.source_w) * 100
    : null;

  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-1.5">
          <Crop className="w-3.5 h-3.5 text-muted-foreground" />
          <p className="text-xs text-muted-foreground">Crop / Reframe</p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={handleAutoDetect}
            disabled={loading}
            className="flex items-center gap-1 text-[10px] px-2 py-1 rounded border border-border/40 text-muted-foreground hover:border-primary/40 hover:text-primary/70 disabled:opacity-50 transition-colors"
          >
            {loading ? <Loader2 className="w-3 h-3 animate-spin" /> : null}
            Auto-detect
          </button>
          {crop && (
            <button
              onClick={() => onCropChange(null)}
              className="flex items-center gap-1 text-[10px] px-2 py-1 rounded border border-border/40 text-muted-foreground hover:border-destructive/50 hover:text-destructive transition-colors"
              title="Remove crop (render will auto-centre)"
            >
              <RotateCcw className="w-3 h-3" />
              Reset
            </button>
          )}
        </div>
      </div>

      {/* Thumbnail with crop overlay */}
      <div
        ref={containerRef}
        className="relative overflow-hidden rounded bg-black select-none"
        style={{ aspectRatio: `${displayCrop?.source_w ?? 16} / ${displayCrop?.source_h ?? 9}` }}
      >
        {/* Segment midframe thumbnail */}
        <video
          src={thumbUrl}
          className="w-full h-full object-contain pointer-events-none"
          preload="metadata"
          muted
          playsInline
        />

        {/* Dark mask — left of crop box */}
        {boxLeft !== null && (
          <div
            className="absolute inset-y-0 left-0 bg-black/60 pointer-events-none"
            style={{ width: `${boxLeft}%` }}
          />
        )}

        {/* Dark mask — right of crop box */}
        {boxRight !== null && (
          <div
            className="absolute inset-y-0 right-0 bg-black/60 pointer-events-none"
            style={{ width: `${boxRight}%` }}
          />
        )}

        {/* Crop box border + drag handle */}
        {boxLeft !== null && boxRight !== null && (
          <div
            className="absolute inset-y-0 border-2 border-primary/80 cursor-ew-resize"
            style={{ left: `${boxLeft}%`, right: `${boxRight}%` }}
            onPointerDown={onPointerDown}
            onPointerMove={onPointerMove}
            onPointerUp={onPointerUp}
          >
            {/* Left drag handle indicator */}
            <div className="absolute left-0 inset-y-0 w-4 flex items-center justify-center">
              <div className="w-1 h-8 rounded-full bg-primary/80" />
            </div>
            {/* Right drag handle indicator */}
            <div className="absolute right-0 inset-y-0 w-4 flex items-center justify-center">
              <div className="w-1 h-8 rounded-full bg-primary/80" />
            </div>
          </div>
        )}

        {loading && (
          <div className="absolute inset-0 flex items-center justify-center bg-black/50">
            <Loader2 className="w-6 h-6 text-primary animate-spin" />
          </div>
        )}
      </div>

      {/* Crop coordinates readout */}
      {displayCrop && (
        <p className="text-[10px] font-mono text-muted-foreground">
          Crop: x={displayCrop.x} · {displayCrop.w}×{displayCrop.h} from {displayCrop.source_w}×{displayCrop.source_h}
          {displayCrop.auto && <span className="ml-1 text-primary/60">(auto)</span>}
        </p>
      )}
    </div>
  );
};

// ── SAM subject-isolation tool ────────────────────────────────────────────────
//
// Click on the video thumbnail to place a SAM point prompt.
// The response is a base64 PNG mask which is rendered as a semi-transparent
// lime overlay using CSS mask-image (luminance mode).
// "Split grade" toggle: when enabled, background gets a desaturated/dimmed
// treatment during render; when disabled, the mask exists but only the full
// grade is applied uniformly.

interface SamToolProps {
  segment: Segment;
  trim: TrimState;
  sam: SamMaskSettings | null;
  onSamChange: (sam: SamMaskSettings | null) => void;
}

const SamTool = ({ segment, trim, sam, onSamChange }: SamToolProps) => {
  const containerRef  = useRef<HTMLDivElement>(null);
  const videoRef      = useRef<HTMLVideoElement>(null);
  const [loading, setLoading]           = useState(false);
  const [dotPercent, setDotPercent]     = useState<{ x: number; y: number } | null>(null);

  const fileName = segment.video_path?.split(/[/\\]/).pop() ?? "";
  const midTime  = (trim.start + trim.end) / 2;
  const videoUrl = `/video/${encodeURIComponent(fileName)}`;

  // Seek the thumbnail to mid-point when metadata loads
  useEffect(() => {
    const video = videoRef.current;
    if (!video || !fileName) return;
    const seek = () => { video.currentTime = midTime; };
    if (video.readyState >= 1) {
      seek();
    } else {
      video.addEventListener("loadedmetadata", seek, { once: true });
    }
  }, [fileName, midTime]);

  // Sync dot position with the current mask's prompt point
  useEffect(() => {
    if (sam) {
      setDotPercent({ x: sam.point_x * 100, y: sam.point_y * 100 });
    } else {
      setDotPercent(null);
    }
  }, [sam]);

  const handleClick = useCallback(async (e: React.MouseEvent<HTMLDivElement>) => {
    if (!containerRef.current || loading) return;
    const rect     = containerRef.current.getBoundingClientRect();
    const cx       = e.clientX - rect.left;
    const cy       = e.clientY - rect.top;
    const contW    = rect.width;
    const contH    = rect.height;

    // Compute click fractions accounting for object-contain letterboxing
    const vW = videoRef.current?.videoWidth  || contW;
    const vH = videoRef.current?.videoHeight || contH;
    const contRatio  = contW / contH;
    const videoRatio = vW / vH;
    let rendW = contW, rendH = contH, offX = 0, offY = 0;
    if (videoRatio > contRatio) {
      rendW = contW;   rendH = contW / videoRatio;
      offY  = (contH - rendH) / 2;
    } else {
      rendH = contH;   rendW = contH * videoRatio;
      offX  = (contW - rendW) / 2;
    }
    const point_x = Math.max(0, Math.min(1, (cx - offX) / rendW));
    const point_y = Math.max(0, Math.min(1, (cy - offY) / rendH));

    // Place dot at raw container fraction for immediate visual feedback
    setDotPercent({ x: (cx / contW) * 100, y: (cy / contH) * 100 });

    setLoading(true);
    try {
      const result = await fetchSamMask(fileName, midTime, point_x, point_y);
      onSamChange(result);
    } catch (err) {
      console.error("sam_mask failed", err);
      setDotPercent(null);
    } finally {
      setLoading(false);
    }
  }, [loading, fileName, midTime, onSamChange]);

  const maskDataUrl = sam ? `data:image/png;base64,${sam.mask_b64}` : null;

  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-1.5">
          <Crosshair className="w-3.5 h-3.5 text-muted-foreground" />
          <p className="text-xs text-muted-foreground">Subject Mask</p>
        </div>
        <div className="flex items-center gap-2">
          {sam && (
            <button
              onClick={() => onSamChange({ ...sam, enabled: !sam.enabled })}
              className={`text-[10px] px-2 py-1 rounded border transition-colors ${
                sam.enabled
                  ? "border-primary/50 text-primary bg-primary/10"
                  : "border-border/40 text-muted-foreground hover:border-primary/40 hover:text-primary/70"
              }`}
            >
              Split grade
            </button>
          )}
          {sam && (
            <button
              onClick={() => { onSamChange(null); setDotPercent(null); }}
              className="flex items-center gap-1 text-[10px] px-2 py-1 rounded border border-border/40 text-muted-foreground hover:border-destructive/50 hover:text-destructive transition-colors"
            >
              <RotateCcw className="w-3 h-3" />
              Clear
            </button>
          )}
        </div>
      </div>

      {/* Click-to-segment thumbnail */}
      <div
        ref={containerRef}
        className="relative overflow-hidden rounded bg-black select-none cursor-crosshair"
        style={{ aspectRatio: "9/16", maxHeight: "200px" }}
        onClick={handleClick}
      >
        <video
          ref={videoRef}
          src={videoUrl}
          className="w-full h-full object-contain pointer-events-none"
          preload="metadata"
          muted
          playsInline
        />

        {/* Lime-green mask overlay — luminance mask so white=show, black=hide */}
        {maskDataUrl && (
          <div
            className="absolute inset-0 pointer-events-none"
            style={{
              backgroundColor: "hsl(82 80% 52% / 0.4)",
              maskImage: `url("${maskDataUrl}")`,
              WebkitMaskImage: `url("${maskDataUrl}")`,
              maskMode: "luminance" as React.CSSProperties["maskMode"],
              WebkitMaskMode: "luminance",
              maskSize: "contain",
              WebkitMaskSize: "contain",
              maskRepeat: "no-repeat",
              WebkitMaskRepeat: "no-repeat",
              maskPosition: "center",
              WebkitMaskPosition: "center",
            } as React.CSSProperties}
          />
        )}

        {/* Click-point indicator dot */}
        {dotPercent && !loading && (
          <div
            className="absolute w-3.5 h-3.5 pointer-events-none"
            style={{
              left: `${dotPercent.x}%`,
              top: `${dotPercent.y}%`,
              transform: "translate(-50%, -50%)",
            }}
          >
            <div className="w-full h-full rounded-full border-2 border-primary bg-primary/50 shadow-sm" />
          </div>
        )}

        {loading && (
          <div className="absolute inset-0 flex items-center justify-center bg-black/50">
            <Loader2 className="w-6 h-6 text-primary animate-spin" />
          </div>
        )}
      </div>

      <p className="text-[10px] font-mono text-muted-foreground">
        {sam
          ? `Mask: ${sam.width}×${sam.height} · split grade ${sam.enabled ? "on" : "off"}`
          : "Click on the subject to isolate it"}
      </p>
    </div>
  );
};

// ── SegmentCard ───────────────────────────────────────────────────────────────

interface SegmentCardProps {
  segment: Segment;
  index: number;
  decision: SegmentDecision | undefined;  // undefined = not yet decided (neutral)
  trim: TrimState;
  grade: GradeSettings;
  crop: CropSettings | null;
  sam: SamMaskSettings | null;
  transition: string;
  prevSegment?: Segment | null;
  prevTrim?: TrimState | null;
  onDecision: (decision: SegmentDecision) => void;
  onTrimChange: (start: number, end: number) => void;
  onGradeChange: (grade: GradeSettings) => void;
  onCropChange: (crop: CropSettings | null) => void;
  onSamChange: (sam: SamMaskSettings | null) => void;
  onTransitionChange: (transition: string) => void;
  isExpanded: boolean;
  onExpand: () => void;
  onCollapse: () => void;
  checked?: boolean;
  onCheck?: () => void;
}

const SegmentCard = ({
  segment,
  index,
  decision,
  trim,
  grade,
  crop,
  sam,
  transition,
  prevSegment,
  prevTrim,
  onDecision,
  onTrimChange,
  onGradeChange,
  onCropChange,
  onSamChange,
  onTransitionChange,
  isExpanded,
  onExpand,
  onCollapse,
  checked,
  onCheck,
}: SegmentCardProps) => {
  const videoRef = useRef<HTMLVideoElement>(null);
  const [playing, setPlaying]         = useState(false);
  const [currentTime, setCurrentTime] = useState(trim.start);
  const [scoreVisible, setScoreVisible] = useState(false);

  // Animate score bars from 0 → value on mount
  useEffect(() => {
    const id = setTimeout(() => setScoreVisible(true), 60);
    return () => clearTimeout(id);
  }, []);

  const fileName     = segment.video_path?.split(/[/\\]/).pop() ?? "Unknown clip";
  const tags         = (segment.combined_tags ?? segment.tags ?? []) as string[];
  const rawDur       = segment.end - segment.start;
  const trimDur      = trim.end - trim.start;
  const styleScore   = typeof segment.style_score === "number" ? segment.style_score : null;
  const videoUrl     = `/video/${encodeURIComponent(fileName)}`;
  const cssFilter    = buildCssFilter(grade);
  const prevFileName = prevSegment?.video_path?.split(/[/\\]/).pop();
  const prevVideoUrl = prevFileName ? `/video/${encodeURIComponent(prevFileName)}` : null;

  // Seek to trim start whenever card is opened
  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;
    if (isExpanded) {
      video.currentTime = trim.start;
      setCurrentTime(trim.start);
      setPlaying(false);
    } else {
      video.pause();
      setPlaying(false);
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isExpanded]);

  // Auto-pause when playhead hits trim end
  const handleTimeUpdate = useCallback(() => {
    const video = videoRef.current;
    if (!video) return;
    setCurrentTime(video.currentTime);
    if (video.currentTime >= trim.end) {
      video.pause();
      video.currentTime = trim.end;
      setPlaying(false);
    }
  }, [trim.end]);

  const togglePlay = useCallback(() => {
    const video = videoRef.current;
    if (!video) return;
    if (playing) {
      video.pause();
    } else {
      if (video.currentTime >= trim.end) video.currentTime = trim.start;
      video.play().catch(() => {});
    }
  }, [playing, trim]);

  // Trim slider — two thumbs
  const handleTrimChange = useCallback((values: number[]) => {
    const [s, e] = values;
    const startChanged = s !== trim.start;
    onTrimChange(s, e);
    if (!playing && startChanged) {
      const video = videoRef.current;
      if (video) {
        video.currentTime = s;
        setCurrentTime(s);
      }
    }
  }, [playing, trim.start, onTrimChange]);

  const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
    switch (e.key) {
      case "a": case "A":
        e.preventDefault(); onDecision("accepted"); break;
      case "r": case "R":
        e.preventDefault(); onDecision("rejected"); break;
      case "Enter":
        e.preventDefault(); isExpanded ? onCollapse() : onExpand(); break;
      case " ":
        if (isExpanded) { e.preventDefault(); togglePlay(); } break;
      case "Escape":
        e.preventDefault(); onCollapse(); break;
    }
  }, [isExpanded, onExpand, onCollapse, togglePlay, onDecision]);

  const trimProgress = trim.end > trim.start
    ? Math.max(0, Math.min(100, ((currentTime - trim.start) / (trim.end - trim.start)) * 100))
    : 0;

  // ── Score bar data ─────────────────────────────────────────────────────────
  const templateScore  = typeof segment.template_score  === "number" ? Math.min(1, Math.max(0, segment.template_score))  : null;
  const rawAesthetic   = typeof segment.aesthetic_score === "number" ? segment.aesthetic_score : styleScore;
  const aestheticScore = typeof rawAesthetic === "number" ? Math.min(1, Math.max(0, rawAesthetic)) : null;
  const generalScore   = typeof segment.score === "number" ? Math.min(1, Math.max(0, segment.score)) : null;
  // Use template_score for bar 1 when available; fall back to aesthetic_score / style_score
  const bar1Score      = templateScore ?? aestheticScore;
  const bar1Label      = templateScore !== null ? "Template" : "Aesthetic";

  // ── Status left-border style ───────────────────────────────────────────────
  const borderLeft =
    decision === "accepted"                            ? "4px solid var(--status-accepted)"  :
    decision === "rejected"                            ? "4px solid transparent"              :
    segment.buffer                                     ? "4px solid #60a5fa"                  :
                                                         "4px solid var(--status-pending)";

  const cardOpacity = decision === "rejected" ? 0.5 : 1;

  const midTime = (trim.start + trim.end) / 2;
  const thumbUrl = `/video/${encodeURIComponent(fileName)}#t=${midTime.toFixed(2)}`;

  return (
    <div
      tabIndex={0}
      onKeyDown={handleKeyDown}
      className="segment-card focus:outline-none transition-all"
      style={{
        background: "var(--bg-secondary)",
        border: "1px solid var(--border-subtle)",
        borderLeft,
        borderRadius: 6,
        opacity: cardOpacity,
        transform: "translateY(0)",
        transition: "transform 100ms ease, opacity 200ms ease, border-color 200ms ease",
      }}
      onMouseEnter={(e) => { if (decision !== "rejected") (e.currentTarget as HTMLElement).style.transform = "translateY(-1px)"; }}
      onMouseLeave={(e) => { (e.currentTarget as HTMLElement).style.transform = "translateY(0)"; }}
    >
      {/* ── Card header ─────────────────────────────────────────────────── */}
      <div
        className="flex items-stretch gap-0 cursor-pointer select-none"
        onClick={() => isExpanded ? onCollapse() : onExpand()}
      >
        {/* Thumbnail (160×90) */}
        <div
          className="relative flex-shrink-0 overflow-hidden"
          style={{ width: 160, height: 90, background: "#000", borderRadius: "5px 0 0 5px" }}
        >
          <video
            src={thumbUrl}
            className="w-full h-full object-cover"
            preload="metadata"
            muted
            playsInline
            style={{ display: "block" }}
            onMouseEnter={(e) => { e.currentTarget.play().catch(() => {}); }}
            onMouseLeave={(e) => { e.currentTarget.pause(); e.currentTarget.currentTime = midTime; }}
            onClick={(e) => e.stopPropagation()}
          />
          {/* Segment number badge */}
          <span
            className="absolute top-1.5 left-1.5 px-1.5 py-0.5 rounded text-[10px]"
            style={{ background: "rgba(0,0,0,0.7)", fontFamily: "var(--font-mono)", color: "var(--text-secondary)" }}
          >
            #{index + 1}
          </span>
          {/* Duration badge */}
          <span
            className="absolute bottom-1.5 left-1.5 px-1.5 py-0.5 rounded text-[10px]"
            style={{ background: "rgba(0,0,0,0.7)", fontFamily: "var(--font-mono)", color: "var(--text-primary)" }}
          >
            {trimDur.toFixed(1)}s
          </span>
          {/* Transition badge top-right */}
          {index > 0 && transition !== "cut" && (
            <span
              className="absolute top-1.5 right-1.5 px-1.5 py-0.5 rounded text-[9px]"
              style={{ background: "var(--accent-muted)", color: "var(--accent-primary)", fontFamily: "var(--font-mono)" }}
            >
              {transition.replace(/_/g, " ")}
            </span>
          )}
        </div>

        {/* Middle: info + score bars */}
        <div className="flex-1 flex flex-col justify-center px-4 py-3 gap-2 min-w-0">
          {/* Row 1: filename + timecodes */}
          <div className="flex items-center gap-2 min-w-0">
            {onCheck !== undefined && (
              <span
                onClick={(e) => { e.stopPropagation(); onCheck?.(); }}
                role="checkbox"
                aria-checked={checked ?? false}
                className="flex-shrink-0 w-3.5 h-3.5 rounded border flex items-center justify-center cursor-pointer transition-colors"
                style={{
                  borderColor: checked ? "var(--accent-primary)" : "var(--border-subtle)",
                  background:  checked ? "var(--accent-muted)"   : "transparent",
                  color:       "var(--accent-primary)",
                }}
              >
                {checked && (
                  <svg className="w-2 h-2" viewBox="0 0 10 8" fill="none">
                    <path d="M1 4l3 3 5-6" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
                  </svg>
                )}
              </span>
            )}
            <span
              className="text-xs font-medium truncate"
              style={{ fontFamily: "var(--font-mono)", color: "var(--text-secondary)" }}
              title={fileName}
            >
              {fileName}
            </span>
            <span
              className="text-[10px] flex-shrink-0"
              style={{ fontFamily: "var(--font-mono)", color: "var(--text-muted)" }}
            >
              {trim.start.toFixed(2)}s–{trim.end.toFixed(2)}s
            </span>
          </div>

          {/* Row 2: score bars */}
          <div className="flex items-end gap-4">
            {([
              { label: bar1Label,   score: bar1Score      },
              { label: "Motion",    score: generalScore   },
              { label: "Audio",     score: null           },
            ] as { label: string; score: number | null }[]).map(({ label, score }) => (
              <div key={label} className="flex flex-col gap-1" style={{ width: 60 }}>
                <span style={{ fontSize: 9, color: "var(--text-muted)", fontFamily: "var(--font-display)" }}>{label}</span>
                <div
                  className="rounded-full overflow-hidden"
                  style={{ height: 4, background: "var(--bg-tertiary)", width: 60 }}
                >
                  <div
                    className="h-full rounded-full"
                    style={{
                      width: score !== null && scoreVisible ? `${Math.round(score * 100)}%` : "0%",
                      background: "linear-gradient(90deg, var(--accent-secondary), var(--accent-primary))",
                      transition: "width 0.6s ease-out",
                    }}
                  />
                </div>
              </div>
            ))}
          </div>

          {/* Row 3: status badge / tags */}
          <div className="flex items-center gap-2 flex-wrap">
            {segment.buffer && (
              <span
                className="px-2 py-0.5 rounded text-[10px]"
                style={{ background: "rgba(96,165,250,0.15)", color: "#60a5fa", fontFamily: "var(--font-mono)" }}
              >
                Buffer
              </span>
            )}
            {tags.slice(0, 4).map((tag) => (
              <span
                key={tag}
                className="px-1.5 py-0.5 rounded text-[10px]"
                style={{ background: "var(--bg-tertiary)", color: "var(--text-muted)" }}
              >
                {tag}
              </span>
            ))}
          </div>
        </div>

        {/* Right: action buttons (80px) + expand chevron */}
        <div
          className="flex flex-col items-center justify-center gap-2 px-3 flex-shrink-0"
          style={{ width: 80, borderLeft: "1px solid var(--border-subtle)" }}
          onClick={(e) => e.stopPropagation()}
        >
          <button
            onClick={() => onDecision("accepted")}
            className="w-9 h-9 rounded flex items-center justify-center transition-all"
            style={{
              background: decision === "accepted" ? "rgba(52,211,153,0.2)" : "var(--bg-tertiary)",
              border: `1px solid ${decision === "accepted" ? "var(--status-accepted)" : "var(--border-subtle)"}`,
              color: decision === "accepted" ? "var(--status-accepted)" : "var(--text-muted)",
            }}
            title="Accept (A)"
          >
            <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
              <path d="M2 7l3.5 3.5L12 3" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </button>
          <button
            onClick={() => onDecision("rejected")}
            className="w-9 h-9 rounded flex items-center justify-center transition-all"
            style={{
              background: decision === "rejected" ? "rgba(239,68,68,0.2)" : "var(--bg-tertiary)",
              border: `1px solid ${decision === "rejected" ? "#ef4444" : "var(--border-subtle)"}`,
              color: decision === "rejected" ? "#ef4444" : "var(--text-muted)",
            }}
            title="Reject (R)"
          >
            <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
              <path d="M3 3l8 8M11 3l-8 8" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
            </svg>
          </button>
          <button
            onClick={(e) => { e.stopPropagation(); if (isExpanded) { onCollapse(); } else { onExpand(); } }}
            className="w-6 h-6 rounded flex items-center justify-center transition-all"
            style={{ color: "var(--text-muted)" }}
            title={isExpanded ? "Collapse" : "Expand controls"}
          >
            <ChevronDown
              className={`w-4 h-4 transition-transform duration-200 ${isExpanded ? "rotate-180" : ""}`}
            />
          </button>
        </div>
      </div>

      {/* ── Expanded section ────────────────────────────────── */}
      {isExpanded && (
        <div className="border-t border-border/30 px-4 pt-4 pb-4">
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">

            {/* Left: video player + trim rail */}
            <div className="flex flex-col gap-3">
              {/* Filter applied to wrapper div — NOT to <video> directly.
                  Applying CSS filter to a <video> element breaks PiP in Chrome/Firefox:
                  the compositing layer used for filters is incompatible with PiP rendering. */}
              <div
                className="relative rounded-lg overflow-hidden bg-black"
                style={{ aspectRatio: "9/16", maxHeight: "320px", filter: cssFilter || undefined }}
              >
                <video
                  ref={videoRef}
                  src={videoUrl}
                  className="w-full h-full object-contain"
                  onTimeUpdate={handleTimeUpdate}
                  onPlay={() => setPlaying(true)}
                  onPause={() => setPlaying(false)}
                  onEnded={() => setPlaying(false)}
                  preload="metadata"
                />
              </div>

              {/* Transport bar */}
              <div className="flex items-center gap-2">
                <button
                  onClick={(e) => { e.stopPropagation(); togglePlay(); }}
                  className="w-8 h-8 rounded-full surface-elevated flex items-center justify-center hover:text-primary transition-colors flex-shrink-0"
                >
                  {playing ? <Pause className="w-3.5 h-3.5" /> : <Play className="w-3.5 h-3.5" />}
                </button>
                <span className="font-mono text-xs text-muted-foreground tabular-nums">
                  {currentTime.toFixed(1)}s / {trim.end.toFixed(1)}s
                </span>
                <div className="flex-1 h-1 bg-border/40 rounded-full overflow-hidden">
                  <div
                    className="h-full bg-primary/60"
                    style={{ width: `${trimProgress}%` }}
                  />
                </div>
              </div>

              {/* Trim rail */}
              <div className="space-y-1.5" onClick={(e) => e.stopPropagation()}>
                <div className="flex justify-between text-[10px] text-muted-foreground font-mono">
                  <span>In: {trim.start.toFixed(1)}s</span>
                  <span>Out: {trim.end.toFixed(1)}s</span>
                </div>
                <SliderPrimitive.Root
                  className="relative flex w-full touch-none select-none items-center"
                  min={segment.start}
                  max={segment.end}
                  step={0.5}
                  value={[trim.start, trim.end]}
                  onValueChange={handleTrimChange}
                  minStepsBetweenThumbs={1}
                >
                  <SliderPrimitive.Track className="relative h-1.5 w-full grow overflow-hidden rounded-full bg-border/40">
                    <SliderPrimitive.Range className="absolute h-full bg-primary/70" />
                  </SliderPrimitive.Track>
                  <SliderPrimitive.Thumb className="block h-4 w-1.5 rounded-sm border-2 border-primary bg-background cursor-ew-resize focus:outline-none focus:ring-1 focus:ring-primary/50" />
                  <SliderPrimitive.Thumb className="block h-4 w-1.5 rounded-sm border-2 border-primary bg-background cursor-ew-resize focus:outline-none focus:ring-1 focus:ring-primary/50" />
                </SliderPrimitive.Root>
              </div>

              {/* Crop / Reframe — shown only for wider-than-9:16 source */}
              <div onClick={(e) => e.stopPropagation()}>
                <CropTool
                  segment={segment}
                  trim={trim}
                  crop={crop}
                  onCropChange={onCropChange}
                />
              </div>

              {/* SAM subject isolation */}
              <div onClick={(e) => e.stopPropagation()}>
                <SamTool
                  segment={segment}
                  trim={trim}
                  sam={sam}
                  onSamChange={onSamChange}
                />
              </div>
            </div>

            {/* Right: color grade */}
            <div className="flex flex-col gap-4" onClick={(e) => e.stopPropagation()}>
              {/* Sliders */}
              <div className="space-y-3">
                {GRADE_SLIDERS.map(({ key, label }) => {
                  const val = grade[key] as number;
                  return (
                    <div key={key} className="space-y-1">
                      <div className="flex justify-between text-xs">
                        <span className="text-muted-foreground">{label}</span>
                        <span className="font-mono text-foreground/60 tabular-nums w-8 text-right">
                          {val > 0 ? "+" : ""}{val}
                        </span>
                      </div>
                      <Slider
                        min={-50}
                        max={50}
                        step={1}
                        value={[val]}
                        onValueChange={([v]) => onGradeChange({ ...grade, [key]: v })}
                      />
                    </div>
                  );
                })}
              </div>

              {/* LUT grid */}
              <div className="space-y-1.5">
                <p className="text-xs text-muted-foreground">LUT Preset</p>
                <div className="grid grid-cols-4 gap-1.5">
                  {LUTS.map((lut) => (
                    <button
                      key={lut}
                      onClick={() => onGradeChange({ ...grade, lut })}
                      className={`px-1.5 py-1.5 text-[10px] rounded-md border font-mono transition-colors ${
                        grade.lut === lut
                          ? "border-primary text-primary bg-primary/10"
                          : "border-border/40 text-muted-foreground hover:border-border/70"
                      }`}
                    >
                      {lut}
                    </button>
                  ))}
                </div>
              </div>

              {/* Transition selector — only for segments after the first */}
              {index > 0 && (
                <div className="space-y-1.5">
                  <p className="text-xs text-muted-foreground">Transition in</p>
                  <div className="grid grid-cols-3 gap-1.5">
                    {TRANSITIONS.map((t) => (
                      <button
                        key={t.value}
                        onClick={() => onTransitionChange(t.value)}
                        className={`px-1.5 py-1.5 text-[10px] rounded-md border font-mono transition-colors text-left ${
                          transition === t.value
                            ? "border-primary text-primary bg-primary/10"
                            : "border-border/40 text-muted-foreground hover:border-border/70"
                        }`}
                        title={t.group}
                      >
                        {t.label}
                      </button>
                    ))}
                  </div>
                </div>
              )}

              {/* Transition preview — prev tail → current head */}
              {index > 0 && prevVideoUrl && (
                <TransitionPreview
                  prevVideoUrl={prevVideoUrl}
                  prevTrimEnd={prevTrim?.end ?? (prevSegment?.end ?? 0)}
                  currVideoUrl={videoUrl}
                  currTrimStart={trim.start}
                  transition={transition}
                />
              )}

              {/* Tags (expanded view) */}
              {(tags.length > 0 || segment.buffer) && (
                <div className="flex flex-wrap gap-1.5 text-[11px] text-muted-foreground">
                  {tags.map((tag) => (
                    <span key={tag} className="px-2 py-0.5 rounded-full bg-border/40">{tag}</span>
                  ))}
                  {segment.buffer && <span className="text-[10px] text-accent font-mono uppercase tracking-widest">Buffer</span>}
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default SegmentCard;
