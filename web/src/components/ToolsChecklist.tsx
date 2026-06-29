import { useState } from "react";

import type { ItemToolState, ToolPref } from "../api/types";
import { useT } from "../lib/i18n";
import { pxToRem } from "../lib/pxToRem";

/**
 * The per-item tool picker list (#322). One row per pickable App tool, each with
 * a tri-state control — Default (follow the template/profile), On (force on), or
 * Off (force off). Purely presentational and controlled: the caller owns the
 * sparse override `prefs` (a `Record<key, boolean>` — present key = pinned
 * on/off, absent = follow) and applies whatever `onChange` hands back, mirroring
 * the backend `attached_tool_prefs` storage exactly.
 *
 * A row in the Default state shows what the template currently resolves to, so
 * "follow" is never ambiguous. Search filters by label/key; "reset to defaults"
 * clears the override for the currently-visible rows.
 */
export function ToolsChecklist({
  tools,
  prefs,
  onChange,
}: {
  tools: ItemToolState[];
  prefs: Record<string, boolean>;
  onChange: (next: Record<string, boolean>) => void;
}) {
  const t = useT();
  const [search, setSearch] = useState("");
  const term = search.trim().toLowerCase();
  const visible = tools.filter(
    (tool) => tool.label.toLowerCase().includes(term) || tool.key.toLowerCase().includes(term),
  );

  const stateOf = (key: string): ToolPref =>
    key in prefs ? (prefs[key] ? "on" : "off") : "follow";

  const setState = (key: string, next: ToolPref) => {
    const out = { ...prefs };
    if (next === "follow") delete out[key];
    else out[key] = next === "on";
    onChange(out);
  };

  const resetVisible = () => {
    const out = { ...prefs };
    for (const tool of visible) delete out[tool.key];
    onChange(out);
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8, flex: 1, minHeight: 0 }}>
      <input
        data-testid="tools-search"
        placeholder={t("tools.search")}
        value={search}
        onChange={(e) => setSearch(e.target.value)}
        style={{
          width: "100%",
          height: 30,
          boxSizing: "border-box",
          padding: "0 10px",
          fontSize: pxToRem(13),
          borderRadius: "var(--radius-btn)",
          border: "1px solid var(--paper-3)",
          background: "var(--paper-1, var(--white))",
          color: "var(--text-paper)",
        }}
      />

      {tools.length > 0 && (
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <button type="button" data-testid="tools-reset" onClick={resetVisible} style={linkBtn()}>
            {t("tools.resetVisible")}
          </button>
        </div>
      )}

      <div
        style={{ overflowY: "auto", minHeight: 0, flex: 1, display: "flex", flexDirection: "column", gap: 2 }}
      >
        {visible.map((tool) => {
          const state = stateOf(tool.key);
          return (
            <div
              key={tool.key}
              data-testid={`tool-row-${tool.key}`}
              style={{
                display: "flex",
                alignItems: "center",
                gap: 10,
                padding: "6px 6px",
                borderRadius: "var(--radius-btn)",
                fontSize: pxToRem(13),
              }}
            >
              <div style={{ flex: 1, minWidth: 0 }}>
                <div
                  style={{
                    fontWeight: 500,
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                  }}
                >
                  {tool.label}
                </div>
                <div
                  style={{
                    fontSize: pxToRem(11),
                    color: "var(--text-paper-d)",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                  }}
                >
                  {state === "follow"
                    ? tool.default_on
                      ? t("tools.defaultOn")
                      : t("tools.defaultOff")
                    : tool.description}
                </div>
              </div>
              <div
                role="group"
                aria-label={t("tools.state.aria", { tool: tool.label })}
                style={{ display: "flex", border: "1px solid var(--paper-3)", borderRadius: "var(--radius-btn)", overflow: "hidden" }}
              >
                {(["follow", "on", "off"] as ToolPref[]).map((opt) => (
                  <button
                    key={opt}
                    type="button"
                    data-testid={`tool-${tool.key}-${opt}`}
                    aria-pressed={state === opt}
                    onClick={() => setState(tool.key, opt)}
                    style={segBtn(state === opt)}
                  >
                    {t(opt === "follow" ? "tools.follow" : opt === "on" ? "tools.on" : "tools.off")}
                  </button>
                ))}
              </div>
            </div>
          );
        })}

        {tools.length > 0 && visible.length === 0 && (
          <p style={{ fontSize: pxToRem(12), color: "var(--text-paper-d)" }}>
            {t("tools.noMatch", { q: search.trim() })}
          </p>
        )}
        {tools.length === 0 && (
          <p style={{ fontSize: pxToRem(12), color: "var(--text-paper-d)" }}>{t("tools.none")}</p>
        )}
      </div>
    </div>
  );
}

function segBtn(active: boolean): React.CSSProperties {
  return {
    height: 24,
    padding: "0 10px",
    fontSize: pxToRem(12),
    border: "none",
    borderRight: "1px solid var(--paper-3)",
    background: active ? "var(--accent)" : "var(--white)",
    color: active ? "var(--white)" : "var(--text-paper)",
    cursor: "pointer",
  };
}

function linkBtn(): React.CSSProperties {
  return {
    height: 24,
    padding: "0 8px",
    fontSize: pxToRem(12),
    borderRadius: "var(--radius-btn)",
    border: "1px solid var(--paper-3)",
    background: "var(--white)",
    color: "var(--text-paper)",
    cursor: "pointer",
  };
}
