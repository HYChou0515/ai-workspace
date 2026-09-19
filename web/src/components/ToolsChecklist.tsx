import { useState } from "react";

import type { ItemToolState, ToolPref } from "../api/types";
import { useT } from "../lib/i18n";
import { pxToRem } from "../lib/pxToRem";
import {
  BUILTIN_GROUP,
  type ToolGroup,
  groupState,
  groupsOf,
  prefOf,
  withGroupState,
} from "../lib/toolGroups";

/**
 * The per-item tool picker list (#322). One row per pickable App tool, each with
 * a tri-state control — Default (follow the template/profile), On (force on), or
 * Off (force off). Purely presentational and controlled: the caller owns the
 * sparse override `prefs` (a `Record<key, boolean>` — present key = pinned
 * on/off, absent = follow) and applies whatever `onChange` hands back, mirroring
 * the backend `attached_tool_prefs` storage exactly.
 *
 * Rows fold by package (plan-tools-picker-groups): every built-in under one
 * fold literally named `builtin`, a package's granted commands under its own.
 * A fold's header carries the same tri-state, DERIVED from its rows — the one
 * state they all share, or "mixed" with nothing pressed — and pressing it sets
 * every row of the fold. Nothing is stored per fold. A fold opens collapsed
 * unless it is mixed (the only time a reader needs to look inside) or the
 * search matched inside it; a fold of one row is just that row.
 *
 * A row in the Default state shows what the template currently resolves to, so
 * "follow" is never ambiguous. Search filters by label/key (and fold name);
 * "reset to defaults" clears the override for every row the search left in.
 *
 * Every row states where it came from (#724): the platform, or the third party
 * who published it and which release of theirs resolved. `app.json` grants at
 * whichever granularity it chose, so a row that is ONE COMMAND of a bundle also
 * names the bundle — otherwise two rows read as peers while one is part of the
 * other, and a command seen in a chat card cannot be traced to its switch.
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
  // A fold the reader opened or shut by hand; anything else follows the
  // auto rule (mixed / search hit ⇒ open).
  const [manual, setManual] = useState<Record<string, boolean>>({});
  const term = search.trim().toLowerCase();

  const groups = groupsOf(tools);
  const matches = (tool: ItemToolState) =>
    tool.label.toLowerCase().includes(term) || tool.key.toLowerCase().includes(term);
  // A fold whose own name matches shows every row of it; otherwise only the
  // rows that match. No search ⇒ everything.
  const visibleOf = (g: ToolGroup): ItemToolState[] => {
    if (!term) return g.tools;
    const label = g.id === BUILTIN_GROUP ? t("tools.group.builtin") : g.label;
    if (label.toLowerCase().includes(term) || g.id.toLowerCase().includes(term)) return g.tools;
    return g.tools.filter(matches);
  };
  const shown = groups
    .map((g) => ({ group: g, rows: visibleOf(g) }))
    .filter(({ rows }) => rows.length > 0);
  const visible = shown.flatMap(({ rows }) => rows);

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
          background: "var(--white)",
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

      <div className="scrollable"
        style={{ overflowY: "auto", minHeight: 0, flex: 1, display: "flex", flexDirection: "column", gap: 2 }}
      >
        {shown.map(({ group, rows }) => {
          // A fold of one granted row is that row: a header would show the
          // same tri-state twice.
          if (group.tools.length === 1) {
            return <ToolRow key={group.id} tool={group.tools[0]!} state={prefOf(group.tools[0]!.key, prefs)} setState={setState} />;
          }
          const state = groupState(group, prefs);
          const open = manual[group.id] ?? (state === "mixed" || (term !== "" && rows.length > 0));
          const label = group.id === BUILTIN_GROUP ? t("tools.group.builtin") : group.label;
          return (
            <div key={group.id} data-testid={`tool-group-${group.id}`} style={{ display: "flex", flexDirection: "column", gap: 2 }}>
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 10,
                  padding: "6px 6px",
                  borderRadius: "var(--radius-btn)",
                  fontSize: pxToRem(13),
                  background: "var(--paper-1)",
                }}
              >
                <button
                  type="button"
                  data-testid={`tool-group-header-${group.id}`}
                  aria-expanded={open}
                  aria-label={t("tools.group.toggle", { group: label })}
                  title={group.id === BUILTIN_GROUP ? label : `${group.id} · ${label}`}
                  onClick={() => setManual((m) => ({ ...m, [group.id]: !open }))}
                  style={{
                    flex: 1,
                    minWidth: 0,
                    display: "flex",
                    alignItems: "baseline",
                    gap: 6,
                    padding: 0,
                    border: "none",
                    background: "transparent",
                    color: "var(--text-paper)",
                    cursor: "pointer",
                    textAlign: "left",
                    font: "inherit",
                  }}
                >
                  <span aria-hidden="true" style={{ display: "inline-block", width: "1em", fontSize: pxToRem(11) }}>
                    {open ? "▾" : "▸"}
                  </span>
                  <span style={{ fontWeight: 600, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {label}
                  </span>
                  {/* The count and the mixed mark yield first: the fold's NAME
                      is what a reader scans for, and at phone width there is
                      not room for all three at full size. */}
                  <span style={secondary()}>
                    {t("tools.group.count", { n: String(group.tools.length) })}
                  </span>
                  {state === "mixed" ? (
                    <span data-testid={`tool-group-${group.id}-mixed`} style={secondary()}>
                      · {t("tools.group.mixed")}
                    </span>
                  ) : null}
                </button>
                <TriState
                  aria={t("tools.group.aria", { group: label })}
                  testId={`tool-group-${group.id}`}
                  state={state}
                  onPick={(next) => onChange(withGroupState(group, prefs, next))}
                />
              </div>
              {open
                ? rows.map((tool) => (
                    <ToolRow key={tool.key} tool={tool} state={prefOf(tool.key, prefs)} setState={setState} indent />
                  ))
                : null}
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

/** One granted tool: its name, where it came from, what Default means for it,
 * and its own tri-state. */
function ToolRow({
  tool,
  state,
  setState,
  indent = false,
}: {
  tool: ItemToolState;
  state: ToolPref;
  setState: (key: string, next: ToolPref) => void;
  indent?: boolean;
}) {
  const t = useT();
  // The secondary line shows the follow-default hint or the tool's own
  // description; both it and the label clip on one line, so mirror the
  // shown text into title= for a hover tooltip when it overflows (#456).
  const detail =
    state === "follow"
      ? tool.default_on
        ? t("tools.defaultOn")
        : t("tools.defaultOff")
      : tool.description;
  // Who to go to. A third-party tool names its author (or says nobody
  // claimed it — which is NOT the same as it being ours); everything
  // else is the platform's own.
  //
  // Nothing at all when it could not be resolved: there is no release
  // and no author, so "no author published" would be describing a
  // manifest nobody read. The reason line carries that row instead.
  const origin = tool.unavailable
    ? ""
    : !tool.external
      ? t("tools.origin.builtin")
      : tool.author
        ? t("tools.origin.by", { author: tool.author })
        : t("tools.origin.noAuthor");
  return (
    <div
      data-testid={`tool-row-${tool.key}`}
      style={{
        display: "flex",
        alignItems: "center",
        gap: 10,
        padding: "6px 6px",
        paddingLeft: indent ? 24 : 6,
        borderRadius: "var(--radius-btn)",
        fontSize: pxToRem(13),
      }}
    >
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: "flex", alignItems: "baseline", gap: 6, minWidth: 0 }}>
          <div
            title={tool.package ? `${tool.package} · ${tool.label}` : tool.label}
            style={{
              fontWeight: 500,
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}
          >
            {tool.package ? (
              <span style={{ fontWeight: 400, color: "var(--text-paper-d)" }}>
                {tool.package}
                {" · "}
              </span>
            ) : null}
            {tool.label}
          </div>
          {origin || tool.version ? (
            <div
              title={tool.version ? `${tool.version} · ${origin}` : origin}
              style={{
                // Yields first. The tool's own NAME is what a reader
                // scans for, so provenance clips before the label does
                // — `Wafer His… 1.4.2 · by Wafer Team` is the wrong way
                // round. Both stay shrinkable so a long label cannot
                // push the chip off the row entirely.
                flexShrink: 999,
                minWidth: 0,
                fontSize: pxToRem(11),
                color: "var(--text-paper-d)",
                overflow: "hidden",
                textOverflow: "ellipsis",
                whiteSpace: "nowrap",
              }}
            >
              {tool.version ? `${tool.version} · ` : ""}
              {origin}
            </div>
          ) : null}
        </div>
        <div
          title={tool.unavailable ?? detail}
          style={{
            fontSize: pxToRem(11),
            color: tool.unavailable ? "var(--err)" : "var(--text-paper-d)",
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          {tool.unavailable
            ? t("tools.origin.unavailable", { reason: tool.unavailable })
            : detail}
          {tool.stale && !tool.unavailable ? (
            <span data-testid={`tool-${tool.key}-stale`}>
              {" · "}
              {t("tools.origin.stale")}
            </span>
          ) : null}
        </div>
      </div>
      <TriState
        aria={t("tools.state.aria", { tool: tool.label })}
        testId={`tool-${tool.key}`}
        state={state}
        onPick={(next) => setState(tool.key, next)}
      />
    </div>
  );
}

/** The Default / On / Off segmented control. A `mixed` state presses nothing:
 * that is what a fold looks like when its rows disagree. */
function TriState({
  aria,
  testId,
  state,
  onPick,
}: {
  aria: string;
  testId: string;
  state: ToolPref | "mixed";
  onPick: (next: ToolPref) => void;
}) {
  const t = useT();
  return (
    <div
      role="group"
      aria-label={aria}
      style={{ display: "flex", border: "1px solid var(--paper-3)", borderRadius: "var(--radius-btn)", overflow: "hidden", flexShrink: 0 }}
    >
      {(["follow", "on", "off"] as ToolPref[]).map((opt) => (
        <button
          key={opt}
          type="button"
          data-testid={`${testId}-${opt}`}
          aria-pressed={state === opt}
          onClick={() => onPick(opt)}
          style={segBtn(state === opt)}
        >
          {t(opt === "follow" ? "tools.follow" : opt === "on" ? "tools.on" : "tools.off")}
        </button>
      ))}
    </div>
  );
}

function secondary(): React.CSSProperties {
  return {
    fontSize: pxToRem(11),
    color: "var(--text-paper-d)",
    flexShrink: 999,
    minWidth: 0,
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
  };
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
