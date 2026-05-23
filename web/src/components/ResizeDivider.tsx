/**
 * Draggable divider for resizable panels. Reports a signed pixel delta as
 * the user drags; the parent applies it to a panel dimension (and usually
 * persists it). Pointer-capture means the drag keeps tracking even if the
 * cursor leaves the thin hit area.
 */

import { useRef, useState } from "react";

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
  const vertical = orientation === "vertical";

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
      style={{
        flexShrink: 0,
        cursor: vertical ? "col-resize" : "row-resize",
        background: active ? "var(--accent)" : "transparent",
        transition: active ? "none" : "background 0.15s ease",
        ...(vertical
          ? { width: 5, marginInline: -2, alignSelf: "stretch" }
          : { height: 5, marginBlock: -2 }),
      }}
      onMouseEnter={(e) => {
        if (!active) (e.currentTarget as HTMLElement).style.background = "var(--paper-3)";
      }}
      onMouseLeave={(e) => {
        if (!active) (e.currentTarget as HTMLElement).style.background = "transparent";
      }}
    />
  );
}
