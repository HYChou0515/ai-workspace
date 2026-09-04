/**
 * The "View" settings panel (#GH-projects P3) — a gear popover that consolidates
 * Group by, multi-level Sort, and Fields (show/hide) for a Table / Board view,
 * modelled on GitHub Projects' View menu. Every change applies locally at once
 * (the container recomputes the effective spec); "Save to view" persists the
 * choice into the view's `.ai.yaml`. Driven entirely by the `ViewConfig` the
 * container (`AiYamlRenderer`) builds — this component holds only open/closed UI.
 */

import { useEffect, useRef, useState } from "react";

import { clockHours, clockText } from "./shared";
import type { SortRule, ViewConfig } from "./types";

const SORT_TIER_CAP = 3;

/** What "skip non-working hours" means before anyone narrows it (#785). */
const DEFAULT_WORK_HOURS = { from: 7, to: 21 };

/** Move one end of the working day, or decline to. A window that closes before
 * it opens is dropped by the parser anyway; refusing it at the control means
 * the view file never holds one, so nobody has to find that out. */
function setEnd(config: ViewConfig, end: "from" | "to", text: string) {
  const hours = clockHours(text);
  if (hours === undefined || !config.workHours) return;
  const next = { ...config.workHours, [end]: hours };
  if (next.from >= next.to) return;
  config.onSetWorkHours?.(next);
}

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

          {(config.onToggleSkipWeekends || config.onSetWorkHours) && (
            <section className="ev-viewpanel__sec">
              {/* Both controls say the same thing at two scales — which time the
                  chart does not draw — so they share a section, and each is
                  gated on its own callback rather than on the other's. */}
              <div className="ev-viewpanel__label">Working days</div>
              {config.onToggleSkipWeekends && (
                <label className="ev-viewpanel__field">
                  <input
                    type="checkbox"
                    aria-label="Skip weekends"
                    checked={config.skipWeekends ?? false}
                    onChange={() => config.onToggleSkipWeekends?.(!config.skipWeekends)}
                  />
                  Skip weekends (Mon–Fri only)
                </label>
              )}
              {config.onSetWorkHours && (
                <>
                  <label className="ev-viewpanel__field">
                    <input
                      type="checkbox"
                      aria-label="Skip non-working hours"
                      checked={Boolean(config.workHours)}
                      // Switching ON means a working day, not an empty one: a
                      // window with no hours in it folds the whole day away and
                      // leaves the chart blank.
                      onChange={() =>
                        config.onSetWorkHours?.(config.workHours ? undefined : DEFAULT_WORK_HOURS)
                      }
                    />
                    Skip non-working hours
                  </label>
                  {config.workHours && (
                    <div className="ev-viewpanel__field">
                      <input
                        type="time"
                        aria-label="day starts"
                        value={clockText(config.workHours.from)}
                        onChange={(e) => setEnd(config, "from", e.target.value)}
                      />
                      <span> to </span>
                      <input
                        type="time"
                        aria-label="day ends"
                        value={clockText(config.workHours.to)}
                        onChange={(e) => setEnd(config, "to", e.target.value)}
                      />
                    </div>
                  )}
                </>
              )}
            </section>
          )}

          {config.onSetColorBy && (
            <section className="ev-viewpanel__sec">
              <div className="ev-viewpanel__label">Colour</div>
              <select
                className="ev-select"
                aria-label="colour by"
                value={config.colorBy ?? ""}
                onChange={(e) => config.onSetColorBy?.(e.target.value)}
              >
                <option value="">Off</option>
                {config.colorByOptions?.map((o) => (
                  <option key={o.name} value={o.name}>
                    {o.label}
                  </option>
                ))}
              </select>
            </section>
          )}

          {config.onSetWeekday && (
            <section className="ev-viewpanel__sec">
              <div className="ev-viewpanel__label">Time axis</div>
              <label className="ev-viewpanel__field">
                <input
                  type="checkbox"
                  aria-label="always show week"
                  checked={config.alwaysWeek ?? false}
                  onChange={() => config.onToggleAlwaysWeek?.(!config.alwaysWeek)}
                />
                Always show the week
              </label>
              <select
                className="ev-select"
                aria-label="weekday format"
                value={config.weekday ?? "number"}
                onChange={(e) => config.onSetWeekday?.(e.target.value)}
              >
                <option value="number">Weekdays as 1 2 3</option>
                <option value="short">Weekdays as Mon Tue</option>
              </select>
              <select
                className="ev-select"
                aria-label="day of month"
                value={config.dayOfMonth ?? "hidden"}
                onChange={(e) => config.onSetDayOfMonth?.(e.target.value)}
              >
                <option value="hidden">No date</option>
                <option value="always">Date under the day</option>
                <option value="hover">Date on hover</option>
              </select>
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
