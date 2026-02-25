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
  { value: "dissolve",    label: "Dissolve",    group: "Blend"  },
  { value: "fade_black",  label: "Fade black",  group: "Blend"  },
  { value: "wipe_up",     label: "Wipe up",     group: "Blend"  },
  { value: "zoom_in",     label: "Zoom in",     group: "Blend"  },
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
  else if (grade.temp < 0) parts.push(`hue-rotate(${Math.abs(grade.temp)}deg)`);
  const lutCss = LUT_CSS[grade.lut] ?? "";
  if (lutCss) parts.push(lutCss);
  return parts.join(" ");
}

interface SegmentCardProps {
  segment: Segment;
  index: number;
  decision: SegmentDecision | undefined;  // undefined = not yet decided (neutral)
  trim: TrimState;
  grade: GradeSettings;
  transition: string;
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

  const fileName   = segment.video_path?.split(/[/\\]/).pop() ?? "Unknown clip";
  const tags       = (segment.combined_tags ?? segment.tags ?? []) as string[];
  const rawDur     = segment.end - segment.start;
  const trimDur    = trim.end - trim.start;
  const styleScore = typeof segment.style_score === "number" ? segment.style_score : null;
  const videoUrl   = `/video/${encodeURIComponent(fileName)}`;
  const cssFilter  = buildCssFilter(grade);

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
              <div className="relative rounded-lg overflow-hidden bg-black" style={{ aspectRatio: "9/16", maxHeight: "320px" }}>
                <video
                  ref={videoRef}
                  src={videoUrl}
                  className="w-full h-full object-contain"
                  style={{ filter: cssFilter || undefined }}
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
