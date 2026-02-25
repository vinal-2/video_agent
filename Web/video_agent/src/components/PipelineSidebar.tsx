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
    <aside className="w-64 flex-shrink-0 flex flex-col h-full glass-surface border-r border-border/50 relative">
      <div className="absolute inset-0 bg-gradient-to-b from-primary/[0.02] to-transparent pointer-events-none" />

      <div className="flex-1 overflow-y-auto p-5 space-y-7 relative z-10">
        <div className="flex items-center gap-2 text-muted-foreground">
          <SlidersHorizontal className="w-4 h-4" />
          <span className="text-xs font-semibold uppercase tracking-[0.15em]">Pipeline Config</span>
        </div>

        <div className="space-y-2.5 animate-fade-in" style={{ animationDelay: "0.05s" }}>
          <label className="text-[11px] font-semibold text-muted-foreground uppercase tracking-[0.15em]">
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

        <div className="space-y-2.5 animate-fade-in" style={{ animationDelay: "0.1s" }}>
          <label className="text-[11px] font-semibold text-muted-foreground uppercase tracking-[0.15em]">
            Output Quality
          </label>
          <div className="grid grid-cols-2 gap-1.5">
            {qualityOptions.map((q) => (
              <button
                key={q.value}
                onClick={() => onConfigChange({ quality: q.value })}
                disabled={disabledInputs}
                className={`relative px-3 py-2.5 rounded-lg text-sm font-medium transition-all ${
                  config.quality === q.value
                    ? "gradient-primary-btn text-primary-foreground glow-primary"
                    : "surface-elevated text-secondary-foreground hover:text-foreground hover:border-primary/20 border border-transparent"
                } disabled:opacity-60`}
              >
                <span className="block">{q.label}</span>
                <span
                  className={`text-[10px] block mt-0.5 ${
                    config.quality === q.value ? "text-primary-foreground/70" : "text-muted-foreground"
                  }`}
                >
                  {q.desc}
                </span>
              </button>
            ))}
          </div>
        </div>

        <div className="space-y-3.5 animate-fade-in" style={{ animationDelay: "0.15s" }}>
          <label className="text-[11px] font-semibold text-muted-foreground uppercase tracking-[0.15em]">
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
        <div className="absolute inset-x-4 -top-6 h-6 bg-gradient-to-t from-sidebar to-transparent pointer-events-none" />
        <button
          onClick={handleRun}
          disabled={running}
          className="w-full flex items-center justify-center gap-2.5 px-4 py-3.5 rounded-xl gradient-primary-btn glow-primary-strong text-primary-foreground font-semibold text-sm transition-all active:scale-[0.97] hover:scale-[1.01] disabled:opacity-60"
        >
          <Sparkles className="w-4.5 h-4.5" />
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
