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
import type { Segment } from "@/lib/api";
import type { SegmentDecision } from "@/hooks/usePipeline";

// ── Types ─────────────────────────────────────────────────────────────────────

interface SegmentTimelineProps {
  segments: Segment[];
  segmentStates: Record<number, SegmentDecision | undefined>;
  selectedIndex: number | null;
  onSelect: (index: number) => void;
  onReorder: (newSegments: Segment[]) => void;
}

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
}

const SortableBlock = ({
  segment,
  index,
  originalIndex,
  decision,
  width,
  isSelected,
  onSelect,
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
}: BlockProps) => {
  const duration = (segment.end - segment.start);
  const name     = baseName(segment.video_path);
  const narrow   = width < 60;

  const borderColor = isSelected ? "var(--text-primary)" : blockBorder(decision, segment.buffer);

  return (
    <div
      onClick={() => onSelect(originalIndex)}
      title={`${name} · ${duration.toFixed(1)}s`}
      style={{
        width,
        minWidth: 40,
        height: 72,
        background: blockBg(decision, segment.buffer),
        borderLeft: `2px solid ${borderColor}`,
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

  const activeSegment = activeId !== null ? segments[activeId] : null;
  const activeWidth   = activeId !== null ? blockWidths[activeId] : MIN_BLOCK_WIDTH;

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
          Total: {totalDuration.toFixed(1)}s
        </span>
      </div>

      {/* Blocks */}
      <div
        ref={containerRef}
        className="overflow-x-auto"
        style={{ padding: "12px 16px 0" }}
      >
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
            <div className="flex gap-1 pb-2" style={{ width: "max-content" }}>
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
      </div>

      {/* Beat grid placeholder */}
      <div
        className="px-4 flex items-center gap-2 overflow-hidden"
        style={{ height: 20, borderTop: "1px solid var(--border-subtle)" }}
      >
        {/* Faint tick marks */}
        {Array.from({ length: Math.ceil(containerWidth / 60) }).map((_, i) => (
          <div
            key={i}
            style={{ width: 1, height: 8, background: "var(--border-subtle)", flexShrink: 0, marginRight: 59 }}
          />
        ))}
        <span
          className="absolute left-1/2 -translate-x-1/2 whitespace-nowrap"
          style={{ fontSize: 9, color: "var(--text-muted)", fontFamily: "var(--font-mono)", pointerEvents: "none" }}
        >
          Beat sync — drop a music track to align cuts
        </span>
      </div>
    </div>
  );
};

export default SegmentTimeline;
