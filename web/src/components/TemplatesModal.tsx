/**
 * Templates gallery — lists the available template profiles; picking one
 * opens New Investigation pre-seeded with that template. Presentational:
 * the parent owns the template list + what "pick" does.
 */

import { useEffect } from "react";

import { Icon } from "./Icon";

export function TemplatesModal({
  open,
  templates,
  onPick,
  onClose,
}: {
  open: boolean;
  templates: string[];
  onPick: (profile: string) => void;
  onClose: () => void;
}) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div
      role="presentation"
      onClick={onClose}
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0,0,0,0.4)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 100,
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Templates"
        onClick={(e) => e.stopPropagation()}
        style={{
          width: 480,
          maxWidth: "92vw",
          maxHeight: "80vh",
          overflow: "auto",
          background: "var(--white)",
          borderRadius: "var(--radius-card)",
          border: "1px solid var(--paper-3)",
          boxShadow: "0 16px 40px rgba(0,0,0,0.2)",
          padding: 20,
          display: "flex",
          flexDirection: "column",
          gap: 12,
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <strong style={{ fontSize: 14, flex: 1 }}>Start from a template</strong>
          <button type="button" aria-label="close" onClick={onClose} style={{ color: "var(--text-paper-d)" }}>
            <Icon name="x" size={14} />
          </button>
        </div>
        <p style={{ margin: 0, fontSize: 12, color: "var(--text-paper-d)" }}>
          Pick a template to seed a new investigation's starter files.
        </p>
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {templates.length === 0 && (
            <div style={{ fontSize: 12, color: "var(--text-paper-d2)" }}>No templates found.</div>
          )}
          {templates.map((t) => (
            <button
              key={t}
              type="button"
              onClick={() => onPick(t)}
              style={{
                display: "flex",
                alignItems: "center",
                gap: 10,
                padding: "12px 14px",
                border: "1px solid var(--paper-3)",
                borderRadius: "var(--radius-btn)",
                background: "var(--white)",
                textAlign: "left",
                cursor: "pointer",
              }}
              onMouseEnter={(e) => (e.currentTarget.style.background = "var(--paper-2)")}
              onMouseLeave={(e) => (e.currentTarget.style.background = "var(--white)")}
            >
              <Icon name="file" size={16} color="var(--accent)" />
              <span style={{ flex: 1 }}>
                <span style={{ fontWeight: 600, fontSize: 13 }}>{t}</span>
              </span>
              <Icon name="chev_r" size={14} color="var(--text-paper-d2)" />
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
