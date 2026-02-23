import { useState } from "react";
import { Trash2, Terminal, FileVideo, CheckCircle2, Activity } from "lucide-react";

const tabs = [
  { id: "Log" as const, icon: Terminal },
  { id: "Review" as const, icon: CheckCircle2 },
  { id: "Output" as const, icon: FileVideo },
];
type Tab = "Log" | "Review" | "Output";

const MainContent = () => {
  const [activeTab, setActiveTab] = useState<Tab>("Log");

  return (
    <main className="flex-1 flex flex-col min-w-0 relative">
      {/* Background pattern */}
      <div className="absolute inset-0 dot-grid opacity-30 pointer-events-none" />
      <div className="absolute inset-0 scanline pointer-events-none" />

      {/* Tabs */}
      <div className="relative z-10 border-b border-border/50 glass-surface">
        <div className="flex">
          {tabs.map(({ id, icon: Icon }) => (
            <button
              key={id}
              onClick={() => setActiveTab(id)}
              className={`flex items-center gap-2 px-6 py-3.5 text-sm font-medium transition-all relative group ${
                activeTab === id
                  ? "text-foreground"
                  : "text-muted-foreground hover:text-secondary-foreground"
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

      {/* Status Bar */}
      <div className="relative z-10 flex items-center justify-between px-6 py-3.5 border-b border-border/30">
        <div className="flex items-center gap-2.5">
          {[
            { label: "Step", value: "—" },
            { label: "Segments", value: "0" },
            { label: "Elapsed", value: "00:00" },
          ].map(({ label, value }) => (
            <span key={label} className="inline-flex items-center gap-2 px-3.5 py-1.5 rounded-lg glass-card text-sm">
              <span className="text-muted-foreground">{label}</span>
              <span className="font-mono text-foreground/70">{value}</span>
            </span>
          ))}
        </div>
        <button className="flex items-center gap-1.5 text-sm text-muted-foreground hover:text-destructive transition-colors group">
          <Trash2 className="w-4 h-4 group-hover:scale-110 transition-transform" />
          Clear
        </button>
      </div>

      {/* Content Area */}
      <div className="relative z-10 flex-1 flex items-center justify-center">
        <div className="text-center space-y-6 animate-fade-in">
          {/* Icon with glow */}
          <div className="relative mx-auto w-20 h-20">
            <div className="absolute inset-0 rounded-2xl bg-primary/10 animate-pulse-glow" />
            <div className="relative w-20 h-20 rounded-2xl surface-elevated flex items-center justify-center animate-float">
              <Terminal className="w-8 h-8 text-primary/70" />
            </div>
          </div>

          <div className="space-y-2">
            <h2 className="text-xl font-semibold text-foreground tracking-tight">Ready to run</h2>
            <p className="text-sm text-muted-foreground max-w-sm leading-relaxed">
              Configure your pipeline settings and click <span className="text-primary font-medium">Run Pipeline</span> to begin processing
            </p>
          </div>

          {/* Activity indicator */}
          <div className="flex items-center justify-center gap-2 text-xs text-muted-foreground/60">
            <Activity className="w-3.5 h-3.5" />
            <span className="font-mono tracking-wider">AWAITING INPUT</span>
          </div>
        </div>
      </div>
    </main>
  );
};

export default MainContent;
