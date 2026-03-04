import { AlertTriangle, Film, Cpu } from "lucide-react";
import type { PipelineStatus } from "@/lib/api";

const phaseLabels: Record<string, string> = {
  idle:      "Idle",
  analysing: "Analysing",
  reviewing: "Reviewing",
  rendering: "Rendering",
  done:      "Done",
  error:     "Error",
};

interface DashboardHeaderProps {
  status:      PipelineStatus | null;
  clipCount:   number;
  logCount:    number;
  warnings:    string[];
  errorDetail?: string | null;
}

const DashboardHeader = ({ status, clipCount, logCount, warnings, errorDetail }: DashboardHeaderProps) => {
  const phase   = status?.phase ?? "idle";
  const label   = phaseLabels[phase] ?? phase;
  const active  = !!status?.running;
  const isError = phase === "error";

  const dotColor = isError
    ? "bg-red-500"
    : active
      ? "bg-amber-400"
      : "bg-zinc-500";

  const priorityMessage = warnings[0] ?? (isError ? errorDetail ?? status?.last_error ?? "" : "");
  const tooltip = warnings.slice(0, 3).join(" • ") || priorityMessage;

  return (
    <header
      className="relative flex items-center justify-between px-6 z-10 flex-shrink-0"
      style={{
        height: 48,
        background: "var(--bg-secondary)",
        borderBottom: "1px solid var(--border-subtle)",
        boxShadow: "0 1px 0 var(--border-subtle)",
      }}
    >
      {/* ── Left: wordmark ─────────────────────────────────────────────── */}
      <div className="flex items-center gap-3">
        {/* Amber hexagon icon */}
        <svg width="20" height="20" viewBox="0 0 20 20" fill="none" aria-hidden>
          <path
            d="M10 1L17.66 5.5V14.5L10 19L2.34 14.5V5.5L10 1Z"
            fill="var(--accent-primary)"
            opacity="0.9"
          />
          <path
            d="M10 1L17.66 5.5V14.5L10 19L2.34 14.5V5.5L10 1Z"
            stroke="var(--accent-secondary)"
            strokeWidth="0.5"
            fill="none"
          />
        </svg>
        <span
          className="text-sm font-semibold tracking-tight"
          style={{ fontFamily: "var(--font-display)", color: "var(--text-primary)" }}
        >
          Video<span style={{ color: "var(--accent-primary)" }}>Agent</span>
        </span>
      </div>

      {/* ── Center: phase status pill ───────────────────────────────────── */}
      <div
        className="absolute left-1/2 -translate-x-1/2 flex items-center gap-2 px-4 py-1.5 rounded-full"
        style={{ background: "var(--bg-tertiary)", border: "1px solid var(--border-subtle)" }}
      >
        {/* Pulsing dot */}
        <span className="relative flex h-2 w-2 flex-shrink-0">
          {active && !isError && (
            <span
              className="absolute inline-flex h-full w-full rounded-full animate-ping-amber"
              style={{ background: "var(--accent-primary)", opacity: 0.6 }}
            />
          )}
          <span className={`relative inline-flex h-2 w-2 rounded-full ${dotColor}`} />
        </span>
        <span
          className="text-xs font-medium"
          style={{
            fontFamily: "var(--font-display)",
            color: isError ? "#f87171" : active ? "var(--accent-primary)" : "var(--text-secondary)",
          }}
        >
          {label}
        </span>
      </div>

      {/* ── Right: stats ────────────────────────────────────────────────── */}
      <div className="flex items-center gap-3">
        {priorityMessage && (
          <div
            className="flex items-center gap-1.5 px-2.5 py-1 rounded text-xs max-w-[200px]"
            style={{
              background: "rgba(248,113,113,0.1)",
              border: "1px solid rgba(248,113,113,0.3)",
              color: "#f87171",
            }}
            title={tooltip}
          >
            <AlertTriangle className="w-3 h-3 flex-shrink-0" />
            <span className="truncate">{priorityMessage}</span>
          </div>
        )}

        <div
          className="flex items-center gap-1.5 px-2.5 py-1 rounded text-xs"
          style={{ background: "var(--bg-tertiary)", border: "1px solid var(--border-subtle)" }}
        >
          <Film className="w-3 h-3" style={{ color: "var(--text-secondary)" }} />
          <span style={{ fontFamily: "var(--font-mono)", color: "var(--text-primary)" }}>
            {clipCount}
          </span>
          <span style={{ color: "var(--text-muted)" }}>clips</span>
        </div>

        <div
          className="flex items-center gap-1.5 px-2.5 py-1 rounded text-xs"
          style={{ background: "var(--bg-tertiary)", border: "1px solid var(--border-subtle)" }}
        >
          <span style={{ color: "var(--text-muted)" }}>Logs</span>
          <span style={{ fontFamily: "var(--font-mono)", color: "var(--text-primary)" }}>
            {logCount}
          </span>
        </div>

        <div
          className="flex items-center gap-1.5 px-2.5 py-1 rounded text-xs"
          style={{ background: "var(--bg-tertiary)", border: "1px solid var(--border-subtle)" }}
        >
          <Cpu className="w-3 h-3" style={{ color: "var(--text-muted)" }} />
          <span style={{ fontFamily: "var(--font-mono)", color: "var(--text-muted)" }}>GPU: --</span>
        </div>
      </div>
    </header>
  );
};

export default DashboardHeader;
