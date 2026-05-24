/**
 * Settings — footer gear button + a small modal. v1 holds the theme picker
 * (System / Light / Dark), applied live via useThemeMode and persisted.
 */

import { useState } from "react";

import { type ThemeMode, useThemeMode } from "../hooks/theme";
import { Icon } from "./Icon";

const MODES: { mode: ThemeMode; label: string }[] = [
  { mode: "system", label: "System" },
  { mode: "light", label: "Light" },
  { mode: "dark", label: "Dark" },
];

export function SettingsButton() {
  const [open, setOpen] = useState(false);
  const [mode, setMode] = useThemeMode();

  return (
    <>
      <button
        type="button"
        aria-label="Settings"
        onClick={() => setOpen(true)}
        style={{ color: "var(--text-paper-d)" }}
      >
        <Icon name="settings" size={14} />
      </button>

      {open && (
        <div
          onClick={() => setOpen(false)}
          style={{
            position: "fixed",
            inset: 0,
            background: "rgba(20,22,28,0.35)",
            backdropFilter: "blur(2px)",
            zIndex: 80,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
          }}
        >
          <div
            role="dialog"
            aria-label="Settings"
            onClick={(e) => e.stopPropagation()}
            style={{
              width: 340,
              background: "var(--white)",
              border: "1px solid var(--paper-3)",
              borderRadius: "var(--radius-modal)",
              boxShadow: "0 20px 50px rgba(20,22,28,0.25)",
              padding: 20,
            }}
          >
            <div
              style={{ display: "flex", alignItems: "center", marginBottom: 16, gap: 8 }}
            >
              <span
                style={{
                  fontFamily: "var(--font-display)",
                  fontWeight: 700,
                  fontSize: "var(--text-display-sm)",
                  color: "var(--ink)",
                  flex: 1,
                }}
              >
                Settings
              </span>
              <button
                type="button"
                aria-label="Close"
                onClick={() => setOpen(false)}
                style={{ color: "var(--text-paper-d)" }}
              >
                <Icon name="x" size={16} />
              </button>
            </div>

            <div
              className="caps"
              style={{ fontSize: 10, color: "var(--text-paper-d2)", marginBottom: 8 }}
            >
              Theme
            </div>
            <div role="radiogroup" aria-label="Theme" style={{ display: "flex", gap: 8 }}>
              {MODES.map((m) => {
                const on = mode === m.mode;
                return (
                  <button
                    key={m.mode}
                    type="button"
                    role="radio"
                    aria-checked={on}
                    onClick={() => setMode(m.mode)}
                    style={{
                      flex: 1,
                      display: "inline-flex",
                      alignItems: "center",
                      justifyContent: "center",
                      gap: 5,
                      padding: "8px 10px",
                      borderRadius: "var(--radius-btn)",
                      border: `1px solid ${on ? "var(--accent)" : "var(--paper-3)"}`,
                      background: on ? "var(--accent-soft)" : "var(--white)",
                      color: on ? "var(--accent-h)" : "var(--text-paper)",
                      fontWeight: on ? 600 : 400,
                      fontSize: "var(--text-body-sm)",
                      cursor: "pointer",
                    }}
                  >
                    {on && <Icon name="check" size={12} />}
                    {m.label}
                  </button>
                );
              })}
            </div>
          </div>
        </div>
      )}
    </>
  );
}
