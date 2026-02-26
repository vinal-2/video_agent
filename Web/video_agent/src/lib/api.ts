export type PipelinePhase = "idle" | "analysing" | "reviewing" | "rendering" | "done" | "error" | string;

export interface GradeSettings {
  brightness: number;  // -50 to +50
  contrast: number;    // -50 to +50
  saturation: number;  // -50 to +50
  temp: number;        // -50 to +50 (warm/cool)
  lut: string;         // "none" | "cinema" | "golden" | "cool" | "fade" | "punch" | "mono" | "teal_org"
}

export const DEFAULT_GRADE: GradeSettings = {
  brightness: 0,
  contrast: 0,
  saturation: 0,
  temp: 0,
  lut: "none",
};

export interface TrimState {
  start: number;
  end: number;
}

export interface CropSettings {
  x: number;        // left edge in source pixels
  y: number;        // always 0 — full-height crop
  w: number;        // source_height * 9/16, rounded to even
  h: number;        // source height
  source_w: number; // original frame width
  source_h: number; // original frame height
  auto: boolean;    // true = computed by smart_crop, false = user-dragged
}

export interface Segment {
  start: number;
  end: number;
  score?: number;
  buffer?: boolean;
  video_path?: string;
  tags?: string[];
  combined_tags?: string[];
  style_sim?: number;
  style_score?: number;
  [key: string]: unknown;
}

export interface SegmentCounts {
  selected: number;
  accepted: number;
  buffer: number;
  pending: number;
}

export interface PipelineStatus {
  running: boolean;
  phase: PipelinePhase;
  log_line_count: number;
  selected_segments?: Segment[];
  reviewed_segments?: Segment[];
  last_output?: string | null;
  last_error?: string | null;
  error_detail?: string | null;
  clip_count?: number;
  segment_counts?: SegmentCounts;
  warnings?: string[];
  ffmpeg_progress?: number | null;
  [key: string]: unknown;
}

export interface OutputInfo {
  path: string | null;
  name?: string;
  size_mb?: number;
}

export interface ClipInfo {
  name: string;
  size_mb: number;
}

export interface PipelineConfig {
  template: string;
  quality: "proxy" | "normal" | "high" | "4k";
  buffer: number;
  llm: boolean;
  vision: boolean;
  visionModel: string;
  visionMax: number;
  disableCache: boolean;
}

export const defaultConfig: PipelineConfig = {
  template: "travel_reel",
  quality: "proxy",
  buffer: 5,
  llm: false,
  vision: false,
  visionModel: "moondream",
  visionMax: 15,
  disableCache: false,
};

async function ensureOk(res: Response) {
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || res.statusText);
  }
  return res;
}

export async function fetchStatus(): Promise<PipelineStatus> {
  const res = await ensureOk(await fetch("/api/status"));
  return res.json();
}

export async function fetchTemplates(): Promise<string[]> {
  const res = await ensureOk(await fetch("/api/templates"));
  return res.json();
}

export async function fetchClips(): Promise<ClipInfo[]> {
  const res = await ensureOk(await fetch("/api/clips"));
  return res.json();
}

export async function fetchLatestOutput(): Promise<OutputInfo> {
  const res = await ensureOk(await fetch("/api/output/latest"));
  return res.json();
}

function buildParams(config: PipelineConfig) {
  return {
    template: config.template,
    quality: config.quality,
    buffer: config.buffer,
    llm: config.llm ? "1" : "0",
    vision: config.vision ? config.visionModel : "none",
    vision_max: config.vision ? config.visionMax : undefined,
    disable_cache: config.disableCache ? "1" : "0",
  };
}

export async function runPipelineRequest(config: PipelineConfig) {
  const res = await ensureOk(
    await fetch("/api/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(buildParams(config)),
    }),
  );
  return res.json().catch(() => ({}));
}

export async function renderSegmentsRequest(segments: Segment[], config: PipelineConfig) {
  const payload = {
    segments,
    params: buildParams(config),
  };
  const res = await ensureOk(
    await fetch("/api/review", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  );
  return res.json().catch(() => ({}));
}

export async function fetchCropAuto(
  video_path: string,
  start: number,
  end: number,
): Promise<CropSettings> {
  const res = await ensureOk(
    await fetch("/api/crop_auto", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ video_path, start, end }),
    }),
  );
  const data = await res.json();
  return { ...data, auto: true };
}

export interface SamMaskSettings {
  mask_b64: string;   // base64-encoded PNG — white=subject, black=background
  width: number;      // source frame width
  height: number;     // source frame height
  point_x: number;    // fractional X used to generate mask (0.0–1.0)
  point_y: number;    // fractional Y
  enabled: boolean;   // when false, mask exists but split-grade is off
}

export async function fetchSamMask(
  video_path: string,
  timestamp: number,
  point_x: number,
  point_y: number,
): Promise<SamMaskSettings> {
  const res = await ensureOk(
    await fetch("/api/sam_mask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ video_path, timestamp, point_x, point_y }),
    }),
  );
  const data = await res.json();
  return { ...data, point_x, point_y, enabled: true };
}

export interface InpaintJobStatus {
  status: "pending" | "running" | "done" | "failed";
  progress: number;              // 0.0–1.0
  frames_done: number;
  frames_total: number;
  estimated_seconds: number | null;
  output_path: string | null;
  error: string | null;
}

export interface InpaintJob {
  jobId: string;
  segmentIndex: number;
  videoPath: string;
  status: InpaintJobStatus;
}

export async function startInpaintJob(
  segment_index: number,
  video_path: string,
  start: number,
  end: number,
  mask_b64: string,
): Promise<{ job_id: string }> {
  const res = await ensureOk(
    await fetch("/api/inpaint/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ segment_index, video_path, start, end, mask_b64 }),
    }),
  );
  return res.json();
}

export async function getInpaintStatus(job_id: string): Promise<InpaintJobStatus> {
  const res = await ensureOk(await fetch(`/api/inpaint/status/${job_id}`));
  return res.json();
}

export async function cancelInpaintJob(job_id: string): Promise<void> {
  await ensureOk(
    await fetch(`/api/inpaint/cancel/${job_id}`, { method: "POST" }),
  );
}

export async function cancelPipelineRequest() {
  const res = await ensureOk(
    await fetch("/api/cancel", {
      method: "POST",
    }),
  );
  return res.json().catch(() => ({}));
}
