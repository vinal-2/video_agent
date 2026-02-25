import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  Trash2,
  Terminal,
  FileVideo,
  CheckCircle2,
  Activity,
  AlertTriangle,
  PlayCircle,
} from "lucide-react";
import type { PipelineStatus, Segment, OutputInfo, SegmentCounts, GradeSettings, TrimState } from "@/lib/api";
import type { SegmentDecision } from "@/hooks/usePipeline";
import SegmentCard from "@/components/SegmentCard";

const tabs = [
  { id: "Log"    as const, icon: Terminal    },
  { id: "Review" as const, icon: CheckCircle2 },
  { id: "Output" as const, icon: FileVideo    },
];
type Tab = (typeof tabs)[number]["id"];

const phaseLabels: Record<string, string> = {
  idle:      "Idle",
  analysing: "Video analysis",
  reviewing: "Review clips",
  rendering: "Rendering",
  done:      "Done",
  error:     "Error",
};

interface MainContentProps {
  status: PipelineStatus | null;
  logs: string[];
  segments: Segment[];
  segmentStates: Record<number, SegmentDecision | undefined>;
  trimData: Record<number, TrimState>;
  gradeData: Record<number, GradeSettings>;
  transitionData: Record<number, string>;
  setSegmentState: (index: number, decision: SegmentDecision) => void;
  updateTrim: (index: number, start: number, end: number) => void;
  updateGrade: (index: number, grade: GradeSettings) => void;
  updateTransition: (index: number, transition: string) => void;
  acceptAll: () => void;
  rejectAll: () => void;
  onRender: () => Promise<void>;
  running: boolean;
  outputInfo: OutputInfo | null;
  clearLogs: () => void;
  lastError?: string | null;
  segmentCounts?: SegmentCounts;
  ffmpegProgress: number | null;
}

const MainContent = ({
  status,
  logs,
  segments,
  segmentStates,
  trimData,
  gradeData,
  transitionData,
  setSegmentState,
  updateTrim,
  updateGrade,
  updateTransition,
  acceptAll,
  rejectAll,
  onRender,
  running,
  outputInfo,
  clearLogs,
  lastError,
  segmentCounts,
  ffmpegProgress,
}: MainContentProps) => {
  const [activeTab, setActiveTab] = useState<Tab>("Log");
  const [rendering, setRendering] = useState(false);

  const phase = status?.phase ?? "idle";
  const computedAccepted = useMemo(
    () => segments.filter((_, idx) => segmentStates[idx] !== "rejected").length,
    [segments, segmentStates],
  );
  const acceptedCount  = computedAccepted;
  const totalSegments  = segmentCounts?.selected ?? segments.length;
  // Compute pending client-side: segments with no explicit decision yet.
  // The server's segment_counts.pending is always stale (set at analysis time).
  const pendingCount   = useMemo(
    () => segments.filter((_, idx) => segmentStates[idx] === undefined).length,
    [segments, segmentStates],
  );
  const progressPercent = ffmpegProgress !== null ? Math.round(ffmpegProgress * 100) : null;

  useEffect(() => {
    if (phase === "reviewing") {
      setActiveTab("Review");
    } else if (phase === "done") {
      setActiveTab("Output");
    } else if (phase === "rendering") {
      setActiveTab("Log");
    }
  }, [phase]);

  const handleRender = () => {
    setRendering(true);
    onRender()
      .catch((err) => console.error(err))
      .finally(() => setRendering(false));
  };

  const phaseLabel = phaseLabels[phase] ?? phase;

  return (
    <main className="flex-1 flex flex-col min-w-0 relative">
      <div className="absolute inset-0 dot-grid opacity-30 pointer-events-none" />
      <div className="absolute inset-0 scanline pointer-events-none" />

      <div className="relative z-10 border-b border-border/50 glass-surface">
        <div className="flex">
          {tabs.map(({ id, icon: Icon }) => (
            <button
              key={id}
              onClick={() => setActiveTab(id)}
              className={`flex items-center gap-2 px-6 py-3.5 text-sm font-medium transition-all relative group ${
                activeTab === id ? "text-foreground" : "text-muted-foreground hover:text-secondary-foreground"
              }`}
            >
              <Icon className={`w-4 h-4 transition-colors ${activeTab === id ? "text-primary" : ""}`} />
              {id}
              {activeTab === id && (
                <span className="absolute bottom-0 left-2 right-2 h-0.5 bg-gradient-to-r from-transparent via-primary to-transparent rounded-t" />
              )}
            </button>
          ))}
        </div>
      </div>

      <div className="relative z-10 flex items-center justify-between px-6 py-3.5 border-b border-border/30 flex-wrap gap-3">
        <div className="flex items-center gap-2.5 flex-wrap">
          {[
            { label: "Step",     value: phaseLabel },
            { label: "Segments", value: `${acceptedCount}/${totalSegments}` },
            { label: "Pending",  value: pendingCount.toString() },
            { label: "Logs",     value: logs.length.toString() },
          ].map(({ label, value }) => (
            <span
              key={label}
              className="inline-flex items-center gap-2 px-3.5 py-1.5 rounded-lg glass-card text-sm"
            >
              <span className="text-muted-foreground">{label}</span>
              <span className="font-mono text-foreground/70">{value}</span>
            </span>
          ))}
          {progressPercent !== null && phase === "rendering" && (
            <span className="inline-flex items-center gap-2 px-3.5 py-1.5 rounded-lg glass-card text-sm">
              <span className="text-muted-foreground">Render</span>
              <span className="font-mono text-primary">{progressPercent}%</span>
            </span>
          )}
        </div>
        <button
          onClick={clearLogs}
          className="flex items-center gap-1.5 text-sm text-muted-foreground hover:text-destructive transition-colors group"
        >
          <Trash2 className="w-4 h-4 group-hover:scale-110 transition-transform" />
          Clear log
        </button>
      </div>

      {lastError && (
        <div className="relative z-10 px-6 py-2 bg-destructive/10 text-destructive text-sm flex items-center gap-2 border-b border-destructive/20">
          <AlertTriangle className="w-4 h-4" />
          <span>{lastError}</span>
        </div>
      )}

      <div className="relative z-10 flex-1 overflow-hidden">
        {activeTab === "Log" && <LogPanel logs={logs} running={running} phase={phaseLabel} />}
        {activeTab === "Review" && (
          <ReviewPanel
            segments={segments}
            segmentStates={segmentStates}
            trimData={trimData}
            gradeData={gradeData}
            transitionData={transitionData}
            setSegmentState={setSegmentState}
            updateTrim={updateTrim}
            updateGrade={updateGrade}
            updateTransition={updateTransition}
            acceptAll={acceptAll}
            rejectAll={rejectAll}
            acceptedCount={acceptedCount}
            totalSegments={totalSegments}
            onRender={handleRender}
            running={running || rendering}
          />
        )}
        {activeTab === "Output" && <OutputPanel outputInfo={outputInfo} />}
      </div>
    </main>
  );
};

// ── Log panel ──────────────────────────────────────────────────────────────────

interface LogPanelProps {
  logs: string[];
  running: boolean;
  phase: string;
}

const LogPanel = ({ logs, running, phase }: LogPanelProps) => {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [logs]);

  if (!logs.length) {
    return (
      <EmptyState
        title="Ready to run"
        subtitle={`Pipeline is ${running ? "running" : "standing by"}. Current phase: ${phase}.`}
      />
    );
  }

  return (
    <div className="h-full overflow-y-auto px-6 py-4 font-mono text-xs text-muted-foreground space-y-1 log-scroll">
      {logs.map((line, idx) => (
        <div key={idx} className={`log-line ${classifyLog(line)}`}>
          {line}
        </div>
      ))}
      <div ref={bottomRef} />
    </div>
  );
};

// ── Review panel ───────────────────────────────────────────────────────────────

interface ReviewPanelProps {
  segments: Segment[];
  segmentStates: Record<number, SegmentDecision | undefined>;
  trimData: Record<number, TrimState>;
  gradeData: Record<number, GradeSettings>;
  transitionData: Record<number, string>;
  setSegmentState: (index: number, decision: SegmentDecision) => void;
  updateTrim: (index: number, start: number, end: number) => void;
  updateGrade: (index: number, grade: GradeSettings) => void;
  updateTransition: (index: number, transition: string) => void;
  acceptAll: () => void;
  rejectAll: () => void;
  acceptedCount: number;
  totalSegments: number;
  onRender: () => void;
  running: boolean;
}

const ReviewPanel = ({
  segments,
  segmentStates,
  trimData,
  gradeData,
  transitionData,
  setSegmentState,
  updateTrim,
  updateGrade,
  updateTransition,
  acceptAll,
  rejectAll,
  acceptedCount,
  totalSegments,
  onRender,
  running,
}: ReviewPanelProps) => {
  const [expandedIndex, setExpandedIndex] = useState<number | null>(null);

  if (!segments.length) {
    return (
      <EmptyState
        title="No segments to review"
        subtitle="Run the pipeline to generate candidate clips."
        icon={<CheckCircle2 className="w-8 h-8 text-primary/70" />}
      />
    );
  }

  return (
    <div className="h-full overflow-y-auto px-6 py-4 space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold text-foreground">Review segments</h2>
          <p className="text-sm text-muted-foreground">{acceptedCount}/{totalSegments} accepted</p>
        </div>
        <div className="flex items-center gap-2">
          <button onClick={acceptAll} className="text-xs px-3 py-2 rounded-md border border-border/60 hover:border-primary/40">
            Accept all
          </button>
          <button onClick={rejectAll} className="text-xs px-3 py-2 rounded-md border border-border/60 hover:border-primary/40">
            Reject all
          </button>
          <button
            onClick={onRender}
            disabled={!acceptedCount || running}
            className="flex items-center gap-2 px-4 py-2.5 rounded-lg gradient-primary-btn text-primary-foreground font-semibold disabled:opacity-60"
          >
            <PlayCircle className="w-4 h-4" />
            {running ? "Rendering..." : "Render accepted"}
          </button>
        </div>
      </div>

      <div className="space-y-3">
        {segments.map((segment, index) => {
          const trim       = trimData[index]  ?? { start: segment.start, end: segment.end };
          const grade      = gradeData[index] ?? { brightness: 0, contrast: 0, saturation: 0, temp: 0, lut: "none" };
          const decision   = segmentStates[index]; // undefined = neutral (not yet decided)

          return (
            <SegmentCard
              key={`${segment.video_path ?? ""}-${segment.start}-${segment.end}-${index}`}
              segment={segment}
              index={index}
              decision={decision}
              trim={trim}
              grade={grade}
              transition={transitionData[index] ?? "cut"}
              prevSegment={index > 0 ? segments[index - 1] : null}
              prevTrim={index > 0 ? (trimData[index - 1] ?? { start: segments[index - 1].start, end: segments[index - 1].end }) : null}
              onDecision={(d) => setSegmentState(index, d)}
              onTrimChange={(s, e) => updateTrim(index, s, e)}
              onGradeChange={(g) => updateGrade(index, g)}
              onTransitionChange={(t) => updateTransition(index, t)}
              isExpanded={expandedIndex === index}
              onExpand={() => setExpandedIndex(index)}
              onCollapse={() => setExpandedIndex(null)}
            />
          );
        })}
      </div>
    </div>
  );
};

// ── Output panel ───────────────────────────────────────────────────────────────

const OutputPanel = ({ outputInfo }: { outputInfo: OutputInfo | null }) => {
  if (!outputInfo?.path) {
    return (
      <EmptyState
        title="No output yet"
        subtitle="Render a sequence to see the compiled video here."
        icon={<FileVideo className="w-8 h-8 text-primary/70" />}
      />
    );
  }

  return (
    <div className="h-full flex flex-col items-center justify-center gap-4 px-6 py-6">
      <video
        key={outputInfo.path}
        src={`${outputInfo.path}?t=${Date.now()}`}
        controls
        className="w-full max-w-3xl rounded-xl border border-border/60 bg-black"
      />
      <div className="flex items-center gap-4 text-sm text-muted-foreground">
        <span className="font-mono text-foreground">{outputInfo.name ?? "event_compilation.mp4"}</span>
        {outputInfo.size_mb ? <span>{outputInfo.size_mb} MB</span> : null}
        <a
          href={outputInfo.path}
          download={outputInfo.name}
          className="text-primary hover:underline font-semibold"
        >
          Download
        </a>
      </div>
    </div>
  );
};

// ── Empty state ────────────────────────────────────────────────────────────────

const EmptyState = ({
  title,
  subtitle,
  icon = <Activity className="w-8 h-8 text-primary/70" />,
}: {
  title: string;
  subtitle: string;
  icon?: ReactNode;
}) => (
  <div className="relative z-10 flex-1 flex items-center justify-center px-8">
    <div className="text-center space-y-6 animate-fade-in max-w-md">
      <div className="relative mx-auto w-20 h-20">
        <div className="absolute inset-0 rounded-2xl bg-primary/10 animate-pulse-glow" />
        <div className="relative w-20 h-20 rounded-2xl surface-elevated flex items-center justify-center animate-float text-primary/70">
          {icon}
        </div>
      </div>
      <div className="space-y-2">
        <h2 className="text-xl font-semibold text-foreground tracking-tight">{title}</h2>
        <p className="text-sm text-muted-foreground leading-relaxed">{subtitle}</p>
      </div>
    </div>
  </div>
);

// ── Log classifier ─────────────────────────────────────────────────────────────

function classifyLog(line: string) {
  if (!line) return "";
  if (line.toLowerCase().includes("error") || line.toLowerCase().includes("traceback")) return "text-destructive";
  if (line.toLowerCase().includes("warn")) return "text-amber-400";
  if (line.includes("Done") || line.includes("✓")) return "text-status-online";
  return "";
}

export default MainContent;
