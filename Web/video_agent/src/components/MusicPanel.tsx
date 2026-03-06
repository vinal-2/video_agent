import { useState, useEffect, useRef } from "react";
import { Music, Check, X, Loader2 } from "lucide-react";
import { Switch } from "@/components/ui/switch";
import type { BeatMap } from "@/lib/api";

// ── Section pill colours ───────────────────────────────────────────────────────
const SECTION_COLORS: Record<string, string> = {
  chorus:        "rgba(239,68,68,0.85)",
  verse:         "rgba(34,197,94,0.85)",
  intro:         "rgba(59,130,246,0.85)",
  outro:         "rgba(139,92,246,0.85)",
  bridge:        "rgba(234,179,8,0.85)",
  "pre-chorus":  "rgba(249,115,22,0.85)",
  break:         "rgba(107,114,128,0.85)",
  solo:          "rgba(236,72,153,0.85)",
};

const ACCEPTED_TYPES = ".mp3,.aac,.wav,.m4a,.flac,.ogg";

type UploadPhase = "idle" | "uploading" | "ready" | "analyzing" | "analyzed";

interface MusicFile { filename: string; size_mb: number; }

interface MusicPanelProps {
  onBeatMapChange: (bm: BeatMap | null) => void;
}

const SectionLabel = ({
  style,
}: {
  style: React.CSSProperties;
}) => (
  <label
    className="block uppercase"
    style={{
      fontFamily: "var(--font-display)",
      fontSize: 11,
      fontWeight: 500,
      letterSpacing: "0.1em",
      color: "var(--text-muted)",
      ...style,
    }}
  >
    Music Track
  </label>
);

const MusicPanel = ({ onBeatMapChange }: MusicPanelProps) => {
  const [phase,     setPhase]     = useState<UploadPhase>("idle");
  const [fileName,  setFileName]  = useState<string | null>(null);
  const [beatMap,   setBeatMap]   = useState<BeatMap | null>(null);
  const [beatSyncOn,setBeatSyncOn]= useState(true);
  const [prevFiles, setPrevFiles] = useState<MusicFile[]>([]);
  const [error,     setError]     = useState<string | null>(null);
  const [dragOver,  setDragOver]  = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  // Load previously uploaded tracks
  useEffect(() => {
    fetch("/api/music/list")
      .then((r) => r.json())
      .then(setPrevFiles)
      .catch(() => {});
  }, []);

  const setJobMusic = (name: string | null) =>
    fetch("/api/job/set-music", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ music_filename: name }),
    }).catch(() => {});

  const refreshList = () =>
    fetch("/api/music/list")
      .then((r) => r.json())
      .then(setPrevFiles)
      .catch(() => {});

  const uploadFile = async (file: File) => {
    setPhase("uploading");
    setError(null);
    const fd = new FormData();
    fd.append("music", file);
    try {
      const res  = await fetch("/api/music/upload", { method: "POST", body: fd });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error ?? "Upload failed");
      setFileName(data.filename);
      setPhase("ready");
      await setJobMusic(data.filename);
      refreshList();
    } catch (e) {
      setError((e as Error).message);
      setPhase("idle");
    }
  };

  const analyze = async () => {
    if (!fileName) return;
    setPhase("analyzing");
    setError(null);
    try {
      const res  = await fetch("/api/music/analyze", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ filename: fileName }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error ?? "Analysis failed");
      setBeatMap(data as BeatMap);
      onBeatMapChange(data as BeatMap);
      setPhase("analyzed");
    } catch (e) {
      setError((e as Error).message);
      setPhase("ready");
    }
  };

  const selectPrev = async (name: string) => {
    setFileName(name);
    setBeatMap(null);
    onBeatMapChange(null);
    setPhase("ready");
    await setJobMusic(name);
  };

  const clear = () => {
    setFileName(null);
    setBeatMap(null);
    setBeatSyncOn(false);
    onBeatMapChange(null);
    setPhase("idle");
    setError(null);
    setJobMusic(null);
  };

  const handleToggleBeatSync = (on: boolean) => {
    setBeatSyncOn(on);
    setJobMusic(on && fileName ? fileName : null);
    if (!on) onBeatMapChange(null);
    else if (beatMap) onBeatMapChange(beatMap);
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(false);
    const file = e.dataTransfer.files[0];
    if (file) uploadFile(file);
  };

  const truncate = (s: string, n: number) =>
    s.length > n ? s.slice(0, n - 1) + "…" : s;

  // ── STATE 1: idle ────────────────────────────────────────────────────────────
  if (phase === "idle") {
    return (
      <div className="space-y-2">
        <SectionLabel style={{}} />

        {/* Drop zone */}
        <div
          onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
          onDragLeave={() => setDragOver(false)}
          onDrop={handleDrop}
          onClick={() => inputRef.current?.click()}
          className="rounded-md cursor-pointer flex flex-col items-center justify-center gap-1 transition-all"
          style={{
            height: 72,
            border: `1.5px dashed ${dragOver ? "var(--accent-secondary)" : "var(--border-subtle)"}`,
            background: dragOver ? "var(--accent-muted)" : "transparent",
          }}
        >
          <Music className="w-4 h-4" style={{ color: "var(--text-muted)" }} />
          <span style={{ fontSize: 11, color: "var(--text-muted)", fontFamily: "var(--font-display)" }}>
            Drop MP3 / AAC
          </span>
          <span
            style={{ fontSize: 10, color: "var(--accent-secondary)", fontFamily: "var(--font-display)", cursor: "pointer" }}
          >
            or browse
          </span>
        </div>
        <input
          ref={inputRef}
          type="file"
          accept={ACCEPTED_TYPES}
          className="hidden"
          onChange={(e) => { const f = e.target.files?.[0]; if (f) uploadFile(f); }}
        />

        {error && (
          <p style={{ fontSize: 10, color: "var(--destructive)", fontFamily: "var(--font-display)" }}>{error}</p>
        )}

        {/* Previously uploaded tracks */}
        {prevFiles.length > 0 && (
          <div className="space-y-1 mt-1">
            {prevFiles.slice(0, 3).map((f) => (
              <div key={f.filename} className="flex items-center justify-between gap-1">
                <span style={{ fontSize: 10, color: "var(--text-muted)", fontFamily: "var(--font-display)" }}>
                  {truncate(f.filename, 18)}
                </span>
                <button
                  onClick={() => selectPrev(f.filename)}
                  style={{
                    fontSize: 10,
                    fontFamily: "var(--font-display)",
                    color: "var(--accent-secondary)",
                    background: "none",
                    border: "none",
                    cursor: "pointer",
                    padding: "2px 6px",
                  }}
                >
                  Use
                </button>
              </div>
            ))}
          </div>
        )}
      </div>
    );
  }

  // ── STATE 2: uploading ───────────────────────────────────────────────────────
  if (phase === "uploading") {
    return (
      <div className="space-y-2">
        <SectionLabel style={{}} />
        <div className="flex items-center gap-2 px-3 py-2 rounded-md" style={{ background: "var(--bg-tertiary)" }}>
          <Loader2 className="w-3.5 h-3.5 animate-spin" style={{ color: "var(--accent-secondary)" }} />
          <span style={{ fontSize: 11, color: "var(--text-muted)", fontFamily: "var(--font-display)" }}>
            Uploading…
          </span>
        </div>
      </div>
    );
  }

  // ── STATE 3: ready (uploaded, not yet analyzed) ──────────────────────────────
  if (phase === "ready") {
    return (
      <div className="space-y-2">
        <SectionLabel style={{}} />
        <div className="flex items-center gap-2 px-3 py-2 rounded-md" style={{ background: "var(--bg-tertiary)" }}>
          <Check className="w-3.5 h-3.5 flex-shrink-0" style={{ color: "#22c55e" }} />
          <span
            className="flex-1 truncate"
            style={{ fontSize: 11, color: "var(--text-primary)", fontFamily: "var(--font-display)" }}
            title={fileName ?? ""}
          >
            {truncate(fileName ?? "", 20)}
          </span>
          <button onClick={clear} style={{ background: "none", border: "none", cursor: "pointer", padding: 2 }}>
            <X className="w-3 h-3" style={{ color: "var(--text-muted)" }} />
          </button>
        </div>

        {error && (
          <p style={{ fontSize: 10, color: "var(--destructive)", fontFamily: "var(--font-display)" }}>{error}</p>
        )}

        <button
          onClick={analyze}
          className="w-full py-1.5 rounded text-xs font-medium transition-all hover:brightness-110"
          style={{
            fontFamily: "var(--font-display)",
            background: "rgba(234,179,8,0.15)",
            border: "1px solid rgba(234,179,8,0.4)",
            color: "rgba(234,179,8,0.9)",
          }}
        >
          Analyze beats
        </button>
      </div>
    );
  }

  // ── STATE 3b: analyzing ──────────────────────────────────────────────────────
  if (phase === "analyzing") {
    return (
      <div className="space-y-2">
        <SectionLabel style={{}} />
        <div className="flex items-center gap-2 px-3 py-2 rounded-md" style={{ background: "var(--bg-tertiary)" }}>
          <Loader2 className="w-3.5 h-3.5 animate-spin flex-shrink-0" style={{ color: "rgba(234,179,8,0.9)" }} />
          <span style={{ fontSize: 11, color: "var(--text-muted)", fontFamily: "var(--font-display)" }}>
            Detecting beats…
          </span>
        </div>
      </div>
    );
  }

  // ── STATE 4: analyzed ────────────────────────────────────────────────────────
  return (
    <div className="space-y-2">
      <SectionLabel style={{}} />

      {/* File row */}
      <div className="flex items-center gap-2">
        {/* BPM badge */}
        <span
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 11,
            fontWeight: 600,
            background: "rgba(234,179,8,0.15)",
            border: "1px solid rgba(234,179,8,0.35)",
            color: "rgba(234,179,8,0.95)",
            borderRadius: 4,
            padding: "2px 6px",
            whiteSpace: "nowrap",
          }}
        >
          {beatMap?.bpm?.toFixed(0)} BPM
        </span>
        <span
          className="flex-1 truncate"
          style={{ fontSize: 10, color: "var(--text-muted)", fontFamily: "var(--font-display)" }}
          title={fileName ?? ""}
        >
          {truncate(fileName ?? "", 14)}
        </span>
        <button onClick={clear} style={{ background: "none", border: "none", cursor: "pointer", padding: 2 }}>
          <X className="w-3 h-3" style={{ color: "var(--text-muted)" }} />
        </button>
      </div>

      {/* Sections detected */}
      {beatMap && beatMap.segments.length > 0 && (
        <div>
          <p style={{ fontSize: 10, color: "var(--text-muted)", fontFamily: "var(--font-display)", marginBottom: 4 }}>
            {beatMap.segments.length} sections detected
          </p>
          <div className="flex flex-wrap gap-1">
            {beatMap.segments.map((s, i) => (
              <span
                key={i}
                style={{
                  fontSize: 9,
                  fontFamily: "var(--font-display)",
                  fontWeight: 600,
                  letterSpacing: "0.06em",
                  padding: "1px 5px",
                  borderRadius: 3,
                  background: SECTION_COLORS[s.label] ?? "rgba(107,114,128,0.7)",
                  color: "#fff",
                  textTransform: "uppercase",
                }}
              >
                {s.label}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Beat sync toggle */}
      <div
        className="flex items-center justify-between px-3 py-2 rounded-lg"
        style={{ background: "var(--bg-tertiary)" }}
      >
        <span style={{ fontSize: 11, fontFamily: "var(--font-display)", color: beatSyncOn ? "var(--accent-secondary)" : "var(--text-muted)", fontWeight: 600 }}>
          {beatSyncOn ? "Beat sync ON" : "Beat sync OFF"}
        </span>
        <Switch checked={beatSyncOn} onCheckedChange={handleToggleBeatSync} />
      </div>
    </div>
  );
};

export default MusicPanel;
