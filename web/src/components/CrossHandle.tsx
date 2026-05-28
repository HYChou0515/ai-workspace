/**
 * Cross-shaped resize handle for T-junctions / 4-way intersections between
 * split panes. Sits as an absolutely-positioned dot on the parent (which must
 * be position:relative). Reports BOTH X and Y deltas from the drag start so
 * the caller can update two ratios at once — e.g. the outer split's ratio AND
 * the inner perpendicular split's ratio in one drag.
 *
 * Anchor-on-drag-start (same pattern as ResizeDivider) so the dragged point
 * tracks the cursor 1:1 regardless of pointer-event coalescing.
 */

import { useRef, useState } from "react";

const SIZE = 12;
const HALF = SIZE / 2;

export function CrossHandle({
  left,
  top,
  ariaLabel,
  onResizeStart,
  onResize,
  onResizeEnd,
}: {
  /** CSS left/top of the handle's center; e.g. "50%" or "120px". */
  left: string;
  top: string;
  ariaLabel?: string;
  onResizeStart?: () => void;
  /** (dxFromStart, dyFromStart) in viewport pixels. */
  onResize: (dx: number, dy: number) => void;
  onResizeEnd?: () => void;
}) {
  const start = useRef<{ x: number; y: number } | null>(null);
  const [active, setActive] = useState(false);
  const [hover, setHover] = useState(false);

  const showFill = active || hover;
  const bg = active ? "var(--accent)" : showFill ? "var(--paper-3)" : "transparent";

  return (
    <div
      role="separator"
      aria-label={ariaLabel}
      onPointerDown={(e) => {
        e.currentTarget.setPointerCapture(e.pointerId);
        start.current = { x: e.clientX, y: e.clientY };
        setActive(true);
        onResizeStart?.();
      }}
      onPointerMove={(e) => {
        if (start.current == null) return;
        onResize(e.clientX - start.current.x, e.clientY - start.current.y);
      }}
      onPointerUp={(e) => {
        e.currentTarget.releasePointerCapture(e.pointerId);
        start.current = null;
        setActive(false);
        onResizeEnd?.();
      }}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        position: "absolute",
        left,
        top,
        width: SIZE,
        height: SIZE,
        marginLeft: -HALF,
        marginTop: -HALF,
        cursor: "move",
        background: bg,
        borderRadius: 2,
        transition: active ? "none" : "background 0.15s ease",
        zIndex: 10,
      }}
    />
  );
}
