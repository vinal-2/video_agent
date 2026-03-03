import { useState } from "react";
import { Sparkles, ChevronDown, Layers, Eye, Cpu, Trash2, SlidersHorizontal } from "lucide-react";
import { Switch } from "@/components/ui/switch";

const qualityOptions = [
  { label: "Proxy", desc: "Fast" },
  { label: "Normal", desc: "720p" },
  { label: "High", desc: "1080p" },
  { label: "4K", desc: "2160p" },
];

const templates = [
  { value: "travel-reel", label: "Travel Reel" },
  { value: "product-demo", label: "Product Demo" },
  { value: "social-clip", label: "Social Clip" },
  { value: "tutorial", label: "Tutorial" },
];

const PipelineSidebar = () => {
  const [template, setTemplate] = useState("travel-reel");
  const [templateOpen, setTemplateOpen] = useState(false);
  const [quality, setQuality] = useState("High");
  const [llmPlanner, setLlmPlanner] = useState(true);
  const [visionTagger, setVisionTagger] = useState(true);
  const [clearCache, setClearCache] = useState(false);
  const [bufferSegments, setBufferSegments] = useState("5");
  const [maxVisionSegments, setMaxVisionSegments] = useState("20");

  const selectedTemplate = templates.find((t) => t.value === template);

  return (
    <aside className="w-64 flex-shrink-0 flex flex-col h-full glass-surface border-r border-border/50 relative">
      {/* Decorative sidebar gradient */}
      <div className="absolute inset-0 bg-gradient-to-b from-primary/[0.02] to-transparent pointer-events-none" />

      <div className="flex-1 overflow-y-auto p-5 space-y-7 relative z-10">
        {/* Section Header */}
        <div className="flex items-center gap-2 text-muted-foreground">
          <SlidersHorizontal className="w-4 h-4" />
          <span className="text-xs font-semibold uppercase tracking-[0.15em]">Pipeline Config</span>
        </div>

        {/* Template - Custom Dropdown */}
        <div className="space-y-2.5 animate-fade-in" style={{ animationDelay: "0.05s" }}>
          <label className="text-[11px] font-semibold text-muted-foreground uppercase tracking-[0.15em]">Template</label>
          <div className="relative">
            <button
              onClick={() => setTemplateOpen(!templateOpen)}
              className="w-full flex items-center justify-between px-3.5 py-2.5 rounded-lg surface-elevated text-sm text-foreground font-medium transition-all hover:border-primary/30 border border-border/50"
            >
              {selectedTemplate?.label}
              <ChevronDown className={`w-4 h-4 text-muted-foreground transition-transform ${templateOpen ? "rotate-180" : ""}`} />
            </button>
            {templateOpen && (
              <div className="absolute top-full left-0 right-0 mt-1.5 rounded-lg surface-elevated border border-border/50 overflow-hidden z-50 animate-scale-in">
                {templates.map((t) => (
                  <button
                    key={t.value}
                    onClick={() => { setTemplate(t.value); setTemplateOpen(false); }}
                    className={`w-full text-left px-3.5 py-2.5 text-sm transition-colors ${
                      template === t.value
                        ? "text-primary bg-primary/10"
                        : "text-foreground hover:bg-accent"
                    }`}
                  >
                    {t.label}
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>

        {/* Output Quality */}
        <div className="space-y-2.5 animate-fade-in" style={{ animationDelay: "0.1s" }}>
          <label className="text-[11px] font-semibold text-muted-foreground uppercase tracking-[0.15em]">Output Quality</label>
          <div className="grid grid-cols-2 gap-1.5">
            {qualityOptions.map((q) => (
              <button
                key={q.label}
                onClick={() => setQuality(q.label)}
                className={`relative px-3 py-2.5 rounded-lg text-sm font-medium transition-all ${
                  quality === q.label
                    ? "gradient-primary-btn text-primary-foreground glow-primary"
                    : "surface-elevated text-secondary-foreground hover:text-foreground hover:border-primary/20 border border-transparent"
                }`}
              >
                <span className="block">{q.label}</span>
                <span className={`text-[10px] block mt-0.5 ${quality === q.label ? "text-primary-foreground/70" : "text-muted-foreground"}`}>
                  {q.desc}
                </span>
              </button>
            ))}
          </div>
        </div>

        {/* Options */}
        <div className="space-y-3.5 animate-fade-in" style={{ animationDelay: "0.15s" }}>
          <label className="text-[11px] font-semibold text-muted-foreground uppercase tracking-[0.15em]">Options</label>
          <div className="space-y-1">
            {[
              { icon: Cpu, label: "LLM Planner", checked: llmPlanner, onChange: setLlmPlanner },
              { icon: Eye, label: "Vision Tagger", checked: visionTagger, onChange: setVisionTagger },
              { icon: Trash2, label: "Clear Cache", checked: clearCache, onChange: setClearCache },
            ].map(({ icon: Icon, label, checked, onChange }) => (
              <div key={label} className="flex items-center justify-between px-3 py-2.5 rounded-lg hover:bg-accent/50 transition-colors group">
                <div className="flex items-center gap-2.5">
                  <Icon className="w-3.5 h-3.5 text-muted-foreground group-hover:text-foreground transition-colors" />
                  <span className="text-sm text-sidebar-foreground">{label}</span>
                </div>
                <Switch checked={checked} onCheckedChange={onChange} />
              </div>
            ))}
          </div>
        </div>

        {/* Number Inputs */}
        <div className="space-y-4 animate-fade-in" style={{ animationDelay: "0.2s" }}>
          {[
            { label: "Buffer segments", icon: Layers, value: bufferSegments, onChange: setBufferSegments },
            { label: "Max vision segments", icon: Eye, value: maxVisionSegments, onChange: setMaxVisionSegments },
          ].map(({ label, icon: Icon, value, onChange }) => (
            <div key={label} className="space-y-2">
              <label className="flex items-center gap-2 text-sm text-sidebar-foreground font-medium">
                <Icon className="w-3.5 h-3.5 text-muted-foreground" />
                {label}
              </label>
              <input
                type="number"
                value={value}
                onChange={(e) => onChange(e.target.value)}
                className="w-full px-3.5 py-2.5 rounded-lg surface-sunken border border-border/50 text-foreground text-sm font-mono focus:outline-none focus:ring-1 focus:ring-primary/50 focus:border-primary/30 transition-all"
              />
            </div>
          ))}
        </div>
      </div>

      {/* Run Pipeline Button */}
      <div className="p-4 relative z-10">
        <div className="absolute inset-x-4 -top-6 h-6 bg-gradient-to-t from-sidebar to-transparent pointer-events-none" />
        <button className="w-full flex items-center justify-center gap-2.5 px-4 py-3.5 rounded-xl gradient-primary-btn glow-primary-strong text-primary-foreground font-semibold text-sm transition-all active:scale-[0.97] hover:scale-[1.01]">
          <Sparkles className="w-4.5 h-4.5" />
          Run Pipeline
        </button>
      </div>
    </aside>
  );
};

export default PipelineSidebar;
