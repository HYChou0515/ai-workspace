/**
 * The versioned welcome teaching modal (#161). Presentational only — the caller
 * (via `useOnboarding`) decides when it's open and what dismissal means:
 *   - "Got it" (onGotIt): close for now; shows again next visit.
 *   - "Don't show again" (onDontShowAgain): permanently dismiss this version.
 * Escape / backdrop are the soft close (onGotIt), never a permanent dismiss.
 */

import { useEffect } from "react";

import type { Onboarding } from "../api/types";
import { pxToRem } from "../lib/pxToRem";

export function OnboardingModal({
  content,
  onGotIt,
  onDontShowAgain,
}: {
  content: Onboarding;
  onGotIt: () => void;
  onDontShowAgain: () => void;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onGotIt();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onGotIt]);

  return (
    <div
      role="presentation"
      onClick={onGotIt}
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0,0,0,0.4)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 200,
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label={content.title}
        onClick={(e) => e.stopPropagation()}
        style={{
          width: 460,
          maxWidth: "92vw",
          maxHeight: "86vh",
          overflowY: "auto",
          background: "var(--white)",
          borderRadius: "var(--radius-card)",
          border: "1px solid var(--paper-3)",
          boxShadow: "0 16px 40px rgba(0,0,0,0.22)",
          padding: 24,
          display: "flex",
          flexDirection: "column",
          gap: 16,
        }}
      >
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <h2 style={{ fontSize: pxToRem(20), fontWeight: 800, margin: 0, letterSpacing: "-0.01em" }}>
            {content.title}
          </h2>
          {content.intro && (
            <p style={{ fontSize: pxToRem(14), color: "var(--text-paper-d)", margin: 0, lineHeight: 1.5 }}>
              {content.intro}
            </p>
          )}
        </div>

        {content.points.length > 0 && (
          <ol style={{ listStyle: "none", margin: 0, padding: 0, display: "flex", flexDirection: "column", gap: 12 }}>
            {content.points.map((p, i) => (
              <li key={p.title} style={{ display: "flex", gap: 12, alignItems: "flex-start" }}>
                <span
                  aria-hidden="true"
                  style={{
                    flex: "0 0 auto",
                    width: 24,
                    height: 24,
                    borderRadius: 999,
                    background: "var(--accent)",
                    color: "var(--white)",
                    fontSize: pxToRem(12),
                    fontWeight: 700,
                    display: "inline-flex",
                    alignItems: "center",
                    justifyContent: "center",
                  }}
                >
                  {i + 1}
                </span>
                <div style={{ minWidth: 0 }}>
                  <div style={{ fontSize: pxToRem(14), fontWeight: 600 }}>{p.title}</div>
                  <div style={{ fontSize: pxToRem(13), color: "var(--text-paper-d)", lineHeight: 1.5 }}>
                    {p.body}
                  </div>
                </div>
              </li>
            ))}
          </ol>
        )}

        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8, marginTop: 4 }}>
          <button
            type="button"
            onClick={onDontShowAgain}
            style={{
              height: 32,
              padding: "0 12px",
              borderRadius: "var(--radius-btn)",
              fontSize: pxToRem(13),
              cursor: "pointer",
              border: "none",
              background: "transparent",
              color: "var(--text-paper-d)",
            }}
          >
            Don't show again
          </button>
          <button
            type="button"
            autoFocus
            onClick={onGotIt}
            style={{
              height: 32,
              padding: "0 16px",
              borderRadius: "var(--radius-btn)",
              fontSize: pxToRem(13),
              cursor: "pointer",
              border: "1px solid var(--accent)",
              background: "var(--accent)",
              color: "var(--white)",
              fontWeight: 600,
            }}
          >
            Got it
          </button>
        </div>
      </div>
    </div>
  );
}
