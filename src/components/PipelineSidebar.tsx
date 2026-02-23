import { useState } from "react";
import { Sparkles } from "lucide-react";
import { Switch } from "@/components/ui/switch";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";

const qualityOptions = ["Proxy", "Normal", "High", "4K"];

const PipelineSidebar = () => {
  const [template, setTemplate] = useState("travel-reel");
  const [quality, setQuality] = useState("High");
  const [llmPlanner, setLlmPlanner] = useState(true);
  const [visionTagger, setVisionTagger] = useState(true);
  const [clearCache, setClearCache] = useState(false);
  const [bufferSegments, setBufferSegments] = useState("5");
  const [maxVisionSegments, setMaxVisionSegments] = useState("20");

  return (
    <aside className="w-60 flex-shrink-0 bg-sidebar border-r border-sidebar-border flex flex-col h-full">
      <div className="flex-1 overflow-y-auto p-4 space-y-6">
        {/* Template */}
        <div className="space-y-2">
          <label className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">Template</label>
          <Select value={template} onValueChange={setTemplate}>
            <SelectTrigger className="bg-accent border-border text-foreground">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="travel-reel">Travel Reel</SelectItem>
              <SelectItem value="product-demo">Product Demo</SelectItem>
              <SelectItem value="social-clip">Social Clip</SelectItem>
              <SelectItem value="tutorial">Tutorial</SelectItem>
            </SelectContent>
          </Select>
        </div>

        {/* Output Quality */}
        <div className="space-y-2">
          <label className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">Output Quality</label>
          <div className="grid grid-cols-2 gap-2">
            {qualityOptions.map((q) => (
              <button
                key={q}
                onClick={() => setQuality(q)}
                className={`px-3 py-2 rounded-md text-sm font-medium transition-colors ${
                  quality === q
                    ? "bg-primary text-primary-foreground"
                    : "bg-accent text-secondary-foreground hover:bg-border"
                }`}
              >
                {q}
              </button>
            ))}
          </div>
        </div>

        {/* Options */}
        <div className="space-y-3">
          <label className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">Options</label>
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <span className="text-sm text-sidebar-foreground">LLM Planner</span>
              <Switch checked={llmPlanner} onCheckedChange={setLlmPlanner} />
            </div>
            <div className="flex items-center justify-between">
              <span className="text-sm text-sidebar-foreground">Vision Tagger</span>
              <Switch checked={visionTagger} onCheckedChange={setVisionTagger} />
            </div>
            <div className="flex items-center justify-between">
              <span className="text-sm text-sidebar-foreground">Clear Cache</span>
              <Switch checked={clearCache} onCheckedChange={setClearCache} />
            </div>
          </div>
        </div>

        {/* Buffer segments */}
        <div className="space-y-2">
          <label className="text-sm text-sidebar-foreground font-medium">Buffer segments</label>
          <input
            type="number"
            value={bufferSegments}
            onChange={(e) => setBufferSegments(e.target.value)}
            className="w-full px-3 py-2 rounded-md bg-accent border border-border text-foreground text-sm focus:outline-none focus:ring-1 focus:ring-ring"
          />
        </div>

        {/* Max vision segments */}
        <div className="space-y-2">
          <label className="text-sm text-sidebar-foreground font-medium">Max vision segments</label>
          <input
            type="number"
            value={maxVisionSegments}
            onChange={(e) => setMaxVisionSegments(e.target.value)}
            className="w-full px-3 py-2 rounded-md bg-accent border border-border text-foreground text-sm focus:outline-none focus:ring-1 focus:ring-ring"
          />
        </div>
      </div>

      {/* Run Pipeline Button */}
      <div className="p-4 border-t border-sidebar-border">
        <button className="w-full flex items-center justify-center gap-2 px-4 py-3 rounded-lg bg-primary text-primary-foreground font-semibold text-sm hover:brightness-110 transition-all active:scale-[0.98]">
          <Sparkles className="w-4 h-4" />
          Run Pipeline
        </button>
      </div>
    </aside>
  );
};

export default PipelineSidebar;
