import { useCallback, useRef, useState } from "react";
import {
  DndContext,
  DragEndEvent,
  DragOverlay,
  DragStartEvent,
  PointerSensor,
  useSensor,
  useSensors,
} from "@dnd-kit/core";
import {
  SortableContext,
  arrayMove,
  horizontalListSortingStrategy,
  useSortable,
} from "@dnd-kit/sortable";
import { restrictToHorizontalAxis } from "@dnd-kit/modifiers";
import { CSS } from "@dnd-kit/utilities";
import type { Segment, BeatMap } from "@/lib/api";
import type { SegmentDecision } from "@/hooks/usePipeline";

// ── Types ─────────────────────────────────────────────────────────────────────

interface SegmentTimelineProps {
  segments: Segment[];
  segmentStates: Record<number, SegmentDecision | undefined>;
  selectedIndex: number | null;
  onSelect: (index: number) => void;
  onReorder: (newSegments: Segment[]) => void;
  beatMap?: BeatMap | null;
}

// ── Section colours ────────────────────────────────────────────────────────────
const SECTION_BG: Record<string, string> = {
  chorus:       "rgba(239,68,68,0.08)",
  verse:        "rgba(34,197,94,0.08)",
  bridge:       "rgba(234,179,8,0.08)",
  intro:        "rgba(59,130,246,0.08)",
  outro:        "rgba(139,92,246,0.08)",
  "pre-chorus": "rgba(249,115,22,0.08)",
  break:        "rgba(107,114,128,0.06)",
  solo:         "rgba(236,72,153,0.08)",
};

// ── Helpers ───────────────────────────────────────────────────────────────────

function blockBg(decision: SegmentDecision | undefined, buffer: boolean | undefined): string {
  if (decision === "accepted") return "rgba(52,211,153,0.12)";
  if (decision === "rejected") return "rgba(239,68,68,0.08)";
  if (buffer)                  return "rgba(96,165,250,0.12)";
  return "var(--bg-tertiary)";
}

function blockBorder(decision: SegmentDecision | undefined, buffer: boolean | undefined): string {
  if (decision === "accepted") return "#34d399";
  if (decision === "rejected") return "#ef4444";
  if (buffer)                  return "#60a5fa";
  return "var(--status-pending)";
}

function baseName(videoPath: string | undefined): string {
  if (!videoPath) return "clip";
  return videoPath.split(/[/\\]/).pop() ?? "clip";
}

// ── Sortable block ────────────────────────────────────────────────────────────

interface BlockProps {
  segment: Segment;
  index: number;
  originalIndex: number;
  decision: SegmentDecision | undefined;
  width: number;
  isSelected: boolean;
  onSelect: (idx: number) => void;
  isDragOverlay?: boolean;
  beatSnapped?: boolean;
}

const SortableBlock = ({
  segment,
  index,
  originalIndex,
  decision,
  width,
  isSelected,
  onSelect,
  beatSnapped,
}: BlockProps) => {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } =
    useSortable({ id: originalIndex });

  const style: React.CSSProperties = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.3 : 1,
  };

  return (
    <div ref={setNodeRef} style={style} {...attributes} {...listeners}>
      <TimelineBlock
        segment={segment}
        index={index}
        originalIndex={originalIndex}
        decision={decision}
        width={width}
        isSelected={isSelected}
        onSelect={onSelect}
        beatSnapped={beatSnapped}
      />
    </div>
  );
};

const TimelineBlock = ({
  segment,
  index,
  originalIndex,
  decision,
  width,
  isSelected,
  onSelect,
  isDragOverlay = false,
  beatSnapped = false,
}: BlockProps) => {
  const duration    = segment.end - segment.start;
  const name        = baseName(segment.video_path);
  const narrow      = width < 60;
  const borderColor = isSelected ? "var(--text-primary)" : blockBorder(decision, segment.buffer);

  return (
    <div
      onClick={() => onSelect(originalIndex)}
      title={`${name} · ${duration.toFixed(1)}s`}
      style={{
        position: "relative",
        width,
        minWidth: 40,
        height: 72,
        background: blockBg(decision, segment.buffer),
        borderLeft:   `2px solid ${borderColor}`,
        borderTop:    `1px solid ${isSelected ? "var(--text-primary)" : "var(--border-subtle)"}`,
        borderRight:  `1px solid ${isSelected ? "var(--text-primary)" : "var(--border-subtle)"}`,
        borderBottom: `1px solid ${isSelected ? "var(--text-primary)" : "var(--border-subtle)"}`,
        borderRadius: 4,
        cursor: isDragOverlay ? "grabbing" : "pointer",
        flexShrink: 0,
        padding: "6px 6px 4px",
        display: "flex",
        flexDirection: "column",
        justifyContent: "space-between",
        transform: isDragOverlay ? "rotate(2deg) scale(1.04)" : undefined,
        boxShadow: isDragOverlay ? "0 8px 24px rgba(0,0,0,0.5)" : undefined,
        userSelect: "none",
        transition: "background 150ms, border-color 150ms",
      }}
    >
      {/* Beat-snapped indicator dot */}
      {beatSnapped && (
        <div
          style={{
            position: "absolute",
            top: 4,
            right: 4,
            width: 5,
            height: 5,
            borderRadius: "50%",
            background: "rgba(234,179,8,0.9)",
          }}
        />
      )}

      {/* Segment number */}
      <span style={{ fontSize: 10, fontFamily: "var(--font-mono)", color: "var(--text-muted)", lineHeight: 1 }}>
        #{index + 1}
      </span>

      {!narrow && (
        <>
          {/* Duration */}
          <span style={{ fontSize: 12, fontFamily: "var(--font-mono)", color: "var(--text-primary)", lineHeight: 1 }}>
            {duration.toFixed(1)}s
          </span>
          {/* Filename */}
          <span style={{ fontSize: 10, fontFamily: "var(--font-mono)", color: "var(--text-muted)", lineHeight: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {name}
          </span>
        </>
      )}
    </div>
  );
};

// ── SegmentTimeline ───────────────────────────────────────────────────────────

const MIN_BLOCK_WIDTH = 40;
const CONTAINER_PADDING = 32; // px total horizontal padding

const SegmentTimeline = ({
  segments,
  segmentStates,
  selectedIndex,
  onSelect,
  onReorder,
  beatMap,
}: SegmentTimelineProps) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const [activeId, setActiveId] = useState<number | null>(null);

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 4 } }),
  );

  // Compute proportional widths
  const totalDuration = segments.reduce((acc, s) => acc + (s.end - s.start), 0);
  const containerWidth = (containerRef.current?.clientWidth ?? 700) - CONTAINER_PADDING;

  const blockWidths = segments.map((s) => {
    if (totalDuration === 0) return MIN_BLOCK_WIDTH;
    return Math.max(MIN_BLOCK_WIDTH, Math.round(((s.end - s.start) / totalDuration) * containerWidth));
  });

  const handleDragStart = useCallback((e: DragStartEvent) => {
    setActiveId(e.active.id as number);
  }, []);

  const handleDragEnd = useCallback((e: DragEndEvent) => {
    setActiveId(null);
    const { active, over } = e;
    if (!over || active.id === over.id) return;
    const oldIndex = segments.findIndex((_, i) => i === (active.id as number));
    const newIndex = segments.findIndex((_, i) => i === (over.id as number));
    if (oldIndex === -1 || newIndex === -1) return;
    onReorder(arrayMove(segments, oldIndex, newIndex));
  }, [segments, onReorder]);

  const activeSegment  = activeId !== null ? segments[activeId] : null;
  const activeWidth    = activeId !== null ? blockWidths[activeId] : MIN_BLOCK_WIDTH;
  // Total pixel width of the blocks row (used for beat grid scaling)
  const totalBlocksPx  = blockWidths.reduce((a, b) => a + b, 0) + Math.max(0, segments.length - 1) * 4;
  const beatDuration   = beatMap?.duration ?? 0;

  // Map a time in seconds to a pixel x within totalBlocksPx
  const timeToX = (t: number) =>
    beatDuration > 0 ? Math.round((t / beatDuration) * totalBlocksPx) : 0;

  if (!segments.length) return null;

  return (
    <div
      style={{
        background: "var(--bg-secondary)",
        borderBottom: "1px solid var(--border-subtle)",
        flexShrink: 0,
      }}
    >
      {/* Header bar */}
      <div
        className="flex items-center justify-between px-4 py-2"
        style={{ borderBottom: "1px solid var(--border-subtle)" }}
      >
        <span style={{ fontSize: 11, fontFamily: "var(--font-display)", fontWeight: 500, color: "var(--text-muted)", letterSpacing: "0.08em", textTransform: "uppercase" }}>
          Timeline
        </span>
        <span style={{ fontSize: 11, fontFamily: "var(--font-mono)", color: "var(--text-secondary)" }}>
          {beatMap ? `${beatMap.bpm.toFixed(0)} BPM · ` : ""}Total: {totalDuration.toFixed(1)}s
        </span>
      </div>

      {/* Scrollable area — blocks + beat grid scroll together */}
      <div
        ref={containerRef}
        className="overflow-x-auto"
        style={{ padding: "12px 16px 0" }}
      >
        {/* Wrapper with relative positioning for section overlay */}
        <div style={{ position: "relative", width: "max-content" }}>

          {/* Section background overlay (behind blocks, pointer-events: none) */}
          {beatMap && beatMap.segments.length > 0 && (
            <div
              style={{
                position: "absolute",
                top: 0,
                left: 0,
                height: 72,
                width: totalBlocksPx,
                pointerEvents: "none",
              }}
            >
              {beatMap.segments.map((sec, i) => {
                const x = timeToX(sec.start);
                const w = timeToX(sec.end) - x;
                return (
                  <div
                    key={i}
                    style={{
                      position: "absolute",
                      left: x,
                      top: 0,
                      width: w,
                      height: "100%",
                      background: SECTION_BG[sec.label] ?? "rgba(107,114,128,0.06)",
                    }}
                  >
                    <span style={{
                      position: "absolute",
                      bottom: 3,
                      left: 4,
                      fontSize: 8,
                      fontFamily: "var(--font-mono)",
                      color: "var(--text-muted)",
                      textTransform: "uppercase",
                      letterSpacing: "0.06em",
                      pointerEvents: "none",
                    }}>
                      {sec.label}
                    </span>
                  </div>
                );
              })}
            </div>
          )}

          <DndContext
            sensors={sensors}
            modifiers={[restrictToHorizontalAxis]}
            onDragStart={handleDragStart}
            onDragEnd={handleDragEnd}
          >
            <SortableContext
              items={segments.map((_, i) => i)}
              strategy={horizontalListSortingStrategy}
            >
              <div className="flex gap-1" style={{ width: "max-content" }}>
                {segments.map((segment, i) => (
                  <SortableBlock
                    key={i}
                    segment={segment}
                    index={i}
                    originalIndex={i}
                    decision={segmentStates[i]}
                    width={blockWidths[i]}
                    isSelected={selectedIndex === i}
                    onSelect={onSelect}
                    beatSnapped={segment.beat_snapped as boolean | undefined}
                  />
                ))}
              </div>
            </SortableContext>

            <DragOverlay>
              {activeSegment !== null && (
                <TimelineBlock
                  segment={activeSegment}
                  index={activeId ?? 0}
                  originalIndex={activeId ?? 0}
                  decision={segmentStates[activeId ?? 0]}
                  width={activeWidth}
                  isSelected={false}
                  onSelect={() => {}}
                  isDragOverlay
                />
              )}
            </DragOverlay>
          </DndContext>

          {/* Beat grid strip — scrolls with blocks */}
          <div
            style={{
              position: "relative",
              height: 20,
              width: totalBlocksPx,
              borderTop: "1px solid var(--border-subtle)",
              marginTop: 2,
              overflow: "hidden",
            }}
          >
            {beatMap ? (
              <>
                {/* Regular beat ticks */}
                {beatMap.beats.map((b, i) => {
                  const x = timeToX(b);
                  return (
                    <div
                      key={i}
                      style={{
                        position: "absolute",
                        left: x,
                        bottom: 0,
                        width: 1,
                        height: 8,
                        background: "rgba(255,255,255,0.15)",
                      }}
                    />
                  );
                })}
                {/* Downbeat ticks (amber, taller) */}
                {beatMap.downbeats.map((d, i) => {
                  const x = timeToX(d);
                  return (
                    <div
                      key={i}
                      style={{
                        position: "absolute",
                        left: x,
                        bottom: 0,
                        width: 2,
                        height: 14,
                        background: "rgba(234,179,8,0.6)",
                      }}
                    />
                  );
                })}
                {/* BPM label */}
                <span style={{
                  position: "absolute",
                  right: 4,
                  top: "50%",
                  transform: "translateY(-50%)",
                  fontSize: 9,
                  fontFamily: "var(--font-mono)",
                  color: "var(--text-muted)",
                  whiteSpace: "nowrap",
                  pointerEvents: "none",
                }}>
                  {beatMap.bpm.toFixed(0)} BPM
                </span>
              </>
            ) : (
              <span style={{
                position: "absolute",
                left: "50%",
                top: "50%",
                transform: "translate(-50%, -50%)",
                fontSize: 9,
                fontFamily: "var(--font-mono)",
                color: "var(--text-muted)",
                whiteSpace: "nowrap",
                pointerEvents: "none",
              }}>
                Drop a music track to enable beat sync
              </span>
            )}
          </div>

        </div>
      </div>
    </div>
  );
};

export default SegmentTimeline;
