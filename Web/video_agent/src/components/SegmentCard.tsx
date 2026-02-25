import { useRef, useState, useEffect, useCallback } from "react";
import { Film, Play, Pause, ChevronDown } from "lucide-react";
import * as SliderPrimitive from "@radix-ui/react-slider";
import { Slider } from "@/components/ui/slider";
import type { Segment, GradeSettings, TrimState } from "@/lib/api";
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

// ── SegmentCard ───────────────────────────────────────────────────────────────

interface SegmentCardProps {
  segment: Segment;
  index: number;
  decision: SegmentDecision | undefined;  // undefined = not yet decided (neutral)
  trim: TrimState;
  grade: GradeSettings;
  transition: string;
  prevSegment?: Segment | null;
  prevTrim?: TrimState | null;
  onDecision: (decision: SegmentDecision) => void;
  onTrimChange: (start: number, end: number) => void;
  onGradeChange: (grade: GradeSettings) => void;
  onTransitionChange: (transition: string) => void;
  isExpanded: boolean;
  onExpand: () => void;
  onCollapse: () => void;
}

const SegmentCard = ({
  segment,
  index,
  decision,
  trim,
  grade,
  transition,
  prevSegment,
  prevTrim,
  onDecision,
  onTrimChange,
  onGradeChange,
  onTransitionChange,
  isExpanded,
  onExpand,
  onCollapse,
}: SegmentCardProps) => {
  const videoRef = useRef<HTMLVideoElement>(null);
  const [playing, setPlaying]       = useState(false);
  const [currentTime, setCurrentTime] = useState(trim.start);

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

  return (
    <div
      tabIndex={0}
      onKeyDown={handleKeyDown}
      className={`segment-card glass-card border rounded-lg focus:outline-none focus:ring-1 focus:ring-primary/30 transition-colors ${
        decision === "rejected" ? "opacity-50 border-destructive/40" : "border-border/60"
      } ${segment.buffer ? "border-dashed border-accent/60" : ""}`}
    >
      {/* ── Card header ─────────────────────────────────────── */}
      <div
        className="p-4 flex items-center justify-between gap-3 flex-wrap cursor-pointer select-none"
        onClick={() => isExpanded ? onCollapse() : onExpand()}
      >
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-md surface-elevated flex items-center justify-center flex-shrink-0">
            <Film className="w-5 h-5 text-primary/70" />
          </div>
          <div>
            <p className="text-sm font-semibold text-foreground">{fileName}</p>
            <p className="text-xs text-muted-foreground font-mono">
              {trim.start.toFixed(2)}s – {trim.end.toFixed(2)}s · {trimDur.toFixed(2)}s
              {trimDur !== rawDur && (
                <span className="ml-1 text-accent/70">(of {rawDur.toFixed(2)}s)</span>
              )}
            </p>
          </div>
        </div>

        <div className="flex items-center gap-2">
          {styleScore !== null && (
            <span className="text-xs font-mono text-muted-foreground tabular-nums">
              {styleScore.toFixed(2)}
            </span>
          )}
          {index > 0 && transition !== "cut" && (
            <span className="text-[10px] font-mono px-1.5 py-0.5 rounded border border-primary/30 text-primary/70">
              {transition.replace("_", " ")}
            </span>
          )}
          <button
            onClick={(e) => { e.stopPropagation(); onDecision("accepted"); }}
            className={`px-3 py-1.5 text-xs rounded-md border ${
              decision === "accepted"
                ? "border-primary text-primary"
                : "border-border/50 text-muted-foreground"
            }`}
          >
            Accept
          </button>
          <button
            onClick={(e) => { e.stopPropagation(); onDecision("rejected"); }}
            className={`px-3 py-1.5 text-xs rounded-md border ${
              decision === "rejected"
                ? "border-destructive text-destructive"
                : "border-border/50 text-muted-foreground"
            }`}
          >
            Reject
          </button>
          <ChevronDown
            className={`w-4 h-4 text-muted-foreground transition-transform duration-200 ${isExpanded ? "rotate-180" : ""}`}
          />
        </div>
      </div>

      {/* Tags (collapsed view) */}
      {!isExpanded && tags.length > 0 && (
        <div className="px-4 pb-3 flex flex-wrap gap-1.5 text-[11px] text-muted-foreground">
          {tags.map((tag) => (
            <span key={tag} className="px-2 py-0.5 rounded-full bg-border/40">{tag}</span>
          ))}
          {segment.buffer && <span className="text-[10px] text-accent font-mono uppercase tracking-widest">Buffer</span>}
        </div>
      )}

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
