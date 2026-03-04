import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { motion, AnimatePresence } from "motion/react";
import {
  Trash2,
  Terminal,
  FileVideo,
  CheckCircle2,
  Activity,
  AlertTriangle,
  PlayCircle,
  RotateCcw,
  Wand2,
  Play,
  Pause,
  Volume2,
  Maximize2,
} from "lucide-react";
import type { PipelineStatus, Segment, OutputInfo, SegmentCounts, GradeSettings, TrimState, CropSettings, SamMaskSettings, InpaintJob, InpaintEngine } from "@/lib/api";
import type { SegmentDecision } from "@/hooks/usePipeline";
import SegmentCard from "@/components/SegmentCard";
import SegmentTimeline from "@/components/SegmentTimeline";
import InpaintTab from "@/components/InpaintTab";

const tabs = [
  { id: "Log"     as const, icon: Terminal    },
  { id: "Review"  as const, icon: CheckCircle2 },
  { id: "Output"  as const, icon: FileVideo    },
  { id: "Inpaint" as const, icon: Wand2        },
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
  cropData: Record<number, CropSettings>;
  samData: Record<number, SamMaskSettings>;
  inpaintJobs: Record<string, InpaintJob>;
  setSegmentState: (index: number, decision: SegmentDecision) => void;
  updateTrim: (index: number, start: number, end: number) => void;
  updateGrade: (index: number, grade: GradeSettings) => void;
  updateTransition: (index: number, transition: string) => void;
  updateCrop: (index: number, crop: CropSettings | null) => void;
  updateSamMask: (index: number, sam: SamMaskSettings | null) => void;
  beginInpaint: (segIdx: number, maskB64: string, engine: InpaintEngine) => Promise<string>;
  removeInpaintJob: (jobId: string) => Promise<void>;
  renderWithInpainting: () => Promise<void>;
  acceptAll: () => void;
  rejectAll: () => void;
  onRender: () => Promise<void>;
  onReset: () => Promise<void>;
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
  cropData,
  samData,
  inpaintJobs,
  setSegmentState,
  updateTrim,
  updateGrade,
  updateTransition,
  updateCrop,
  updateSamMask,
  beginInpaint,
  removeInpaintJob,
  renderWithInpainting,
  acceptAll,
  rejectAll,
  onRender,
  onReset,
  running,
  outputInfo,
  clearLogs,
  lastError,
  segmentCounts,
  ffmpegProgress,
}: MainContentProps) => {
  const [activeTab, setActiveTab]   = useState<Tab>("Log");
  const [rendering, setRendering]   = useState(false);
  const [showBanner, setShowBanner] = useState(false);
  const prevPhaseRef                = useRef<string>("idle");

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
      // Show completion banner when render finishes
      if (prevPhaseRef.current === "rendering") {
        setShowBanner(true);
        const id = setTimeout(() => setShowBanner(false), 3500);
        return () => clearTimeout(id);
      }
    } else if (phase === "rendering") {
      setActiveTab("Log");
    }
    prevPhaseRef.current = phase;
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

      {/* ── Render completion banner ─── */}
      <AnimatePresence>
        {showBanner && (
          <motion.div
            key="completion-banner"
            initial={{ y: -48, opacity: 0 }}
            animate={{ y: 0,   opacity: 1 }}
            exit={{ y: -48, opacity: 0 }}
            transition={{ duration: 0.25, ease: "easeOut" }}
            className="absolute top-0 left-0 right-0 z-50 flex items-center justify-center gap-2 py-2.5"
            style={{ background: "var(--accent-primary)", color: "var(--bg-primary)" }}
          >
            <svg width="14" height="14" viewBox="0 0 14 14" fill="currentColor">
              <path d="M2 7l4 4 6-6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" fill="none"/>
            </svg>
            <span style={{ fontFamily: "var(--font-display)", fontSize: 13, fontWeight: 600 }}>
              Render complete{outputInfo?.name ? ` — ${outputInfo.name}` : ""}
            </span>
          </motion.div>
        )}
      </AnimatePresence>

      <div
        className="relative z-10 flex"
        style={{ borderBottom: "1px solid var(--border-subtle)", background: "var(--bg-secondary)" }}
      >
        {tabs.map(({ id, icon: Icon }) => {
          const disabled = id === "Inpaint" && phase !== "done";
          const isActive = activeTab === id;

          // Badge count for Log and Inpaint tabs
          const badge =
            id === "Log"     ? (logs.length > 0 ? logs.length : null) :
            id === "Inpaint" ? (pendingCount > 0 ? pendingCount  : null) :
            null;

          return (
            <button
              key={id}
              onClick={() => !disabled && setActiveTab(id)}
              disabled={disabled}
              className="relative flex items-center gap-2 px-5 py-3 text-sm transition-all disabled:opacity-40 disabled:cursor-not-allowed"
              style={{
                fontFamily: "var(--font-display)",
                fontWeight: isActive ? 500 : 400,
                color: isActive ? "var(--text-primary)" : "var(--text-secondary)",
                borderBottom: isActive ? "2px solid var(--accent-primary)" : "2px solid transparent",
                marginBottom: -1,
              }}
            >
              <Icon className="w-3.5 h-3.5" style={{ color: isActive ? "var(--accent-primary)" : "currentColor" }} />
              {id}
              {badge !== null && (
                <span
                  className="inline-flex items-center justify-center rounded-full min-w-[18px] h-[18px] px-1 text-[10px] font-mono"
                  style={{
                    background: isActive ? "var(--accent-muted)" : "rgba(255,255,255,0.06)",
                    color: isActive ? "var(--accent-primary)" : "var(--text-muted)",
                  }}
                >
                  {badge > 999 ? "999+" : badge}
                </span>
              )}
            </button>
          );
        })}
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
        <div className="flex items-center gap-3">
          <button
            onClick={clearLogs}
            className="flex items-center gap-1.5 text-sm text-muted-foreground hover:text-destructive transition-colors group"
          >
            <Trash2 className="w-4 h-4 group-hover:scale-110 transition-transform" />
            Clear log
          </button>
          {(segments.length > 0 || outputInfo || logs.length > 0) && !running && (
            <button
              onClick={() => onReset().catch(() => {})}
              className="flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground transition-colors group"
              title="Clear all segments, logs and output — ready for a new run"
            >
              <RotateCcw className="w-4 h-4 group-hover:rotate-[-45deg] transition-transform duration-200" />
              New project
            </button>
          )}
        </div>
      </div>

      {lastError && (
        <div className="relative z-10 px-6 py-2 bg-destructive/10 text-destructive text-sm flex items-center gap-2 border-b border-destructive/20">
          <AlertTriangle className="w-4 h-4" />
          <span>{lastError}</span>
        </div>
      )}

      <div className="relative z-10 flex-1 overflow-hidden">
        <AnimatePresence mode="wait">
          {activeTab === "Log" && (
            <motion.div key="log" className="h-full"
              initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0 }} transition={{ duration: 0.15 }}
            >
              <LogPanel logs={logs} running={running} phase={phaseLabel} onClear={clearLogs} />
            </motion.div>
          )}
          {activeTab === "Review" && (
            <motion.div key="review" className="h-full"
              initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0 }} transition={{ duration: 0.15 }}
            >
              <ReviewPanel
                segments={segments}
                segmentStates={segmentStates}
                trimData={trimData}
                gradeData={gradeData}
                transitionData={transitionData}
                cropData={cropData}
                samData={samData}
                setSegmentState={setSegmentState}
                updateTrim={updateTrim}
                updateGrade={updateGrade}
                updateTransition={updateTransition}
                updateCrop={updateCrop}
                updateSamMask={updateSamMask}
                acceptAll={acceptAll}
                rejectAll={rejectAll}
                acceptedCount={acceptedCount}
                totalSegments={totalSegments}
                onRender={handleRender}
                running={running || rendering}
              />
            </motion.div>
          )}
          {activeTab === "Output" && (
            <motion.div key="output" className="h-full"
              initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0 }} transition={{ duration: 0.15 }}
            >
              <OutputPanel
                outputInfo={outputInfo}
                running={running}
                phase={phase}
                logs={logs}
                ffmpegProgress={ffmpegProgress}
              />
            </motion.div>
          )}
          {activeTab === "Inpaint" && (
            <motion.div key="inpaint" className="h-full"
              initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0 }} transition={{ duration: 0.15 }}
            >
              <InpaintTab
                segments={segments}
                segmentStates={segmentStates}
                trimData={trimData}
                inpaintJobs={inpaintJobs}
                outputInfo={outputInfo}
                phase={phase}
                onBeginInpaint={beginInpaint}
                onCancelJob={removeInpaintJob}
                onSkip={() => setActiveTab("Output")}
                onRenderWithInpainting={renderWithInpainting}
                running={running}
              />
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </main>
  );
};

// ── Log panel ──────────────────────────────────────────────────────────────────

interface LogPanelProps {
  logs: string[];
  running: boolean;
  phase: string;
  onClear: () => void;
}

type LogLevel = "info" | "success" | "warning" | "error" | "phase";

function classifyLogLine(line: string): LogLevel {
  const lower = line.toLowerCase();
  if (line.includes("===") || line.startsWith("─")) return "phase";
  if (lower.includes("error") || lower.includes("traceback")) return "error";
  if (lower.includes("warn")) return "warning";
  if (line.includes("✓") || lower.includes("done") || lower.includes("success")) return "success";
  return "info";
}

const LOG_COLORS: Record<LogLevel, string> = {
  info:    "var(--text-secondary)",
  success: "var(--status-accepted)",
  warning: "var(--status-failed)",
  error:   "#f87171",
  phase:   "var(--accent-primary)",
};

const LogPanel = ({ logs, running, phase, onClear }: LogPanelProps) => {
  const [search, setSearch]           = useState("");
  const [autoScroll, setAutoScroll]   = useState(true);
  const [hasNew, setHasNew]           = useState(false);
  const containerRef                  = useRef<HTMLDivElement>(null);
  const bottomRef                     = useRef<HTMLDivElement>(null);
  const autoScrollRef                 = useRef(true);

  // Keep ref in sync for stale-closure-safe scroll handler
  useEffect(() => { autoScrollRef.current = autoScroll; }, [autoScroll]);

  const filteredLogs = useMemo(
    () => search ? logs.filter((l) => l.toLowerCase().includes(search.toLowerCase())) : logs,
    [logs, search],
  );

  const errorCount   = useMemo(() => logs.filter((l) => classifyLogLine(l) === "error").length,   [logs]);
  const warningCount = useMemo(() => logs.filter((l) => classifyLogLine(l) === "warning").length, [logs]);

  // Auto-scroll on new log entries
  useEffect(() => {
    if (!autoScrollRef.current) { setHasNew(true); return; }
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
    setHasNew(false);
  }, [logs]);

  const handleScroll = useCallback(() => {
    const el = containerRef.current;
    if (!el) return;
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
    if (nearBottom) {
      if (!autoScrollRef.current) { setAutoScroll(true); setHasNew(false); }
    } else {
      if (autoScrollRef.current) setAutoScroll(false);
    }
  }, []);

  const resumeScroll = () => {
    setAutoScroll(true);
    setHasNew(false);
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  };

  const copyAll = () => {
    navigator.clipboard.writeText(logs.join("\n")).catch(() => {});
  };

  return (
    <div className="h-full flex flex-col relative" style={{ background: "#0a0a0a" }}>

      {/* ── Toolbar ──────────────────────────────────────── */}
      <div
        className="flex items-center gap-3 px-4 py-2 flex-shrink-0"
        style={{ borderBottom: "1px solid var(--border-subtle)", background: "var(--bg-secondary)" }}
      >
        {/* Live indicator */}
        <div className="flex items-center gap-1.5 flex-shrink-0">
          <span
            className="w-1.5 h-1.5 rounded-full flex-shrink-0"
            style={{
              background: running ? "var(--accent-primary)" : "var(--text-muted)",
              animation: running ? "ping-amber 1.5s ease-in-out infinite" : "none",
            }}
          />
          <span style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: running ? "var(--accent-primary)" : "var(--text-muted)" }}>
            {running ? "Live" : "Idle"}
          </span>
        </div>

        {/* Search */}
        <input
          type="text"
          placeholder="Filter logs..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="px-2 py-1 rounded"
          style={{
            background: "var(--bg-tertiary)",
            border: "1px solid var(--border-subtle)",
            color: "var(--text-primary)",
            fontFamily: "var(--font-mono)",
            fontSize: 11,
            outline: "none",
            width: 200,
          }}
        />

        <div className="flex-1" />

        {/* Auto-scroll toggle */}
        <button
          onClick={() => setAutoScroll((v) => !v)}
          className="px-2 py-1 rounded text-xs"
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 11,
            background: autoScroll ? "var(--accent-muted)" : "var(--bg-tertiary)",
            border: `1px solid ${autoScroll ? "var(--border-active)" : "var(--border-subtle)"}`,
            color: autoScroll ? "var(--accent-primary)" : "var(--text-muted)",
          }}
        >
          Auto-scroll
        </button>

        {/* Copy all */}
        <button
          onClick={copyAll}
          className="px-2 py-1 rounded text-xs"
          style={{ fontFamily: "var(--font-mono)", fontSize: 11, background: "var(--bg-tertiary)", border: "1px solid var(--border-subtle)", color: "var(--text-secondary)" }}
        >
          Copy
        </button>

        {/* Clear */}
        <button
          onClick={onClear}
          className="px-2 py-1 rounded text-xs"
          style={{ fontFamily: "var(--font-mono)", fontSize: 11, background: "var(--bg-tertiary)", border: "1px solid var(--border-subtle)", color: "var(--text-secondary)" }}
        >
          Clear
        </button>
      </div>

      {/* ── Log lines ────────────────────────────────────── */}
      <div
        ref={containerRef}
        onScroll={handleScroll}
        className="flex-1 overflow-y-auto px-4 py-3"
        style={{ fontFamily: "var(--font-mono)", fontSize: 12, lineHeight: 1.6 }}
      >
        {filteredLogs.length === 0 ? (
          <div style={{ color: "var(--text-muted)", paddingTop: 16 }}>
            {search
              ? "No matching log entries."
              : `Pipeline ${running ? "running" : "standing by"}. Current phase: ${phase}.`}
          </div>
        ) : (
          filteredLogs.map((line, idx) => {
            const level = classifyLogLine(line);
            if (level === "phase") {
              return (
                <div key={idx} className="flex items-center gap-3 py-1.5 my-1">
                  <div className="flex-1 h-px" style={{ background: "var(--border-subtle)" }} />
                  <span style={{ color: "var(--accent-primary)", fontWeight: 600, fontSize: 11, whiteSpace: "nowrap" }}>
                    {line.replace(/={3,}/g, "").replace(/─{3,}/g, "").trim() || line}
                  </span>
                  <div className="flex-1 h-px" style={{ background: "var(--border-subtle)" }} />
                </div>
              );
            }
            return (
              <div key={idx} style={{ color: LOG_COLORS[level] }}>
                {line}
              </div>
            );
          })
        )}
        <div ref={bottomRef} />
      </div>

      {/* ── "New entries" float button ────────────────────── */}
      {hasNew && !autoScroll && (
        <div className="absolute bottom-10 left-1/2 -translate-x-1/2 z-10">
          <button
            onClick={resumeScroll}
            className="px-3 py-1.5 rounded-full text-xs font-semibold"
            style={{
              background: "var(--accent-primary)",
              color: "var(--bg-primary)",
              fontFamily: "var(--font-display)",
              boxShadow: "0 2px 8px rgba(232,160,64,0.4)",
            }}
          >
            ↓ New entries
          </button>
        </div>
      )}

      {/* ── Stats bar ────────────────────────────────────── */}
      <div
        className="flex items-center gap-4 px-4 flex-shrink-0"
        style={{ height: 32, borderTop: "1px solid var(--border-subtle)", background: "var(--bg-secondary)" }}
      >
        <span style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--text-muted)" }}>
          {logs.length} lines
        </span>
        {errorCount > 0 && (
          <span style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "#f87171" }}>
            {errorCount} error{errorCount !== 1 ? "s" : ""}
          </span>
        )}
        {warningCount > 0 && (
          <span style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--status-failed)" }}>
            {warningCount} warning{warningCount !== 1 ? "s" : ""}
          </span>
        )}
      </div>
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
  cropData: Record<number, CropSettings>;
  samData: Record<number, SamMaskSettings>;
  setSegmentState: (index: number, decision: SegmentDecision) => void;
  updateTrim: (index: number, start: number, end: number) => void;
  updateGrade: (index: number, grade: GradeSettings) => void;
  updateTransition: (index: number, transition: string) => void;
  updateCrop: (index: number, crop: CropSettings | null) => void;
  updateSamMask: (index: number, sam: SamMaskSettings | null) => void;
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
  cropData,
  samData,
  setSegmentState,
  updateTrim,
  updateGrade,
  updateTransition,
  updateCrop,
  updateSamMask,
  acceptAll,
  rejectAll,
  acceptedCount,
  totalSegments,
  onRender,
  running,
}: ReviewPanelProps) => {
  const [expandedIndex, setExpandedIndex]       = useState<number | null>(null);
  const [selectedTimelineIndex, setSelectedTimelineIndex] = useState<number | null>(null);
  const [orderedSegments, setOrderedSegments]  = useState<Segment[]>(segments);
  const [bulkSelected, setBulkSelected]        = useState<Set<number>>(new Set());
  const selectAllRef                           = useRef<HTMLInputElement>(null);
  const cardRefs                               = useRef<Record<number, HTMLDivElement | null>>({});

  const allSelected  = segments.length > 0 && bulkSelected.size === segments.length;
  const someSelected = bulkSelected.size > 0 && !allSelected;

  // Set indeterminate state on the "select all" checkbox
  useEffect(() => {
    if (selectAllRef.current) selectAllRef.current.indeterminate = someSelected;
  }, [someSelected]);

  // Keep orderedSegments in sync when segments prop changes
  useEffect(() => {
    setOrderedSegments(segments);
  }, [segments]);

  // Clear selection when segment list changes (e.g. after reset)
  useEffect(() => {
    setBulkSelected(new Set());
  }, [segments.length]);

  const handleTimelineSelect = useCallback((idx: number) => {
    setSelectedTimelineIndex(idx);
    setExpandedIndex(idx);
    // Scroll matching card into view
    const el = cardRefs.current[idx];
    if (el) el.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, []);

  const toggleBulk = useCallback((idx: number) => {
    setBulkSelected((prev) => {
      const next = new Set(prev);
      if (next.has(idx)) next.delete(idx); else next.add(idx);
      return next;
    });
  }, []);

  const handleSelectAll = () => {
    if (allSelected) {
      setBulkSelected(new Set());
    } else {
      setBulkSelected(new Set(segments.map((_, i) => i)));
    }
  };

  const bulkAccept = () => {
    bulkSelected.forEach((idx) => setSegmentState(idx, "accepted"));
    setBulkSelected(new Set());
  };

  const bulkReject = () => {
    bulkSelected.forEach((idx) => setSegmentState(idx, "rejected"));
    setBulkSelected(new Set());
  };

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
    <div className="h-full flex flex-col overflow-hidden">
      {/* Timeline */}
      <SegmentTimeline
        segments={orderedSegments}
        segmentStates={segmentStates}
        selectedIndex={selectedTimelineIndex}
        onSelect={handleTimelineSelect}
        onReorder={setOrderedSegments}
      />

      {/* Scrollable card list */}
      <div className="flex-1 overflow-y-auto px-6 py-4 space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          {/* Select-all checkbox — supports indeterminate state */}
          <input
            ref={selectAllRef}
            type="checkbox"
            checked={allSelected}
            onChange={handleSelectAll}
            className="w-4 h-4 cursor-pointer rounded accent-lime-400 flex-shrink-0"
            title="Select / deselect all"
          />
          <div>
            <h2 className="text-lg font-semibold text-foreground">Review segments</h2>
            <p className="text-sm text-muted-foreground">{acceptedCount}/{totalSegments} accepted</p>
          </div>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          {bulkSelected.size > 0 ? (
            <>
              <span className="text-xs text-muted-foreground font-mono">{bulkSelected.size} selected</span>
              <button
                onClick={bulkAccept}
                className="text-xs px-3 py-2 rounded-md border border-primary/50 text-primary hover:bg-primary/10 transition-colors"
              >
                Accept ({bulkSelected.size})
              </button>
              <button
                onClick={bulkReject}
                className="text-xs px-3 py-2 rounded-md border border-destructive/50 text-destructive hover:bg-destructive/10 transition-colors"
              >
                Reject ({bulkSelected.size})
              </button>
            </>
          ) : (
            <>
              <button onClick={acceptAll} className="text-xs px-3 py-2 rounded-md border border-border/60 hover:border-primary/40">
                Accept all
              </button>
              <button onClick={rejectAll} className="text-xs px-3 py-2 rounded-md border border-border/60 hover:border-primary/40">
                Reject all
              </button>
            </>
          )}
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
        {orderedSegments.map((segment, index) => {
          const trim       = trimData[index]  ?? { start: segment.start, end: segment.end };
          const grade      = gradeData[index] ?? { brightness: 0, contrast: 0, saturation: 0, temp: 0, lut: "none" };
          const crop       = cropData[index]  ?? null;
          const sam        = samData[index]   ?? null;
          const decision   = segmentStates[index];

          return (
            <motion.div
              key={`${segment.video_path ?? ""}-${segment.start}-${segment.end}-${index}`}
              ref={(el: HTMLDivElement | null) => { cardRefs.current[index] = el; }}
              initial={{ opacity: 0, x: -8 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ delay: index * 0.04, duration: 0.2 }}
            >
              <SegmentCard
                segment={segment}
                index={index}
                decision={decision}
                trim={trim}
                grade={grade}
                crop={crop}
                sam={sam}
                transition={transitionData[index] ?? "cut"}
                prevSegment={index > 0 ? orderedSegments[index - 1] : null}
                prevTrim={index > 0 ? (trimData[index - 1] ?? { start: orderedSegments[index - 1].start, end: orderedSegments[index - 1].end }) : null}
                onDecision={(d) => setSegmentState(index, d)}
                onTrimChange={(s, e) => updateTrim(index, s, e)}
                onGradeChange={(g) => updateGrade(index, g)}
                onCropChange={(c) => updateCrop(index, c)}
                onSamChange={(s) => updateSamMask(index, s)}
                onTransitionChange={(t) => updateTransition(index, t)}
                isExpanded={expandedIndex === index}
                onExpand={() => { setExpandedIndex(index); setSelectedTimelineIndex(index); }}
                onCollapse={() => { setExpandedIndex(null); setSelectedTimelineIndex(null); }}
                checked={bulkSelected.has(index)}
                onCheck={() => toggleBulk(index)}
              />
            </motion.div>
          );
        })}
      </div>
      </div>  {/* end scrollable card list */}
    </div>
  );
};

// ── Output panel ───────────────────────────────────────────────────────────────

interface OutputPanelProps {
  outputInfo: OutputInfo | null;
  running: boolean;
  phase: string;
  logs: string[];
  ffmpegProgress: number | null;
}

const RENDER_PHASE_LABEL: Record<string, string> = {
  analysing: "Scoring clips...",
  reviewing: "Selecting segments...",
  rendering: "Rendering...",
  done:      "Complete",
};

function fmtSecs(secs: number): string {
  const m = Math.floor(secs / 60).toString().padStart(2, "0");
  const s = Math.floor(secs % 60).toString().padStart(2, "0");
  return `${m}:${s}`;
}

const OutputPanel = ({ outputInfo, running, phase, logs, ffmpegProgress }: OutputPanelProps) => {
  const videoRef              = useRef<HTMLVideoElement>(null);
  const [playing, setPlaying] = useState(false);
  const [vidTime, setVidTime] = useState(0);
  const [vidDur, setVidDur]   = useState(0);
  const [volume, setVolume]   = useState(1);
  const [elapsed, setElapsed] = useState(0);

  const isActive    = running && phase !== "done" && phase !== "idle" && phase !== "error";
  const progressPct = ffmpegProgress !== null ? Math.round(ffmpegProgress * 100) : null;
  const recentLogs  = logs.slice(-3);
  const hasOutput   = !!outputInfo?.path;

  // Elapsed timer while pipeline active
  useEffect(() => {
    if (!running) { setElapsed(0); return; }
    setElapsed(0);
    const id = setInterval(() => setElapsed((s) => s + 1), 1000);
    return () => clearInterval(id);
  }, [running]);

  const togglePlay = () => {
    const v = videoRef.current;
    if (!v) return;
    if (v.paused) v.play().catch(() => {}); else v.pause();
  };

  const seekClick = (e: React.MouseEvent<HTMLDivElement>) => {
    const v = videoRef.current;
    if (!v || !vidDur) return;
    const rect = e.currentTarget.getBoundingClientRect();
    v.currentTime = ((e.clientX - rect.left) / rect.width) * vidDur;
  };

  const handleVolumeChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const val = Number(e.target.value);
    setVolume(val);
    if (videoRef.current) videoRef.current.volume = val;
  };

  const toggleFullscreen = () => {
    const v = videoRef.current;
    if (!v) return;
    if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
    else v.requestFullscreen?.().catch(() => {});
  };

  return (
    <div className="h-full overflow-y-auto flex flex-col">

      {/* ── Render progress (visible while pipeline runs) ─── */}
      {isActive && (
        <div
          className="px-6 py-4 flex-shrink-0"
          style={{ borderBottom: "1px solid var(--border-subtle)" }}
        >
          <div className="flex items-center justify-between mb-2">
            <span style={{ fontFamily: "var(--font-display)", fontSize: 13, fontWeight: 500, color: "var(--text-primary)" }}>
              {RENDER_PHASE_LABEL[phase] ?? "Processing..."}
            </span>
            <span style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--text-muted)" }}>
              {progressPct !== null ? `${progressPct}%` : ""}
              {elapsed > 0 ? `  ·  ${fmtSecs(elapsed)} elapsed` : ""}
            </span>
          </div>
          {/* Progress bar with shimmer */}
          <div className="w-full h-1.5 rounded-full overflow-hidden" style={{ background: "var(--bg-tertiary)" }}>
            <div
              className="h-full rounded-full relative overflow-hidden"
              style={{
                width: `${progressPct ?? 0}%`,
                background: "var(--accent-primary)",
                transition: "width 0.4s ease",
              }}
            >
              <div
                className="absolute inset-0"
                style={{
                  background: "linear-gradient(90deg, transparent 0%, rgba(255,255,255,0.25) 50%, transparent 100%)",
                  backgroundSize: "200% 100%",
                  animation: "shimmer 1.5s linear infinite",
                }}
              />
            </div>
          </div>
          {/* Last 3 log lines */}
          {recentLogs.length > 0 && (
            <div className="mt-2 space-y-0.5">
              {recentLogs.map((line, i) => (
                <p key={i} className="truncate" style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--text-muted)" }}>
                  {line}
                </p>
              ))}
            </div>
          )}
        </div>
      )}

      {/* ── Video preview ─────────────────────────────────── */}
      {hasOutput ? (
        <div
          className="flex flex-col items-center px-6 py-6 gap-4 flex-1"
          style={{ maxWidth: 960, width: "100%", margin: "0 auto" }}
        >
          {/* 16:9 video container */}
          <div className="w-full rounded-lg overflow-hidden bg-black" style={{ aspectRatio: "16/9" }}>
            <video
              ref={videoRef}
              key={outputInfo!.path!}
              src={outputInfo!.path!}
              className="w-full h-full object-contain"
              onTimeUpdate={() => setVidTime(videoRef.current?.currentTime ?? 0)}
              onLoadedMetadata={() => setVidDur(videoRef.current?.duration ?? 0)}
              onPlay={() => setPlaying(true)}
              onPause={() => setPlaying(false)}
              onEnded={() => setPlaying(false)}
            />
          </div>

          {/* Custom controls */}
          <div className="w-full space-y-2">
            {/* Scrub bar */}
            <div
              onClick={seekClick}
              className="w-full h-1.5 rounded-full cursor-pointer"
              style={{ background: "var(--bg-tertiary)" }}
            >
              <div
                className="h-full rounded-full"
                style={{
                  width: vidDur ? `${(vidTime / vidDur) * 100}%` : "0%",
                  background: "var(--accent-primary)",
                  transition: "width 0.1s linear",
                }}
              />
            </div>

            {/* Buttons row */}
            <div className="flex items-center gap-3">
              <button
                onClick={togglePlay}
                className="flex-shrink-0 w-8 h-8 rounded flex items-center justify-center transition-all"
                style={{ background: "var(--bg-tertiary)", border: "1px solid var(--border-subtle)", color: "var(--accent-primary)" }}
              >
                {playing ? <Pause className="w-3.5 h-3.5" /> : <Play className="w-3.5 h-3.5" />}
              </button>
              <span style={{ fontFamily: "var(--font-mono)", fontSize: 12, color: "var(--text-secondary)" }}>
                {fmtSecs(vidTime)} / {fmtSecs(vidDur)}
              </span>
              <div className="flex-1" />
              <Volume2 className="w-3.5 h-3.5 flex-shrink-0" style={{ color: "var(--text-muted)" }} />
              <input
                type="range" min={0} max={1} step={0.05}
                value={volume}
                onChange={handleVolumeChange}
                className="w-20"
                style={{ accentColor: "var(--accent-primary)" }}
              />
              <button
                onClick={toggleFullscreen}
                className="flex-shrink-0 w-8 h-8 rounded flex items-center justify-center transition-all"
                style={{ background: "var(--bg-tertiary)", border: "1px solid var(--border-subtle)", color: "var(--text-secondary)" }}
              >
                <Maximize2 className="w-3.5 h-3.5" />
              </button>
            </div>
          </div>

          {/* File info + download */}
          <div
            className="w-full flex items-center justify-between flex-wrap gap-3 pt-3"
            style={{ borderTop: "1px solid var(--border-subtle)" }}
          >
            <div className="flex items-center gap-4 flex-wrap" style={{ fontFamily: "var(--font-mono)", fontSize: 12, color: "var(--text-muted)" }}>
              <span>{outputInfo!.name ?? "compilation.mp4"}</span>
              {outputInfo!.size_mb != null && <span>{outputInfo!.size_mb} MB</span>}
              {vidDur > 0 && <span>{fmtSecs(vidDur)}</span>}
            </div>
            <a
              href={outputInfo!.path!}
              download={outputInfo!.name}
              className="flex items-center gap-2 px-4 py-2 rounded text-sm font-semibold gradient-primary-btn"
              style={{ color: "var(--bg-primary)", fontFamily: "var(--font-display)", textDecoration: "none" }}
            >
              ↓ Download{outputInfo!.size_mb != null ? ` · ${outputInfo!.size_mb} MB` : ""}
            </a>
          </div>
        </div>

      ) : !running ? (
        /* ── Empty state ──────────────────────────────────── */
        <div className="flex-1 flex items-center justify-center" style={{ minHeight: "60vh" }}>
          <div className="text-center space-y-4">
            <div
              className="mx-auto w-20 h-20 rounded-2xl flex items-center justify-center"
              style={{ background: "var(--accent-muted)", animation: "pulse-glow 2.5s ease-in-out infinite" }}
            >
              <svg width="32" height="32" viewBox="0 0 32 32" fill="none">
                <path d="M8 5l18 11L8 27V5z" fill="var(--accent-primary)" />
              </svg>
            </div>
            <div>
              <h2 style={{ fontFamily: "var(--font-display)", fontSize: 18, fontWeight: 600, color: "var(--text-primary)" }}>
                No output yet
              </h2>
              <p style={{ fontSize: 13, color: "var(--text-muted)", marginTop: 6 }}>
                Run the pipeline to generate your reel
              </p>
            </div>
          </div>
        </div>
      ) : null}
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

export default MainContent;
