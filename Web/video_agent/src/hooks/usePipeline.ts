import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  PipelineStatus,
  PipelineConfig,
  PipelinePhase,
  GradeSettings,
  TrimState,
  DEFAULT_GRADE,
  defaultConfig,
  OutputInfo,
  ClipInfo,
  fetchStatus,
  fetchTemplates,
  runPipelineRequest,
  renderSegmentsRequest,
  cancelPipelineRequest,
  fetchLatestOutput,
  fetchClips,
} from "@/lib/api";

export type SegmentDecision = "accepted" | "rejected";

const STATUS_INTERVAL = 1500;
const LOG_LIMIT = 1000;
const CONFIG_STORAGE_KEY = "videoAgentPipelineConfig";

export function usePipeline() {
  const [config, setConfig] = useState<PipelineConfig>(() => {
    if (typeof window === "undefined") return defaultConfig;
    try {
      const raw = window.localStorage.getItem(CONFIG_STORAGE_KEY);
      if (raw) {
        return { ...defaultConfig, ...JSON.parse(raw) };
      }
    } catch (err) {
      console.warn("Failed to parse saved pipeline config", err);
    }
    return defaultConfig;
  });
  const [templates, setTemplates] = useState<string[]>([]);
  const [clips, setClips] = useState<ClipInfo[]>([]);
  const [status, setStatus] = useState<PipelineStatus | null>(null);
  const [logs, setLogs] = useState<string[]>([]);
  const [segmentStates, setSegmentStates] = useState<Record<number, SegmentDecision>>({});
  const [trimData, setTrimData] = useState<Record<number, TrimState>>({});
  const [gradeData, setGradeData] = useState<Record<number, GradeSettings>>({});
  const [transitionData, setTransitionData] = useState<Record<number, string>>({});
  const [outputInfo, setOutputInfo] = useState<OutputInfo | null>(null);
  const [error, setError] = useState<string | null>(null);

  const lastSegmentsSignature = useRef<string | null>(null);

  useEffect(() => {
    if (typeof window === "undefined") return;
    try {
      window.localStorage.setItem(CONFIG_STORAGE_KEY, JSON.stringify(config));
    } catch (err) {
      console.warn("Failed to store pipeline config", err);
    }
  }, [config]);

  useEffect(() => {
    fetchTemplates()
      .then((data) => setTemplates(data))
      .catch((err) => console.error("Failed to load templates", err));
    fetchClips()
      .then((data) => setClips(data))
      .catch((err) => console.error("Failed to load clips", err));
  }, []);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const data = await fetchStatus();
        if (!cancelled) {
          setStatus(data);
        }
      } catch (err) {
        console.error("Status poll failed", err);
      }
    };
    load();
    const id = setInterval(load, STATUS_INTERVAL);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  useEffect(() => {
    if (!status) return;
    const segs = status.selected_segments ?? [];
    const signature = segs.map((seg) => `${seg.video_path ?? ""}-${seg.start}-${seg.end}`).join("|");
    if (signature !== lastSegmentsSignature.current) {
      lastSegmentsSignature.current = signature;
      if (segs.length) {
        const nextTrim: Record<number, TrimState> = {};
        const nextGrade: Record<number, GradeSettings> = {};
        segs.forEach((seg, idx) => {
          nextTrim[idx] = { start: seg.start, end: seg.end };
          nextGrade[idx] = { ...DEFAULT_GRADE };
        });
        // Start with no explicit decisions — segments are neutral (included in
        // render by default since the filter is !== "rejected"). This ensures
        // clicking Accept gives visible feedback instead of being a no-op.
        setSegmentStates({});
        setTrimData(nextTrim);
        setGradeData(nextGrade);
        setTransitionData({});
      } else {
        setSegmentStates({});
        setTrimData({});
        setGradeData({});
        setTransitionData({});
      }
    }
  }, [status]);

  useEffect(() => {
    let stopped = false;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let source: EventSource | null = null;

    const connect = () => {
      if (stopped) return;
      source = new EventSource("/api/stream");
      source.onmessage = (event) => {
        try {
          const payload = JSON.parse(event.data);
          if (payload?.ping) return;
          const line = typeof payload === "string" ? payload : JSON.stringify(payload);
          setLogs((prev) => {
            const next = [...prev, line];
            if (next.length > LOG_LIMIT) {
              return next.slice(next.length - LOG_LIMIT);
            }
            return next;
          });
        } catch {
          if (event.data && event.data !== "[DONE]") {
            setLogs((prev) => [...prev, event.data]);
          }
        }
      };
      source.onerror = () => {
        source?.close();
        if (!stopped) {
          reconnectTimer = setTimeout(connect, 2000);
        }
      };
    };

    connect();
    return () => {
      stopped = true;
      source?.close();
      if (reconnectTimer) clearTimeout(reconnectTimer);
    };
  }, []);

  useEffect(() => {
    if (!status) return;
    if (status.phase === "done") {
      fetchLatestOutput()
        .then(setOutputInfo)
        .catch((err) => console.error("Failed to fetch output info", err));
    }
    if (status.phase === "rendering") {
      setOutputInfo(null);
    }
  }, [status?.phase]);

  const segments = status?.selected_segments ?? [];
  const acceptedSegments = useMemo(
    () => segments.filter((_, idx) => segmentStates[idx] !== "rejected"),
    [segments, segmentStates],
  );

  const updateConfig = useCallback((changes: Partial<PipelineConfig>) => {
    setConfig((prev) => ({ ...prev, ...changes }));
  }, []);

  const runPipeline = useCallback(async () => {
    setError(null);
    try {
      await runPipelineRequest(config);
      setLogs([]);
      if (config.disableCache) {
        setConfig((prev) => ({ ...prev, disableCache: false }));
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : "Failed to start pipeline";
      setError(message);
      throw err;
    }
  }, [config]);

  const renderAccepted = useCallback(async () => {
    // Build enriched list from all segments so we can look up trim/grade by original index.
    const enriched = segments
      .map((seg, idx) => {
        const trim = trimData[idx];
        const grade = gradeData[idx] ?? DEFAULT_GRADE;
        return {
          ...seg,
          trimStart: trim?.start ?? seg.start,
          trimEnd: trim?.end ?? seg.end,
          grade,
          transition_in: transitionData[idx] ?? "cut",
        };
      })
      .filter((_, idx) => segmentStates[idx] !== "rejected");
    if (!enriched.length) throw new Error("No segments accepted");
    setError(null);
    await renderSegmentsRequest(enriched, config).catch((err) => {
      const message = err instanceof Error ? err.message : "Failed to start render";
      setError(message);
      throw err;
    });
  }, [segments, segmentStates, trimData, gradeData, transitionData, config]);

  const cancelPipeline = useCallback(async () => {
    setError(null);
    try {
      await cancelPipelineRequest();
    } catch (err) {
      const message = err instanceof Error ? err.message : "Failed to cancel";
      setError(message);
      throw err;
    }
  }, []);

  const setSegmentState = useCallback((index: number, decision: SegmentDecision) => {
    setSegmentStates((prev) => ({ ...prev, [index]: decision }));
  }, []);

  const updateTrim = useCallback((index: number, start: number, end: number) => {
    setTrimData((prev) => ({ ...prev, [index]: { start, end } }));
  }, []);

  const updateGrade = useCallback((index: number, grade: GradeSettings) => {
    setGradeData((prev) => ({ ...prev, [index]: grade }));
  }, []);

  const updateTransition = useCallback((index: number, transition: string) => {
    setTransitionData((prev) => ({ ...prev, [index]: transition }));
  }, []);

  const acceptAll = useCallback(() => {
    const next: Record<number, SegmentDecision> = {};
    segments.forEach((_, idx) => {
      next[idx] = "accepted";
    });
    setSegmentStates(next);
  }, [segments]);

  const rejectAll = useCallback(() => {
    const next: Record<number, SegmentDecision> = {};
    segments.forEach((_, idx) => {
      next[idx] = "rejected";
    });
    setSegmentStates(next);
  }, [segments]);

  const clearLogs = useCallback(() => setLogs([]), []);

  const resetAll = useCallback(async () => {
    // Cancel if running, then wipe all local state so the UI returns to its
    // initial blank state ready for a fresh pipeline run.
    try {
      if (status?.running) await cancelPipelineRequest();
    } catch { /* ignore */ }
    setLogs([]);
    setSegmentStates({});
    setTrimData({});
    setGradeData({});
    setTransitionData({});
    setOutputInfo(null);
    setError(null);
    // Reset the segment signature so new segments will be initialised normally
    lastSegmentsSignature.current = null;
  }, [status?.running]);

  const phase: PipelinePhase = status?.phase ?? "idle";
  const isRunning = Boolean(status?.running);
  const clipCount = status?.clip_count ?? clips.length;
  const warnings = status?.warnings ?? [];
  const ffmpegProgress = status?.ffmpeg_progress ?? null;
  const segmentCounts = status?.segment_counts;
  const errorDetail = status?.error_detail ?? null;

  return {
    config,
    templates,
    clips,
    status,
    logs,
    segments,
    segmentStates,
    trimData,
    gradeData,
    transitionData,
    acceptedSegments,
    outputInfo,
    error,
    phase,
    isRunning,
    clipCount,
    warnings,
    ffmpegProgress,
    segmentCounts,
    errorDetail,
    updateConfig,
    runPipeline,
    renderAccepted,
    cancelPipeline,
    setSegmentState,
    updateTrim,
    updateGrade,
    updateTransition,
    acceptAll,
    rejectAll,
    clearLogs,
    resetAll,
  };
}

export type SegmentState = Record<number, SegmentDecision>;
