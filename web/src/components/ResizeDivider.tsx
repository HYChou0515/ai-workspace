/**
 * Draggable divider for resizable panels. The parent snapshots whatever state
 * it cares about in `onResizeStart`, then each `onResize` reports the signed
 * pixel delta from the DRAG START position (not the previous event). That's
 * the standard pattern for pointer-driven drag: it tracks the cursor 1:1
 * regardless of event coalescing, and clamping at the parent doesn't
 * accumulate drift when the cursor overshoots and comes back.
 *
 * Layout: 12px hit area (off-layout via negative margins). An absolutely-
 * positioned 1–2 px line centers inside — invisible at rest, paper-3 on
 * hover, accent while dragging.
 */

import { useRef, useState } from "react";

import { Icon, type IconName } from "./Icon";

const HIT = 12;
const HALF = HIT / 2;

export function ResizeDivider({
  orientation,
  onResize,
  onResizeStart,
  onResizeEnd,
  ariaLabel,
  collapse,
}: {
  orientation: "vertical" | "horizontal"; // vertical = resizes width, horizontal = resizes height
  onResize: (deltaFromStart: number) => void;
  /** Snapshot the value(s) the parent will anchor to (fired on pointerdown). */
  onResizeStart?: () => void;
  /** Cleanup hook (fired on pointerup). */
  onResizeEnd?: () => void;
  ariaLabel?: string;
  /** Optional collapse chevron centered on the divider — clicking it folds the
   * adjacent panel away. The drag still works on the rest of the hit area
   * (the button stops its own pointer events from starting a resize). */
  collapse?: { label: string; icon: IconName; onToggle: () => void };
}) {
  // Where the drag started, in viewport coords along the active axis.
  const startCoord = useRef<number | null>(null);
  const [active, setActive] = useState(false);
  const [hover, setHover] = useState(false);
  const vertical = orientation === "vertical";

  const showLine = active || hover;
  const lineColor = active ? "var(--accent)" : showLine ? "var(--paper-3)" : "transparent";
  const lineThickness = active ? 2 : 1;

  return (
    <div
      role="separator"
      aria-label={ariaLabel}
      aria-orientation={orientation}
      onPointerDown={(e) => {
        e.currentTarget.setPointerCapture(e.pointerId);
        startCoord.current = vertical ? e.clientX : e.clientY;
        setActive(true);
        onResizeStart?.();
      }}
      onPointerMove={(e) => {
        if (startCoord.current == null) return;
        const cur = vertical ? e.clientX : e.clientY;
        onResize(cur - startCoord.current);
      }}
      onPointerUp={(e) => {
        e.currentTarget.releasePointerCapture(e.pointerId);
        startCoord.current = null;
        setActive(false);
        onResizeEnd?.();
      }}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        flexShrink: 0,
        cursor: vertical ? "col-resize" : "row-resize",
        background: "transparent",
        position: "relative", // anchor the inner line absolutely
        ...(vertical
          ? { width: HIT, marginInline: -HALF, alignSelf: "stretch" }
          : { height: HIT, marginBlock: -HALF, alignSelf: "stretch" }),
      }}
    >
      <div
        aria-hidden
        style={{
          position: "absolute",
          background: lineColor,
          pointerEvents: "none",
          transition: active ? "none" : "background 0.15s ease",
          ...(vertical
            ? {
                top: 0,
                bottom: 0,
                left: HALF - lineThickness / 2,
                width: lineThickness,
              }
            : {
                left: 0,
                right: 0,
                top: HALF - lineThickness / 2,
                height: lineThickness,
              }),
        }}
      />
      {collapse && vertical && (
        <button
          type="button"
          aria-label={collapse.label}
          title={collapse.label}
          // Don't let a click/drag on the chevron start a resize.
          onPointerDown={(e) => e.stopPropagation()}
          onClick={collapse.onToggle}
          style={{
            position: "absolute",
            top: 18,
            left: "50%",
            transform: "translateX(-50%)",
            width: 18,
            height: 30,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            padding: 0,
            cursor: "pointer",
            borderRadius: "var(--radius-chip, 6px)",
            border: "1px solid var(--paper-3)",
            background: "var(--paper-1, var(--paper))",
            color: "var(--text-paper-d)",
          }}
        >
          <Icon name={collapse.icon} size={14} />
        </button>
      )}
    </div>
  );
}
