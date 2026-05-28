/**
 * Draggable divider for resizable panels. Reports a signed pixel delta as
 * the user drags; the parent applies it to a panel dimension (and usually
 * persists it). Pointer-capture means the drag keeps tracking even if the
 * cursor leaves the thin hit area.
 *
 * Layout: the outer hit area is 12px (wide enough to grab comfortably) but
 * is pulled back with negative margins so it doesn't reserve layout space.
 * An absolutely-positioned 1–2 px line centers inside the hit area —
 * invisible at rest, paper-3 on hover, accent while dragging.
 */

import { useRef, useState } from "react";

const HIT = 12;
const HALF = HIT / 2;

export function ResizeDivider({
  orientation,
  onResize,
  ariaLabel,
}: {
  orientation: "vertical" | "horizontal"; // vertical = resizes width, horizontal = resizes height
  onResize: (deltaPx: number) => void;
  ariaLabel?: string;
}) {
  const last = useRef<number | null>(null);
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
        // Capture on the outer (currentTarget) — `e.target` may be the
        // visible inner line (1–2 px) which is brittle to capture on.
        e.currentTarget.setPointerCapture(e.pointerId);
        last.current = vertical ? e.clientX : e.clientY;
        setActive(true);
      }}
      onPointerMove={(e) => {
        if (last.current == null) return;
        const cur = vertical ? e.clientX : e.clientY;
        const delta = cur - last.current;
        last.current = cur;
        if (delta !== 0) onResize(delta);
      }}
      onPointerUp={(e) => {
        e.currentTarget.releasePointerCapture(e.pointerId);
        last.current = null;
        setActive(false);
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
      {/* Absolutely positioned so it stretches along the divider's main axis
          (the full height for vertical / full width for horizontal) without
          flex layout games. pointerEvents:none so the outer always wins. */}
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
    </div>
  );
}
