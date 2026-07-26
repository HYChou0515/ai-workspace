/**
 * The "View" settings panel (#GH-projects P3) — a gear popover that consolidates
 * Group by, multi-level Sort, and Fields (show/hide) for a Table / Board view,
 * modelled on GitHub Projects' View menu. Every change applies locally at once
 * (the container recomputes the effective spec); "Save to view" persists the
 * choice into the view's `.ai.yaml`. Driven entirely by the `ViewConfig` the
 * container (`AiYamlRenderer`) builds — this component holds only open/closed UI.
 */

import { useEffect, useRef, useState } from "react";

import type { SortRule, ViewConfig } from "./types";

const SORT_TIER_CAP = 3;

export function ViewSettingsPanel({ config }: { config: ViewConfig }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  const setTier = (i: number, patch: Partial<SortRule>) =>
    config.onSetSort(config.sort.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  const removeTier = (i: number) => config.onSetSort(config.sort.filter((_, j) => j !== i));
  const addTier = () => {
    const used = new Set(config.sort.map((r) => r.field));
    const next = config.sortOptions.find((o) => !used.has(o.name));
    if (next) config.onSetSort([...config.sort, { field: next.name, dir: "asc" }]);
  };
  const canAdd = config.sort.length < Math.min(SORT_TIER_CAP, config.sortOptions.length);

  return (
    <div className="ev-viewpanel" ref={ref}>
      <button
        type="button"
        className="btn"
        data-variant="ghost"
        data-size="sm"
        aria-label="view settings"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        ⚙ View
        {config.dirty && <span className="ev-viewpanel__dot" aria-hidden />}
      </button>
      {open && (
        <div role="dialog" aria-label="view settings" className="ev-viewpanel__pop">
          {config.groupOptions.length > 0 && (
          <section className="ev-viewpanel__sec">
            <label className="ev-viewpanel__label" htmlFor="ev-groupby">
              Group by
            </label>
            <select
              id="ev-groupby"
              className="ev-select"
              aria-label="group by"
              value={config.groupBy}
              onChange={(e) => config.onGroupBy(e.target.value)}
            >
              <option value="">None</option>
              {config.groupOptions.map((o) => (
                <option key={o.name} value={o.name}>
                  {o.label}
                </option>
              ))}
            </select>
          </section>
          )}

          {config.sortOptions.length > 0 && (
          <section className="ev-viewpanel__sec">
            <div className="ev-viewpanel__label">Sort by</div>
            {config.sort.length === 0 && <div className="ev-viewpanel__hint">Manual order (drag to reorder)</div>}
            {config.sort.map((rule, i) => (
              <div key={`${rule.field}-${i}`} className="ev-viewpanel__tier">
                <select
                  className="ev-select"
                  aria-label={`sort field ${i + 1}`}
                  value={rule.field}
                  onChange={(e) => setTier(i, { field: e.target.value })}
                >
                  {config.sortOptions.map((o) => (
                    <option key={o.name} value={o.name}>
                      {o.label}
                    </option>
                  ))}
                </select>
                <button
                  type="button"
                  className="btn"
                  data-variant="ghost"
                  data-size="sm"
                  aria-label={`sort direction ${i + 1}`}
                  onClick={() => setTier(i, { dir: rule.dir === "asc" ? "desc" : "asc" })}
                >
                  {rule.dir === "asc" ? "↑ Asc" : "↓ Desc"}
                </button>
                <button
                  type="button"
                  className="btn"
                  data-variant="ghost"
                  data-size="sm"
                  aria-label={`remove sort ${i + 1}`}
                  onClick={() => removeTier(i)}
                >
                  ✕
                </button>
              </div>
            ))}
            {canAdd && (
              <button
                type="button"
                className="btn"
                data-variant="ghost"
                data-size="sm"
                aria-label="add sort"
                onClick={addTier}
              >
                + Add sort
              </button>
            )}
          </section>
          )}

          {config.fieldOptions.length > 0 && (
          <section className="ev-viewpanel__sec">
            <div className="ev-viewpanel__label">Fields</div>
            {config.fieldOptions.map((f) => (
              <label key={f.name} className="ev-viewpanel__field">
                <input
                  type="checkbox"
                  aria-label={`show ${f.name}`}
                  checked={!config.hidden.includes(f.name)}
                  onChange={() => config.onToggleField(f.name)}
                />
                {f.label}
              </label>
            ))}
          </section>
          )}

          {config.onToggleSkipWeekends && (
            <section className="ev-viewpanel__sec">
              <div className="ev-viewpanel__label">Working days</div>
              <label className="ev-viewpanel__field">
                <input
                  type="checkbox"
                  aria-label="Skip weekends"
                  checked={config.skipWeekends ?? false}
                  onChange={() => config.onToggleSkipWeekends?.(!config.skipWeekends)}
                />
                Skip weekends (Mon–Fri only)
              </label>
            </section>
          )}

          {config.onSetAssigneeDisplay && (
            <section className="ev-viewpanel__sec">
              <label className="ev-viewpanel__label" htmlFor="ev-people">
                People
              </label>
              <select
                id="ev-people"
                className="ev-select"
                aria-label="people display"
                value={config.assigneeDisplay ?? "avatar"}
                onChange={(e) => config.onSetAssigneeDisplay?.(e.target.value)}
              >
                <option value="avatar">Avatar</option>
                <option value="name">Name</option>
                <option value="none">Off</option>
              </select>
            </section>
          )}

          {config.dirty && (
            <div className="ev-viewpanel__foot">
              <button
                type="button"
                className="btn"
                data-variant="primary"
                data-size="sm"
                disabled={config.saving}
                onClick={config.onSave}
              >
                Save to view
              </button>
              <button type="button" className="btn" data-variant="ghost" data-size="sm" onClick={config.onReset}>
                Reset
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
