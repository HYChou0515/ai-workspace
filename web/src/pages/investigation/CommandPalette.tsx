/**
 * Command palette — ⌘P. Modal list of files in the investigation, fuzzy-matched
 * and ranked by relevance (so `wafmap` finds `wafer_map.csv`). A typed `/` also
 * matches the `∕` slash look-alike a path may carry (see lib/fuzzy). Pick one →
 * opens it in the editor.
 */

import { useEffect, useMemo, useRef, useState } from "react";

import type { FileInfo } from "../../api/types";
import { Icon } from "../../components/Icon";
import { fuzzyFilter } from "../../lib/fuzzy";
import { basename } from "./renderer";
import { pxToRem } from "../../lib/pxToRem";

export function CommandPalette({
  open,
  files,
  onClose,
  onPick,
}: {
  open: boolean;
  files: FileInfo[];
  onClose: () => void;
  onPick: (path: string) => void;
}) {
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (open) {
      setQuery("");
      setActive(0);
      // Defer focus a tick so the input is mounted.
      setTimeout(() => inputRef.current?.focus(), 0);
    }
  }, [open]);

  const filtered = useMemo(
    () => fuzzyFilter(query, files, (f) => f.path).slice(0, 50),
    [query, files],
  );

  if (!open) return null;

  const onKey = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((i) => Math.min(filtered.length - 1, i + 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((i) => Math.max(0, i - 1));
    } else if (e.key === "Enter") {
      e.preventDefault();
      const hit = filtered[active];
      if (hit) {
        onPick(hit.path);
        onClose();
      }
    } else if (e.key === "Escape") {
      onClose();
    }
  };

  return (
    <div
      role="dialog"
      aria-label="Go to file"
      onClick={onClose}
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(20,22,28,0.55)",
        backdropFilter: "blur(4px)",
        display: "flex",
        justifyContent: "center",
        paddingTop: 80,
        zIndex: 200,
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          width: 560,
          maxHeight: "60vh",
          background: "var(--white)",
          border: "1px solid var(--paper-3)",
          borderRadius: "var(--radius-modal)",
          display: "flex",
          flexDirection: "column",
          overflow: "hidden",
        }}
      >
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            padding: "10px 14px",
            borderBottom: "1px solid var(--paper-3)",
          }}
        >
          <Icon name="search" size={14} color="var(--text-paper-d)" />
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={onKey}
            placeholder="Go to file…"
            style={{
              flex: 1,
              border: 0,
              outline: "none",
              background: "transparent",
              fontSize: "var(--text-body)",
            }}
          />
          <span
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: pxToRem(11),
              color: "var(--text-paper-d2)",
              border: "1px solid var(--paper-3)",
              borderRadius: 4,
              padding: "0 6px",
            }}
          >
            esc
          </span>
        </div>
        <div className="scrollable" style={{ overflowY: "auto" }}>
          {filtered.length === 0 && (
            <div style={{ padding: 16, fontSize: pxToRem(13), color: "var(--text-paper-d)" }}>
              No files match.
            </div>
          )}
          {filtered.map((f, i) => {
            const isActive = i === active;
            return (
              <button
                key={f.path}
                type="button"
                onMouseEnter={() => setActive(i)}
                onClick={() => {
                  onPick(f.path);
                  onClose();
                }}
                style={{
                  width: "100%",
                  // Let the flex children shrink so a long path ellipsizes
                  // instead of overflowing the fixed-width modal (which clips
                  // it) when a larger system font is in use (#256).
                  minWidth: 0,
                  textAlign: "left",
                  padding: "8px 14px",
                  display: "flex",
                  alignItems: "center",
                  gap: 8,
                  background: isActive ? "var(--accent-soft)" : "transparent",
                  color: isActive ? "var(--accent-h)" : "var(--text-paper)",
                  fontSize: pxToRem(13),
                }}
              >
                <Icon name="file" size={13} color={isActive ? "var(--accent-h)" : "var(--text-paper-d)"} />
                <span style={{ flexShrink: 0 }}>{basename(f.path)}</span>
                <span style={{ flex: 1, minWidth: 8 }} />
                <span
                  style={{
                    fontFamily: "var(--font-mono)",
                    fontSize: pxToRem(11),
                    color: isActive ? "var(--accent-h)" : "var(--text-paper-d2)",
                    minWidth: 0,
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                  }}
                >
                  {f.path}
                </span>
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
}
