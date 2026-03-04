import { useMemo } from "react";
import { Sparkles, Layers, Eye, Cpu, Trash2, SlidersHorizontal, XCircle } from "lucide-react";
import { Switch } from "@/components/ui/switch";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import type { PipelineConfig, PipelinePhase } from "@/lib/api";

const qualityOptions = [
  { label: "Proxy", desc: "Fast review", value: "proxy" },
  { label: "Normal", desc: "1080p balanced", value: "normal" },
  { label: "High", desc: "1080p max", value: "high" },
  { label: "4K", desc: "Source res", value: "4k" },
] as const;

interface PipelineSidebarProps {
  templates: string[];
  config: PipelineConfig;
  onConfigChange: (changes: Partial<PipelineConfig>) => void;
  onRun: () => Promise<void>;
  onCancel: () => Promise<void>;
  running: boolean;
  phase: PipelinePhase;
  error?: string | null;
}

const prettifyTemplate = (value: string) =>
  value
    .replace(/[_-]+/g, " ")
    .replace(/\b\w/g, (char) => char.toUpperCase());

const PipelineSidebar = ({
  templates,
  config,
  onConfigChange,
  onRun,
  onCancel,
  running,
  phase,
  error,
}: PipelineSidebarProps) => {
  const formattedTemplates = useMemo(() => {
    if (!templates.length) {
      return [{ value: config.template, label: prettifyTemplate(config.template) }];
    }
    return templates.map((value) => ({ value, label: prettifyTemplate(value) }));
  }, [templates, config.template]);

  const selectedTemplate =
    formattedTemplates.find((t) => t.value === config.template) ?? formattedTemplates[0];

  const disabledInputs = running || phase === "rendering";

  const bufferValue = Number.isFinite(config.buffer) ? config.buffer : 0;
  const visionMaxValue = Number.isFinite(config.visionMax) ? config.visionMax : 15;

  const handleNumberChange = (key: "buffer" | "visionMax", value: number) => {
    onConfigChange({ [key]: Number.isNaN(value) ? 0 : value });
  };

  const handleRun = () => {
    if (running) return;
    onRun().catch((err) => console.error(err));
  };

  const handleCancel = () => {
    onCancel().catch((err) => console.error(err));
  };

  return (
    <aside
      className="flex-shrink-0 flex flex-col h-full relative"
      style={{ width: 220, background: "var(--bg-secondary)", borderRight: "1px solid var(--border-subtle)" }}
    >
      <div className="flex-1 overflow-y-auto p-4 space-y-6 relative z-10">
        <div className="flex items-center gap-2" style={{ color: "var(--text-muted)" }}>
          <SlidersHorizontal className="w-3.5 h-3.5" />
          <span
            className="uppercase"
            style={{ fontFamily: "var(--font-display)", fontSize: 11, fontWeight: 500, letterSpacing: "0.1em", color: "var(--text-muted)" }}
          >
            Pipeline Config
          </span>
        </div>

        <div className="space-y-2 animate-fade-in" style={{ animationDelay: "0.05s" }}>
          <label
            className="block uppercase"
            style={{ fontFamily: "var(--font-display)", fontSize: 11, fontWeight: 500, letterSpacing: "0.1em", color: "var(--text-muted)" }}
          >
            Template
          </label>
          <Select
            value={config.template}
            onValueChange={(value) => onConfigChange({ template: value })}
            disabled={disabledInputs}
          >
            <SelectTrigger className="w-full surface-elevated border-border/50 text-sm font-medium">
              <SelectValue>{selectedTemplate?.label}</SelectValue>
            </SelectTrigger>
            <SelectContent>
              {formattedTemplates.map((t) => (
                <SelectItem key={t.value} value={t.value}>
                  {t.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <div className="space-y-2 animate-fade-in" style={{ animationDelay: "0.1s" }}>
          <label
            className="block uppercase"
            style={{ fontFamily: "var(--font-display)", fontSize: 11, fontWeight: 500, letterSpacing: "0.1em", color: "var(--text-muted)" }}
          >
            Output Quality
          </label>
          <div className="grid grid-cols-2 gap-1.5">
            {qualityOptions.map((q) => (
              <button
                key={q.value}
                onClick={() => onConfigChange({ quality: q.value })}
                disabled={disabledInputs}
                style={config.quality === q.value ? {
                  background: "var(--accent-muted)",
                  borderLeft: "3px solid var(--accent-primary)",
                  borderTop: "1px solid var(--border-subtle)",
                  borderRight: "1px solid var(--border-subtle)",
                  borderBottom: "1px solid var(--border-subtle)",
                  color: "var(--text-primary)",
                } : {
                  background: "var(--bg-tertiary)",
                  border: "1px solid var(--border-subtle)",
                  color: "var(--text-secondary)",
                }}
                className="relative px-3 py-2 rounded text-xs font-medium transition-all hover:brightness-110 disabled:opacity-60"
              >
                <span className="block" style={{ fontFamily: "var(--font-display)", fontWeight: config.quality === q.value ? 600 : 500 }}>{q.label}</span>
                <span className="block mt-0.5" style={{ fontSize: 10, color: "var(--text-muted)" }}>
                  {q.desc}
                </span>
              </button>
            ))}
          </div>
        </div>

        <div className="space-y-2 animate-fade-in" style={{ animationDelay: "0.15s" }}>
          <label
            className="block uppercase"
            style={{ fontFamily: "var(--font-display)", fontSize: 11, fontWeight: 500, letterSpacing: "0.1em", color: "var(--text-muted)" }}
          >
            Options
          </label>
          <div className="space-y-1">
            {[
              { icon: Cpu, label: "LLM Planner", checked: config.llm, key: "llm" as const },
              { icon: Eye, label: "Vision Tagger", checked: config.vision, key: "vision" as const },
              { icon: Trash2, label: "Clear Cache", checked: config.disableCache, key: "disableCache" as const },
            ].map(({ icon: Icon, label, checked, key }) => (
              <div
                key={label}
                className="flex items-center justify-between px-3 py-2.5 rounded-lg hover:bg-accent/50 transition-colors group"
              >
                <div className="flex items-center gap-2.5">
                  <Icon className="w-3.5 h-3.5 text-muted-foreground group-hover:text-foreground transition-colors" />
                  <span className="text-sm text-sidebar-foreground">{label}</span>
                </div>
                <Switch
                  checked={checked}
                  onCheckedChange={(value) => onConfigChange({ [key]: value } as Partial<PipelineConfig>)}
                  disabled={disabledInputs && key !== "disableCache"}
                />
              </div>
            ))}
          </div>
        </div>

        <div className="space-y-4 animate-fade-in" style={{ animationDelay: "0.2s" }}>
          <div className="space-y-2">
            <label className="flex items-center gap-2 text-sm text-sidebar-foreground font-medium">
              <Layers className="w-3.5 h-3.5 text-muted-foreground" />
              Buffer segments
            </label>
            <input
              type="number"
              min={0}
              max={20}
              value={bufferValue}
              disabled={disabledInputs}
              onChange={(e) => handleNumberChange("buffer", Number(e.target.value))}
              className="w-full px-3.5 py-2.5 rounded-lg surface-sunken border border-border/50 text-foreground text-sm font-mono focus:outline-none focus:ring-1 focus:ring-primary/50 focus:border-primary/30 transition-all disabled:opacity-60"
            />
          </div>
          <div className="space-y-2">
            <label className="flex items-center gap-2 text-sm text-sidebar-foreground font-medium">
              <Eye className="w-3.5 h-3.5 text-muted-foreground" />
              Max vision segments
            </label>
            <input
              type="number"
              min={1}
              max={50}
              value={visionMaxValue}
              disabled={!config.vision || disabledInputs}
              onChange={(e) => handleNumberChange("visionMax", Number(e.target.value))}
              className="w-full px-3.5 py-2.5 rounded-lg surface-sunken border border-border/50 text-foreground text-sm font-mono focus:outline-none focus:ring-1 focus:ring-primary/50 focus:border-primary/30 transition-all disabled:opacity-40"
            />
          </div>
        </div>

        {error && (
          <div className="flex items-center gap-2 text-xs text-destructive bg-destructive/10 border border-destructive/30 rounded-lg px-3 py-2 animate-fade-in">
            <XCircle className="w-4 h-4" />
            <span>{error}</span>
          </div>
        )}
      </div>

      <div className="p-4 relative z-10 space-y-2">
        <button
          onClick={handleRun}
          disabled={running}
          className="w-full flex items-center justify-center gap-2 py-3 rounded gradient-primary-btn disabled:opacity-60 transition-all"
          style={{
            fontFamily: "var(--font-display)",
            fontWeight: 600,
            fontSize: 14,
            color: "var(--bg-primary)",
            boxShadow: running ? "none" : "0 0 20px rgba(232,160,64,0.3)",
          }}
        >
          <Sparkles className="w-4 h-4" />
          {running ? "Running..." : "Run Pipeline"}
        </button>
        {running || phase === "rendering" ? (
          <button
            onClick={handleCancel}
            className="w-full text-sm text-muted-foreground hover:text-destructive transition-colors"
          >
            Cancel run
          </button>
        ) : null}
      </div>
    </aside>
  );
};

export default PipelineSidebar;
