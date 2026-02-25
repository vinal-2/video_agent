import { Zap, Film, Wifi, AlertTriangle } from "lucide-react";
import type { PipelineStatus } from "@/lib/api";

const phaseLabels: Record<string, string> = {
  idle: "Idle",
  analysing: "Analysing",
  reviewing: "Reviewing",
  rendering: "Rendering",
  done: "Done",
  error: "Error",
};

const phaseTone: Record<string, string> = {
  idle: "text-status-idle",
  analysing: "text-status-online",
  reviewing: "text-status-online",
  rendering: "text-status-online",
  done: "text-status-online",
  error: "text-status-error",
};

interface DashboardHeaderProps {
  status: PipelineStatus | null;
  clipCount: number;
  logCount: number;
  warnings: string[];
  errorDetail?: string | null;
}

const DashboardHeader = ({ status, clipCount, logCount, warnings, errorDetail }: DashboardHeaderProps) => {
  const phase = status?.phase ?? "idle";
  const label = phaseLabels[phase] ?? phase;
  const statusClass = phaseTone[phase] ?? "text-muted-foreground";
  const dotClass =
    phase === "error"
      ? "bg-status-error"
      : status?.running
        ? "bg-status-online"
        : "bg-status-idle";

  return (
    <header className="relative flex items-center justify-between px-6 py-3.5 glass-surface z-10">
      <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-primary/20 to-transparent" />

      <div className="flex items-center gap-3.5">
        <div className="relative flex items-center justify-center w-10 h-10 rounded-xl gradient-primary-btn glow-primary">
          <Zap className="w-5 h-5 text-primary-foreground" />
        </div>
        <div>
          <h1 className="text-base font-semibold text-foreground leading-tight tracking-tight">
            Video<span className="text-gradient">Agent</span>
          </h1>
          <p className="text-[11px] text-muted-foreground font-medium tracking-widest uppercase">AI Compiler</p>
        </div>
      </div>

      <div className="flex items-center gap-3">
        <div className="flex items-center gap-2.5 px-4 py-2 rounded-full glass-card">
          <span className="relative flex h-2.5 w-2.5">
            <span className={`absolute inline-flex h-full w-full rounded-full opacity-50 ${dotClass} animate-ping`} />
            <span className={`relative inline-flex rounded-full h-2.5 w-2.5 ${dotClass}`} />
          </span>
          <span className="text-sm text-foreground font-medium flex items-center gap-1.5">
            <Film className="w-3.5 h-3.5 text-muted-foreground" />
            {clipCount} clips detected
          </span>
        </div>
        <div className="hidden md:flex items-center gap-2 px-3 py-1.5 rounded-lg glass-card">
          <span className="text-xs text-muted-foreground">Logs</span>
          <span className="text-sm font-mono text-foreground">{logCount}</span>
        </div>
      </div>

      <div className="flex flex-col gap-1 items-end">
        <div className="flex items-center gap-2.5 px-3 py-1.5 rounded-lg glass-card">
          <Wifi className={`w-3.5 h-3.5 ${statusClass}`} />
          <span className={`text-sm font-medium ${statusClass}`}>{label}</span>
        </div>
        {(() => {
          const priorityMessage = warnings[0] ?? (phase === "error" ? errorDetail ?? status?.last_error ?? "" : "");
          if (!priorityMessage) return null;
          const tooltip = warnings.slice(0, 3).join(" • ") || priorityMessage;
          return (
            <div
              className="flex items-center gap-1.5 text-xs text-destructive bg-destructive/10 px-3 py-1 rounded-md border border-destructive/30 max-w-xs"
              title={tooltip}
            >
              <AlertTriangle className="w-3 h-3" />
              <span className="truncate">{priorityMessage}</span>
            </div>
          );
        })()}
      </div>
    </header>
  );
};

export default DashboardHeader;
