/**
 * Which tools the usage tab shows (docs/plan-tool-usage-stats.md §9.2) — the
 * same kind of picker as the per-item tool modal (`ToolsPickerModal` /
 * `ToolsChecklist`), so the two read alike: a search field, one fold per
 * package (the built-ins first, as 核心工具), a fold header that names the
 * package and its count and ticks or clears the whole fold (partly ticked =
 * mixed), the commands indented under it; Cancel and Apply.
 *
 * It edits a DRAFT: the page behind changes on Apply, and Cancel / Escape /
 * ✕ ask once before dropping a changed draft (`useDirtyClose`). Nothing is
 * stored — it is how the administrator is looking at the report right now.
 */
import { useEffect, useRef, useState } from "react";

import { type ToolUsageTool, rowKey } from "../api/toolUsage";
import { ModalShell } from "../components/ModalShell";
import { useDirtyClose } from "../hooks/useDirtyClose";
import { useT } from "../lib/i18n";
import { pxToRem } from "../lib/pxToRem";

type Fold = { id: string; rows: ToolUsageTool[] };

function sameSet(a: ReadonlySet<string>, b: ReadonlySet<string>): boolean {
  return a.size === b.size && [...a].every((x) => b.has(x));
}

export function UsageShownModal({
  names,
  hidden,
  onApply,
  onClose,
}: {
  /** Every name the report has, busiest first. */
  names: ToolUsageTool[];
  /** What is NOT shown now, as row keys (`rowKey`) — the draft starts from it. */
  hidden: ReadonlySet<string>;
  onApply: (hidden: ReadonlySet<string>) => void;
  onClose: () => void;
}) {
  const t = useT();
  const [draft, setDraft] = useState<ReadonlySet<string>>(hidden);
  const [search, setSearch] = useState("");
  const [manual, setManual] = useState<Record<string, boolean>>({});
  const term = search.trim().toLowerCase();
  const dirty = !sameSet(draft, hidden);
  const attemptClose = useDirtyClose(dirty, onClose);
  // Applied, nothing is left unsaved: closing needs no question (the tool
  // modal's save-then-close).
  const apply = () => {
    onApply(draft);
    onClose();
  };

  // One fold per package, the built-ins (no package) first, each busiest first.
  const folds: Fold[] = [];
  for (const r of names) {
    const id = r.package ?? "";
    let fold = folds.find((f) => f.id === id);
    if (!fold) {
      fold = { id, rows: [] };
      folds.push(fold);
    }
    fold.rows.push(r);
  }
  folds.sort((a, b) => (a.id === "" ? -1 : b.id === "" ? 1 : 0));
  const labelOf = (f: Fold) => f.id || t("tools.group.builtin");
  // Under a search a fold IS its matching rows (as in the tool modal); a fold
  // whose own name matches shows all of its rows.
  const visible = folds
    .map((f) => ({
      fold: f,
      rows:
        !term || labelOf(f).toLowerCase().includes(term)
          ? f.rows
          : f.rows.filter((r) => r.tool.toLowerCase().includes(term)),
    }))
    .filter(({ rows }) => rows.length > 0);

  const toggle = (key: string) =>
    setDraft((d) => {
      const next = new Set(d);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  const setAll = (rows: ToolUsageTool[], show: boolean) =>
    setDraft((d) => {
      const next = new Set(d);
      for (const r of rows) {
        if (show) next.delete(rowKey(r));
        else next.add(rowKey(r));
      }
      return next;
    });

  return (
    <ModalShell
      onClose={attemptClose}
      ariaLabel={t("usage.shown.title")}
      width={480}
      maxWidth="92vw"
      panelStyle={{ padding: 18, display: "flex", flexDirection: "column", gap: 10, minHeight: 0 }}
    >
      <strong style={{ fontSize: pxToRem(14) }}>{t("usage.shown.title")}</strong>
      <p style={{ margin: 0, fontSize: pxToRem(12), color: "var(--text-paper-d)", lineHeight: 1.5 }}>
        {t("usage.shown.desc")}
      </p>
      <input
        type="search"
        autoComplete="off"
        className="input input--block"
        aria-label={t("tools.search")}
        placeholder={t("tools.search")}
        value={search}
        onChange={(e) => setSearch(e.target.value)}
      />
      <div style={{ display: "flex", gap: 8 }}>
        <button type="button" className="btn" data-size="sm" data-variant="ghost" onClick={() => setDraft(new Set())}>
          {t("usage.shown.all")}
        </button>
        <button
          type="button"
          className="btn"
          data-size="sm"
          data-variant="ghost"
          onClick={() => setDraft(new Set(names.map(rowKey)))}
        >
          {t("usage.shown.none")}
        </button>
      </div>

      <div
        className="scrollable"
        style={{ overflowY: "auto", minHeight: 0, maxHeight: "50vh", display: "flex", flexDirection: "column", gap: 2 }}
      >
        {visible.map(({ fold, rows }) => {
          const ticked = rows.filter((r) => !draft.has(rowKey(r))).length;
          const state = ticked === rows.length ? "all" : ticked === 0 ? "none" : "mixed";
          const open = manual[fold.id] ?? term !== "";
          const label = labelOf(fold);
          return (
            <div key={fold.id || "builtin"} style={{ display: "flex", flexDirection: "column", gap: 2 }}>
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 10,
                  padding: "6px 6px",
                  borderRadius: "var(--radius-btn)",
                  fontSize: pxToRem(13),
                  background: "var(--paper-2)",
                }}
              >
                <button
                  type="button"
                  aria-expanded={open}
                  onClick={() => setManual((m) => ({ ...m, [fold.id]: !open }))}
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
                  <span style={secondary}>{t("tools.group.count", { n: String(rows.length) })}</span>
                  {state === "mixed" ? <span style={secondary}>· {t("tools.group.mixed")}</span> : null}
                </button>
                <FoldBox
                  label={t("usage.shown.fold", { group: label })}
                  state={state}
                  onChange={() => setAll(rows, state !== "all")}
                />
              </div>
              {open
                ? rows.map((r) => (
                    <label
                      key={rowKey(r)}
                      style={{
                        display: "flex",
                        alignItems: "center",
                        gap: 10,
                        padding: "6px 6px 6px 24px",
                        fontSize: pxToRem(13),
                        fontFamily: "var(--font-mono)",
                        overflowWrap: "anywhere",
                        cursor: "pointer",
                      }}
                    >
                      <input
                        type="checkbox"
                        name={r.tool}
                        checked={!draft.has(rowKey(r))}
                        onChange={() => toggle(rowKey(r))}
                      />
                      {r.tool}
                    </label>
                  ))
                : null}
            </div>
          );
        })}
        {visible.length === 0 && (
          <p style={{ fontSize: pxToRem(12), color: "var(--text-paper-d)" }}>
            {t("tools.noMatch", { q: search.trim() })}
          </p>
        )}
      </div>

      <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 2 }}>
        <button type="button" className="btn" data-variant="secondary" data-size="sm" onClick={attemptClose}>
          {t("tools.cancel")}
        </button>
        <button
          type="button"
          className="btn"
          data-variant="primary"
          data-size="sm"
          disabled={!dirty}
          onClick={apply}
        >
          {t("usage.shown.apply")}
        </button>
      </div>
    </ModalShell>
  );
}

const secondary: React.CSSProperties = {
  fontSize: pxToRem(11),
  color: "var(--text-paper-d)",
  whiteSpace: "nowrap",
};

/** A fold's whole-package tick: checked when every row is shown, empty when
 * none is, indeterminate when some are (the tool modal's "mixed"). */
function FoldBox({
  label,
  state,
  onChange,
}: {
  label: string;
  state: "all" | "none" | "mixed";
  onChange: () => void;
}) {
  const ref = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (ref.current) ref.current.indeterminate = state === "mixed";
  }, [state]);
  return (
    <input ref={ref} type="checkbox" aria-label={label} checked={state === "all"} onChange={onChange} />
  );
}
