import { Zap } from "lucide-react";

const DashboardHeader = () => {
  return (
    <header className="flex items-center justify-between px-6 py-3 border-b border-border bg-card">
      <div className="flex items-center gap-3">
        <div className="flex items-center justify-center w-9 h-9 rounded-lg bg-primary">
          <Zap className="w-5 h-5 text-primary-foreground" />
        </div>
        <div>
          <h1 className="text-base font-semibold text-foreground leading-tight">VideoAgent</h1>
          <p className="text-xs text-muted-foreground">AI Compiler</p>
        </div>
      </div>

      <div className="flex items-center gap-4">
        <div className="flex items-center gap-2 px-4 py-1.5 rounded-full bg-secondary border border-border">
          <span className="w-2 h-2 rounded-full bg-status-online animate-pulse-soft" />
          <span className="text-sm text-foreground font-medium">12 clips loaded</span>
        </div>
      </div>

      <div className="flex items-center gap-2">
        <span className="w-2 h-2 rounded-full bg-status-idle" />
        <span className="text-sm text-muted-foreground">Idle</span>
      </div>
    </header>
  );
};

export default DashboardHeader;
