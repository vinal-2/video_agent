import { useState } from "react";
import { Trash2, Terminal } from "lucide-react";

const tabs = ["Log", "Review", "Output"] as const;
type Tab = typeof tabs[number];

const MainContent = () => {
  const [activeTab, setActiveTab] = useState<Tab>("Log");

  return (
    <main className="flex-1 flex flex-col min-w-0 bg-background">
      {/* Tabs */}
      <div className="border-b border-border">
        <div className="flex">
          {tabs.map((tab) => (
            <button
              key={tab}
              onClick={() => setActiveTab(tab)}
              className={`px-5 py-3 text-sm font-medium transition-colors relative ${
                activeTab === tab
                  ? "text-foreground"
                  : "text-muted-foreground hover:text-foreground"
              }`}
            >
              {tab}
              {activeTab === tab && (
                <span className="absolute bottom-0 left-0 right-0 h-0.5 bg-tab-active rounded-t" />
              )}
            </button>
          ))}
        </div>
      </div>

      {/* Status Bar */}
      <div className="flex items-center justify-between px-5 py-3 border-b border-border">
        <div className="flex items-center gap-3">
          <span className="inline-flex items-center gap-2 px-3 py-1.5 rounded-md bg-secondary text-sm text-secondary-foreground">
            Step <span className="text-muted-foreground">—</span>
          </span>
          <span className="inline-flex items-center gap-2 px-3 py-1.5 rounded-md bg-secondary text-sm text-secondary-foreground">
            Segments <span className="font-mono text-muted-foreground">0</span>
          </span>
          <span className="inline-flex items-center gap-2 px-3 py-1.5 rounded-md bg-secondary text-sm text-secondary-foreground">
            Elapsed <span className="font-mono text-muted-foreground">00:00</span>
          </span>
        </div>
        <button className="flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground transition-colors">
          <Trash2 className="w-4 h-4" />
          Clear
        </button>
      </div>

      {/* Content Area */}
      <div className="flex-1 flex items-center justify-center">
        <div className="text-center space-y-4">
          <div className="mx-auto w-16 h-16 rounded-xl bg-secondary flex items-center justify-center">
            <Terminal className="w-7 h-7 text-muted-foreground" />
          </div>
          <div className="space-y-1.5">
            <h2 className="text-lg font-semibold text-foreground">Ready to run</h2>
            <p className="text-sm text-muted-foreground max-w-xs">
              Configure your pipeline settings and click Run Pipeline to begin processing
            </p>
          </div>
        </div>
      </div>
    </main>
  );
};

export default MainContent;
