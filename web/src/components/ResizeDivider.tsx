/**
 * Draggable divider for resizable panels. The parent snapshots whatever state
 * it cares about in `onResizeStart`, then each `onResize` reports the signed
 * pixel delta from the DRAG START position (not the previous event). That's
 * the standard pattern for pointer-driven drag: it tracks the cursor 1:1
 * regardless of event coalescing, and clamping at the parent doesn't
 * accumulate drift when the cursor overshoots and comes back.
 *
 * Affordance (the reason this looks the way it does):
 *
 *  - A **grip is drawn at rest**, not only on hover. A handle that appears
 *    only under the pointer asks people to find it by accident, and for
 *    controls that are not obviously draggable the guidance is a persistent
 *    handle with hover as confirmation, not as the first hint.
 *    https://smart-interface-design-patterns.com/articles/drag-and-drop-ux/
 *    https://www.pencilandpaper.io/articles/ux-pattern-drag-and-drop
 *  - The **hit area is 24px** along the resize axis — the AA floor for pointer
 *    targets (WCAG 2.2 SC 2.5.8, Target Size (Minimum)). It stays off-layout
 *    via negative margins, so the extra reach costs no space.
 *    https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum.html
 *  - It is **operable from the keyboard** — focusable, with arrow keys moving
 *    it by `step` — but only for a parent that passes `value`/`min`/`max`.
 *    The window splitter pattern REQUIRES `aria-valuenow`/`valuemin`/`valuemax`
 *    on a focusable separator, and a tab stop that announces a separator with
 *    no position is worse for a screen-reader user than no tab stop at all. So
 *    the three props are what switch keyboard operation on, rather than every
 *    divider becoming a half-compliant splitter.
 *    https://www.w3.org/WAI/ARIA/apg/patterns/windowsplitter/
 *    (`aria-controls`, which the pattern also asks for, is NOT wired: the panes
 *    these dividers size have no stable ids to point at.)
 *
 * Layout: 24px hit area (off-layout via negative margins), with an absolutely-
 * positioned line and grip centred inside. The line is invisible at rest,
 * paper-3 on hover, accent while dragging or focused; the grip — the resting
 * affordance — is paper-3 at rest, text-paper-d on hover, accent with the line.
 */

import { useRef, useState } from "react";

const HIT = 24;
const HALF = HIT / 2;
/** How far one arrow-key press moves the divider (px). */
const KEY_STEP = 8;
/** The grip's long side — enough to read as a handle, short enough to stay quiet. */
const GRIP = 28;

export function ResizeDivider({
  orientation,
  onResize,
  onResizeStart,
  onResizeEnd,
  ariaLabel,
  value,
  min,
  max,
  step = KEY_STEP,
}: {
  orientation: "vertical" | "horizontal"; // vertical = resizes width, horizontal = resizes height
  onResize: (deltaFromStart: number) => void;
  /** Snapshot the value(s) the parent will anchor to (fired on pointerdown). */
  onResizeStart?: () => void;
  /** Cleanup hook (fired on pointerup). */
  onResizeEnd?: () => void;
  ariaLabel?: string;
  /**
   * Current size of the pane this divider sizes, for `aria-valuenow`. Passing
   * `value` + `min` + `max` is what makes the divider a keyboard-operable
   * splitter; without all three it stays a pointer-only affordance.
   */
  value?: number;
  min?: number;
  max?: number;
  /** Pixels moved per arrow-key press. */
  step?: number;
}) {
  // Where the drag started, in viewport coords along the active axis.
  const startCoord = useRef<number | null>(null);
  const [active, setActive] = useState(false);
  const [hover, setHover] = useState(false);
  const [focused, setFocused] = useState(false);
  const vertical = orientation === "vertical";

  // A separator may only be focusable if it can say where it is (ARIA requires
  // aria-valuenow on a focusable separator, and min/max are what give the
  // number meaning), so the parent wiring all three is the switch.
  const publishesPosition = value != null && min != null && max != null;
  const lit = active || hover || focused;
  const lineColor = active || focused ? "var(--accent)" : hover ? "var(--paper-3)" : "transparent";
  const lineThickness = active ? 2 : 1;
  // The grip is the resting affordance, so it is never fully transparent —
  // quiet when idle, solid once the pointer (or focus) arrives.
  // paper-3 at rest (quiet but present), the darker text-paper-d under the
  // pointer, accent while dragging or focused. (--paper-4 does not exist; the
  // token guard test caught that before it shipped as an invisible grip.)
  const gripColor =
    active || focused ? "var(--accent)" : hover ? "var(--text-paper-d)" : "var(--paper-3)";

  /** Arrow keys drive the same anchored-delta contract a drag does: snapshot,
   * then report one step from that snapshot. */
  const nudge = (delta: number) => {
    onResizeStart?.();
    onResize(delta);
  };

  return (
    <div
      role="separator"
      aria-label={ariaLabel}
      aria-orientation={orientation}
      {...(publishesPosition
        ? {
            tabIndex: 0,
            "aria-valuenow": Math.round(value),
            "aria-valuemin": Math.round(min),
            "aria-valuemax": Math.round(max),
          }
        : {})}
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
      onKeyDown={(e) => {
        if (!publishesPosition) return; // not a tab stop, so not arrow-driven either
        const back = vertical ? "ArrowLeft" : "ArrowUp";
        const fwd = vertical ? "ArrowRight" : "ArrowDown";
        if (e.key !== back && e.key !== fwd) return;
        e.preventDefault(); // arrows would otherwise scroll the pane behind
        nudge(e.key === back ? -step : step);
        onResizeEnd?.();
      }}
      onFocus={() => setFocused(true)}
      onBlur={() => setFocused(false)}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        flexShrink: 0,
        cursor: vertical ? "col-resize" : "row-resize",
        background: "transparent",
        position: "relative", // anchor the inner line absolutely
        outline: "none", // focus is shown by the grip + line, not a ring on a 24px strip
        touchAction: "none", // let a touch drag resize instead of scrolling the pane
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
      {/* The resting handle: a short bar centred on the seam. Present always,
          so the control announces itself before anyone points at it. */}
      <div
        aria-hidden
        data-grip
        style={{
          position: "absolute",
          background: gripColor,
          borderRadius: 999,
          pointerEvents: "none",
          transition: active ? "none" : "background 0.15s ease, opacity 0.15s ease",
          opacity: lit ? 1 : 0.9,
          ...(vertical
            ? {
                width: 3,
                height: GRIP,
                left: HALF - 1.5,
                top: `calc(50% - ${GRIP / 2}px)`,
              }
            : {
                height: 3,
                width: GRIP,
                top: HALF - 1.5,
                left: `calc(50% - ${GRIP / 2}px)`,
              }),
        }}
      />
    </div>
  );
}
