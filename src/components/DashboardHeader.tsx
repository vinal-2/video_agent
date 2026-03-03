import { Zap, Film, Wifi } from "lucide-react";

const DashboardHeader = () => {
  return (
    <header className="relative flex items-center justify-between px-6 py-3.5 glass-surface z-10">
      {/* Subtle top border glow */}
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
            <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-status-online opacity-50" />
            <span className="relative inline-flex rounded-full h-2.5 w-2.5 bg-status-online" />
          </span>
          <span className="text-sm text-foreground font-medium flex items-center gap-1.5">
            <Film className="w-3.5 h-3.5 text-muted-foreground" />
            12 clips loaded
          </span>
        </div>
      </div>

      <div className="flex items-center gap-2.5 px-3 py-1.5 rounded-lg glass-card">
        <Wifi className="w-3.5 h-3.5 text-status-idle" />
        <span className="text-sm text-muted-foreground font-medium">Idle</span>
      </div>
    </header>
  );
};

export default DashboardHeader;
