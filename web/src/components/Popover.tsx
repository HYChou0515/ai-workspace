/**
 * Tiny popover. Opens below its trigger; closes on outside click or Esc.
 * No fancy positioning — assumes the trigger anchors top-left and the
 * popover hangs down-left. Good enough for filter dropdowns.
 */

import { useEffect, useId, useRef, useState } from "react";

export function Popover({
  trigger,
  children,
  align = "start",
  side = "bottom",
  width,
}: {
  trigger: (props: { onClick: () => void; open: boolean }) => React.ReactNode;
  children: (close: () => void) => React.ReactNode;
  align?: "start" | "end";
  /** Which side of the trigger to open on. Use "top" for triggers anchored near
   * the bottom of the viewport (e.g. the composer) so the menu isn't clipped. */
  side?: "top" | "bottom";
  width?: number;
}) {
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLDivElement>(null);
  const id = useId();

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div ref={wrapRef} style={{ position: "relative", display: "inline-block" }}>
      {trigger({ onClick: () => setOpen((v) => !v), open })}
      {open && (
        <div
          id={id}
          role="dialog"
          style={{
            position: "absolute",
            [side === "top" ? "bottom" : "top"]: "calc(100% + 6px)",
            [align === "start" ? "left" : "right"]: 0,
            background: "var(--white)",
            border: "1px solid var(--paper-3)",
            borderRadius: "var(--radius-card)",
            boxShadow: "0 6px 20px rgba(20,22,28,0.08)",
            minWidth: width ?? 200,
            zIndex: 50,
            padding: 4,
          }}
        >
          {children(() => setOpen(false))}
        </div>
      )}
    </div>
  );
}

/* ---------------------------- Pieces ---------------------------- */

export function PopoverItem({
  selected,
  disabled,
  onClick,
  children,
}: {
  selected?: boolean;
  disabled?: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      style={{
        display: "flex",
        alignItems: "center",
        gap: 8,
        width: "100%",
        padding: "6px 10px",
        textAlign: "left",
        background: "transparent",
        borderRadius: 4,
        fontSize: 13,
        color: disabled ? "var(--text-paper-d2)" : "var(--text-paper)",
        cursor: disabled ? "not-allowed" : "pointer",
      }}
      onMouseEnter={(e) => {
        if (disabled) return;
        (e.currentTarget as HTMLButtonElement).style.background = "var(--paper-2)";
      }}
      onMouseLeave={(e) => {
        (e.currentTarget as HTMLButtonElement).style.background = "transparent";
      }}
    >
      <span
        aria-hidden
        style={{
          width: 12,
          height: 12,
          border: "1px solid var(--paper-3)",
          borderRadius: 3,
          background: selected ? "var(--accent)" : "transparent",
          display: "inline-flex",
          alignItems: "center",
          justifyContent: "center",
          color: "var(--white)",
          fontSize: 10,
        }}
      >
        {selected ? "✓" : ""}
      </span>
      <span>{children}</span>
    </button>
  );
}

export function PopoverDivider() {
  return (
    <div
      style={{
        height: 1,
        background: "var(--paper-3)",
        margin: "4px 0",
      }}
    />
  );
}
