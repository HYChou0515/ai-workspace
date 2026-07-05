/**
 * Imperative confirm dialog. `useDialog().confirm(opts)` returns a promise
 * that resolves with the chosen action id (or null on Escape / backdrop /
 * Cancel). Replaces window.confirm/alert for delete + save-on-close prompts.
 */

import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { pxToRem } from "../lib/pxToRem";

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

  useEffect(() => {
    if (!opts) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") settle(null);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
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
                  autoFocus={a.variant === "primary"}
                  onClick={() => settle(a.id)}
                  style={actionStyle(a.variant)}
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

function actionStyle(variant: DialogAction["variant"]): React.CSSProperties {
  const base: React.CSSProperties = {
    height: 30,
    padding: "0 14px",
    borderRadius: "var(--radius-btn)",
    fontSize: pxToRem(13),
    cursor: "pointer",
    border: "1px solid var(--paper-3)",
    background: "var(--white)",
    color: "var(--text-paper)",
  };
  if (variant === "primary") {
    return { ...base, background: "var(--accent)", borderColor: "var(--accent)", color: "var(--white)" };
  }
  if (variant === "danger") {
    return { ...base, color: "var(--err)", borderColor: "var(--err)" };
  }
  return base;
}

export function useDialog(): DialogContextValue {
  const ctx = useContext(DialogContext);
  if (!ctx) throw new Error("useDialog must be used inside <DialogProvider>");
  return ctx;
}

/** The dialog in context, or `null` when there's no provider. The read-only
 * FileTree select mode (#415 card-gen picker) has no confirm prompts (they're
 * all on caps-gated mutations), so it reads the dialog optionally. */
export function useOptionalDialog(): DialogContextValue | null {
  return useContext(DialogContext);
}
