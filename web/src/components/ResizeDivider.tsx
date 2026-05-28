/**
 * Draggable divider for resizable panels. Reports a signed pixel delta as
 * the user drags; the parent applies it to a panel dimension (and usually
 * persists it). Pointer-capture means the drag keeps tracking even if the
 * cursor leaves the thin hit area.
 *
 * Layout: the outer hit area is 12px (wide enough to grab comfortably) but
 * is pulled back with negative margins so it doesn't reserve layout space.
 * The visible 1–2px line lives inside the hit area, centered — invisible at
 * rest, paper-3 on hover, accent while dragging.
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

  return (
    <div
      role="separator"
      aria-label={ariaLabel}
      aria-orientation={orientation}
      onPointerDown={(e) => {
        (e.target as HTMLElement).setPointerCapture(e.pointerId);
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
        (e.target as HTMLElement).releasePointerCapture(e.pointerId);
        last.current = null;
        setActive(false);
      }}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        flexShrink: 0,
        cursor: vertical ? "col-resize" : "row-resize",
        display: "flex",
        alignItems: vertical ? "stretch" : "center",
        justifyContent: vertical ? "center" : "stretch",
        background: "transparent",
        ...(vertical
          ? { width: HIT, marginInline: -HALF, alignSelf: "stretch" }
          : { height: HIT, marginBlock: -HALF }),
      }}
    >
      {/* Visible 1–2 px line, centered inside the wider hit area. */}
      <div
        aria-hidden
        style={{
          background: lineColor,
          transition: active ? "none" : "background 0.15s ease",
          ...(vertical
            ? { width: active ? 2 : 1, alignSelf: "stretch" }
            : { height: active ? 2 : 1, alignSelf: "stretch" }),
        }}
      />
    </div>
  );
}
