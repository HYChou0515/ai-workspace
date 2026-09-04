/**
 * Imperative confirm dialog. `useDialog().confirm(opts)` returns a promise
 * that resolves with the chosen action id (or null on Escape / backdrop /
 * Cancel). Replaces window.confirm/alert for delete + save-on-close prompts.
 */

import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { pxToRem } from "../lib/pxToRem";
import { LAYER_ATTR } from "./ModalShell";

export type DialogAction = {
  id: string;
  label: string;
  variant?: "primary" | "danger" | "default";
};

export type DialogOptions = {
  title: string;
  body?: React.ReactNode;
  actions: DialogAction[];
};

type DialogContextValue = { confirm: (opts: DialogOptions) => Promise<string | null> };

const DialogContext = createContext<DialogContextValue | null>(null);

export function DialogProvider({ children }: { children: React.ReactNode }) {
  const [opts, setOpts] = useState<DialogOptions | null>(null);
  const resolver = useRef<((r: string | null) => void) | null>(null);
  const panelRef = useRef<HTMLDivElement>(null);

  const settle = useCallback((r: string | null) => {
    resolver.current?.(r);
    resolver.current = null;
    setOpts(null);
  }, []);

  const confirm = useCallback(
    (o: DialogOptions) =>
      new Promise<string | null>((resolve) => {
        resolver.current = resolve;
        setOpts(o);
      }),
    [],
  );

  // Pull focus into the prompt when it opens (#779). It used to rely on
  // `autoFocus` on a primary action, so a prompt with no primary — "keep
  // editing" / "discard changes" is two non-primary choices — left focus in the
  // modal underneath. That modal traps Tab inside its own panel, and these
  // buttons are outside it, so the prompt became mouse-only: a keyboard user
  // could answer "keep" (Escape) and nothing else. An undismissable modal by a
  // longer route, which is the thing keeping Escape was supposed to prevent.
  useEffect(() => {
    if (!opts) return;
    // The PANEL, not a button. Focusing a button means the keystroke that
    // opened this prompt can finish on it: type a name and press Enter, the
    // collision confirm appears mid-keypress, and the same Enter activates the
    // freshly-focused action — the dialog answers itself before anyone reads it.
    // Focusing the container puts focus inside the prompt (so Tab reaches the
    // actions and it is answerable by keyboard) without arming anything.
    const restoreTo = document.activeElement as HTMLElement | null;
    panelRef.current?.focus();
    // And hand it back on close, as APG requires and as ModalShell already did.
    // Without this, "keep editing" kept the modal open exactly as promised but
    // left the caret on <body> — the next keystroke went nowhere, and Tab
    // restarted from the panel's first control instead of the field being typed
    // in. A prompt that costs you your place is one people avoid raising.
    return () => restoreTo?.focus?.();
  }, [opts]);

  // While a confirm is up it is the TOP layer, so Escape is its alone (#779).
  // ModalShell listens on document too; without this both handlers run, and for
  // a modal with unsaved work that means cancelling this confirm and opening a
  // fresh one in the same keystroke. Capture + stopImmediatePropagation is what
  // makes "the topmost layer takes the key" a rule rather than an accident of
  // which listener happened to be registered first.
  useEffect(() => {
    if (!opts) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      e.stopImmediatePropagation();
      settle(null);
    };
    document.addEventListener("keydown", onKey, true);
    return () => document.removeEventListener("keydown", onKey, true);
  }, [opts, settle]);

  return (
    <DialogContext.Provider value={{ confirm }}>
      {children}
      {opts && (
        <div
          role="presentation"
          onClick={() => settle(null)}
          style={{
            position: "fixed",
            inset: 0,
            background: "rgba(0,0,0,0.4)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            zIndex: "var(--z-dialog)",
          }}
        >
          <div
            ref={panelRef}
            tabIndex={-1}
            // Part of the layer stack ModalShell reads, so a modal underneath
            // knows to stand down while this is up (#779).
            {...{ [LAYER_ATTR]: "" }}
            role="dialog"
            aria-modal="true"
            aria-label={opts.title}
            onClick={(e) => e.stopPropagation()}
            style={{
              width: 420,
              maxWidth: "90vw",
              background: "var(--white)",
              borderRadius: "var(--radius-card)",
              border: "1px solid var(--paper-3)",
              boxShadow: "0 16px 40px rgba(0,0,0,0.22)",
              padding: 20,
              display: "flex",
              flexDirection: "column",
              gap: 12,
            }}
          >
            <strong style={{ fontSize: pxToRem(14) }}>{opts.title}</strong>
            {opts.body != null && (
              <div style={{ fontSize: pxToRem(13), color: "var(--text-paper-d)", lineHeight: 1.5 }}>
                {opts.body}
              </div>
            )}
            <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 4 }}>
              {opts.actions.map((a) => (
                <button
                  key={a.id}
                  type="button"
                  className="btn"
                  // Addressable by action id, so a test picks the button by what
                  // it DOES rather than by its wording — the labels are i18n and
                  // the default locale differs between here and CI.
                  data-testid={`dialog-action-${a.id}`}
                  data-variant={a.variant === "primary" ? "primary" : a.variant === "danger" ? "danger" : "secondary"}
                  data-size="sm"
                  onClick={() => settle(a.id)}
                >
                  {a.label}
                </button>
              ))}
            </div>
          </div>
        </div>
      )}
    </DialogContext.Provider>
  );
}

export function useDialog(): DialogContextValue {
  const ctx = useContext(DialogContext);
  if (!ctx) throw new Error("useDialog must be used inside <DialogProvider>");
  return ctx;
}
