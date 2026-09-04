/**
 * Shared modal shell (#445 #14/#15/#18). Owns the concerns that every
 * hand-rolled overlay used to re-implement inconsistently: a dimmed fixed
 * backdrop on the one z-index scale (--z-modal, so modals stop colliding with
 * the brand splash / progress bar), Escape-to-close, and a max-height + inner
 * overflow safety net so a tall modal never pushes its actions off a short
 * viewport.
 *
 * Backdrop-click-to-close is OFF by default (#779). It used to be on, and 20 of
 * the 21 call sites simply inherited it — so a stray click beside the panel
 * silently threw away half-typed forms, pasted credentials and unsent
 * permission changes, in modals whose authors never chose that behaviour. The
 * default now withdraws the one exit a user never means to take; a modal that
 * genuinely wants it (a read-only viewer, a palette) asks by passing the prop.
 *
 * The deliberate exits — Escape, ✕, Cancel — all run through `onClose`, so a
 * modal holding unsaved work guards them in one place by wrapping it with
 * `useDirtyClose`, rather than the shell trying to know what "dirty" means.
 *
 * It deliberately does NOT impose an inner layout: a migrating modal passes its
 * existing panel styles (width / padding / display / gap) via `panelStyle`,
 * which override the shell defaults, so its content renders exactly as before.
 */
import { useEffect, useRef, type CSSProperties, type ReactNode } from "react";

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), textarea:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';

export function ModalShell({
  onClose,
  children,
  ariaLabel,
  labelledBy,
  width,
  maxWidth = "90vw",
  closeOnBackdrop = false,
  closeOnEscape = true,
  align = "center",
  zIndex = "var(--z-modal)",
  panelStyle,
  backdropStyle,
  panelClassName,
  "data-testid": testId,
}: {
  onClose: () => void;
  children: ReactNode;
  /** Accessible name when there's no visible title element to point at. */
  ariaLabel?: string;
  /** id of the visible title element (preferred over ariaLabel when present). */
  labelledBy?: string;
  width?: number | string;
  maxWidth?: number | string;
  closeOnBackdrop?: boolean;
  closeOnEscape?: boolean;
  /** Vertical placement of the panel within the backdrop. */
  align?: "center" | "top";
  /** Override for stacking (e.g. var(--z-dialog) for a confirm over a modal). */
  zIndex?: number | string;
  panelStyle?: CSSProperties;
  backdropStyle?: CSSProperties;
  panelClassName?: string;
  "data-testid"?: string;
}) {
  const panelRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!closeOnEscape) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose, closeOnEscape]);

  // Focus management (#467): pull focus into the panel on open, trap Tab within
  // it so keyboard users can't tab out to the page behind, and restore focus to
  // whatever was focused before (the trigger) on close. Runs once per open.
  useEffect(() => {
    const panel = panelRef.current;
    if (!panel) return;
    const restoreTo = document.activeElement as HTMLElement | null;
    const focusables = () => Array.from(panel.querySelectorAll<HTMLElement>(FOCUSABLE));

    // Only claim focus if the content hasn't already placed it (#779 P5).
    // React's `autoFocus` leaves no attribute behind — it calls .focus() while
    // mounting, which is BEFORE this effect — so the check is "is focus already
    // inside the panel", not "is anything marked". Without it the shell hands
    // focus to whatever sits highest in the DOM, and a form whose first control
    // is a segmented toggle never gets the caret into the field the person
    // opened it to type in.
    if (!panel.contains(document.activeElement)) {
      (focusables()[0] ?? panel).focus();
    }

    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Tab") return;
      const els = focusables();
      if (els.length === 0) {
        e.preventDefault();
        panel.focus();
        return;
      }
      const first = els[0];
      const last = els[els.length - 1];
      const active = document.activeElement;
      if (e.shiftKey && (active === first || !panel.contains(active))) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && (active === last || !panel.contains(active))) {
        e.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      restoreTo?.focus?.();
    };
  }, []);

  return (
    <div
      role="presentation"
      data-testid={testId ? `${testId}-backdrop` : undefined}
      onClick={closeOnBackdrop ? () => onClose() : undefined}
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0,0,0,0.4)",
        display: "flex",
        alignItems: align === "top" ? "flex-start" : "center",
        justifyContent: "center",
        padding: 24,
        zIndex,
        ...backdropStyle,
      }}
    >
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-label={ariaLabel}
        aria-labelledby={labelledBy}
        data-testid={testId}
        className={panelClassName}
        tabIndex={-1}
        onClick={(e) => e.stopPropagation()}
        style={{
          background: "var(--white)",
          border: "1px solid var(--paper-3)",
          borderRadius: "var(--radius-modal)",
          boxShadow: "0 16px 40px rgba(0,0,0,0.22)",
          maxWidth,
          maxHeight: "85vh",
          overflowY: "auto",
          width,
          ...panelStyle,
        }}
      >
        {children}
      </div>
    </div>
  );
}
