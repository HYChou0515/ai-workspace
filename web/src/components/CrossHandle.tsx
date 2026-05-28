/**
 * 2D drag handle at the intersection of two perpendicular dividers.
 * Drops onto its parent absolutely at (leftPct, topPct); `onResize` reports
 * the cursor delta from the drag-start position in BOTH axes (dx, dy).
 *
 * Same anchor-from-start semantics as ResizeDivider — coalesced pointer
 * events at high speed stay accurate, and parent clamping doesn't accumulate
 * drift on overshoot.
 *
 * Visible affordance: a small filled square at rest (so the user can see
 * "this point can be dragged in both directions"), accent-colored while
 * dragging. Hit area is the same size as the square.
 */

import { useRef, useState } from "react";

const SIZE = 14;
const HALF = SIZE / 2;

export function CrossHandle({
  leftPct,
  topPct,
  onResize,
  onResizeStart,
  onResizeEnd,
  ariaLabel = "resize panes",
}: {
  /** 0..1 — horizontal position as a fraction of the parent's width. */
  leftPct: number;
  /** 0..1 — vertical position as a fraction of the parent's height. */
  topPct: number;
  onResize: (deltaX: number, deltaY: number) => void;
  onResizeStart?: () => void;
  onResizeEnd?: () => void;
  ariaLabel?: string;
}) {
  const startX = useRef<number | null>(null);
  const startY = useRef<number | null>(null);
  const [active, setActive] = useState(false);
  const [hover, setHover] = useState(false);

  const showFill = active || hover;
  const bg = active ? "var(--accent)" : showFill ? "var(--paper-3)" : "transparent";

  return (
    <div
      role="button"
      aria-label={ariaLabel}
      onPointerDown={(e) => {
        e.currentTarget.setPointerCapture(e.pointerId);
        startX.current = e.clientX;
        startY.current = e.clientY;
        setActive(true);
        onResizeStart?.();
      }}
      onPointerMove={(e) => {
        if (startX.current == null || startY.current == null) return;
        onResize(e.clientX - startX.current, e.clientY - startY.current);
      }}
      onPointerUp={(e) => {
        e.currentTarget.releasePointerCapture(e.pointerId);
        startX.current = null;
        startY.current = null;
        setActive(false);
        onResizeEnd?.();
      }}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        position: "absolute",
        left: `${leftPct * 100}%`,
        top: `${topPct * 100}%`,
        width: SIZE,
        height: SIZE,
        marginLeft: -HALF,
        marginTop: -HALF,
        // Above the panel ResizeDividers (z=0/1) so the cross always wins
        // over single-axis handles at the intersection.
        zIndex: 5,
        cursor: "move",
        background: bg,
        border: active ? "1px solid var(--accent)" : "1px solid transparent",
        borderRadius: 3,
        transition: active ? "none" : "background 0.15s ease, border-color 0.15s ease",
      }}
    />
  );
}
