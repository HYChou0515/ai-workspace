/**
 * The action row of a modal whose body can outgrow the panel — pinned to the
 * bottom of the scrolling area so Save is always on screen.
 *
 * ModalShell already promises "a max-height + inner overflow safety net so a
 * tall modal never pushes its actions off a short viewport", but a plain footer
 * only keeps that promise while the content fits: grant eight people in the
 * share dialog and Save ends up 30px below the panel, reachable only by
 * scrolling — which reads as "the button disappeared".
 *
 * The negative margins bleed the bar across the panel's own padding so the
 * content scrolls UNDER an opaque strip rather than through a transparent gap
 * beside it. Callers keep passing their own buttons; this owns only the pinning.
 */
import type { ReactNode } from "react";

/** Matches the padding the share dialogs give their ModalShell panel. */
const PANEL_PAD = 18;

export function ModalActions({ children }: { children: ReactNode }) {
  return (
    <div data-testid="modal-actions" style={bar}>
      {children}
    </div>
  );
}

const bar: React.CSSProperties = {
  position: "sticky",
  bottom: -PANEL_PAD,
  zIndex: 1,
  display: "flex",
  justifyContent: "flex-end",
  gap: 8,
  margin: `2px -${PANEL_PAD}px -${PANEL_PAD}px`,
  padding: `10px ${PANEL_PAD}px ${PANEL_PAD}px`,
  background: "var(--white)",
  borderTop: "1px solid var(--paper-3)",
};
