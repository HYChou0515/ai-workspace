/**
 * Shared modal shell (#445 #14/#15/#18). Owns the concerns that every
 * hand-rolled overlay used to re-implement inconsistently: a dimmed fixed
 * backdrop on the one z-index scale (--z-modal, so modals stop colliding with
 * the brand splash / progress bar), Escape-to-close AND backdrop-click-to-close
 * (many overlays only had the latter), and a max-height + inner overflow safety
 * net so a tall modal never pushes its actions off a short viewport.
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
  closeOnBackdrop = true,
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

    (focusables()[0] ?? panel).focus();

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
