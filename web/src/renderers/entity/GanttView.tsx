/**
 * gantt view (#419 §B, #448 A2 / #450) — records as bars on a fixed px-per-day
 * timeline. Interactive:
 *   - drag a bar's body to reschedule (both ends, keeps duration); drag a left/
 *     right handle to resize one edge — a drop writes the daterange via `onPatch`
 *     (the useEntityWrite optimistic + 409 path).
 *   - zoom day / week / month (px-per-day density) with horizontal scroll + a
 *     time axis + a "today" marker.
 *   - `group_by` lays records into swimlanes (a ref group resolves its lane
 *     label through the ref index; §A2).
 * The date/drag arithmetic lives in `ganttScale` (pure, unit-tested); this file
 * is the pointer-event + layout shell. Registered as the `gantt` kind.
 *
 * Dependency lines are intentionally out of scope — they need a to-many ref the
 * backend role vocabulary doesn't have yet (tracked as a #450 sub-item).
 */

import { useEffect, useRef, useState } from "react";

import type { EntityInstance } from "../../api/entities";
import {
  applyDrag,
  axisFor,
  canvasWidthFor,
  daysBetween,
  deltaDays,
  type DragMode,
  PPD_ANCHORS,
  ppdToSlider,
  type Span,
  sliderToPpd,
  spanToDates,
  spanValue,
  visibleDaysFor,
  type Zoom,
} from "./ganttScale";
import type { RefIndex } from "./refTraversal";
import { fieldText, roleOf } from "./shared";
import type { EntityViewProps } from "./types";

const GUTTER = 150;
const COARSE_H = 18; // top context band (month / year)
const FINE_H = 20; // fine tick row (day numbers / week starts / months)
const AXIS_H = COARSE_H + FINE_H;
const LANE_H = 24;
const ROW_H = 26;
const ZOOMS: Zoom[] = ["day", "week", "month"];

type Row = { e: EntityInstance; span: Span };
type Lane = { key: string; label: string | null; rows: Row[] };
type Drag = { number: number; mode: DragMode; days: number };

function groupLanes(rows: Row[], groupField: string | undefined, type: EntityViewProps["type"], refIndex: RefIndex | undefined): Lane[] {
  if (!groupField) return [{ key: "__all__", label: null, rows }];
  const spec = roleOf(type, groupField);
  const byKey = new Map<string, Lane>();
  const order: string[] = [];
  for (const row of rows) {
    const raw = row.e.fields[groupField];
    let key: string;
    let label: string;
    if (raw == null || raw === "") {
      key = "__ungrouped__";
      label = "(ungrouped)";
    } else if (spec?.role === "ref" && spec.to && refIndex) {
      const num = Number(raw);
      const target = refIndex.get(spec.to)?.get(num);
      key = String(raw);
      label = target ? fieldText(target.fields.title) || `#${num}` : `#${num}?`;
    } else {
      key = fieldText(raw);
      label = key;
    }
    let lane = byKey.get(key);
    if (!lane) {
      lane = { key, label, rows: [] };
      byKey.set(key, lane);
      order.push(key);
    }
    lane.rows.push(row);
  }
  return order.map((k) => byKey.get(k)!);
}

export function GanttView({ spec, type, entities, refIndex, onPatch, busy }: EntityViewProps) {
  const spanField = spec.span ?? "span";
  const labelField = spec.label ?? "title";
  const [ppd, setPpd] = useState<number>(PPD_ANCHORS.week);
  const [drag, setDrag] = useState<Drag | null>(null);
  // Measure the scroll pane so a short project can FILL its width (max(pane,
  // content)) instead of hugging a half-empty card; a long one still scrolls.
  const scrollRef = useRef<HTMLDivElement>(null);
  const [paneWidth, setPaneWidth] = useState(0);
  useEffect(() => {
    const el = scrollRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver((entries) => {
      for (const e of entries) setPaneWidth(e.contentRect.width);
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const rows: Row[] = entities
    .map((e) => ({ e, span: spanToDates(e.fields[spanField]) }))
    .filter((r): r is Row => r.span !== null);

  if (rows.length === 0) {
    return <div style={{ color: "var(--text-paper-d)" }}>No records with a date range to chart yet.</div>;
  }

  const minDate = rows.map((r) => r.span.start).reduce((m, s) => (s < m ? s : m));
  const maxDate = rows.map((r) => r.span.end).reduce((m, e) => (e > m ? e : m));
  const totalDays = daysBetween(minDate, maxDate) + 1;
  // Fill the pane when the data is short, scroll when it's long; the dated grid
  // then extends across the whole canvas so there is no empty gap.
  const canvasWidth = canvasWidthFor(totalDays, ppd, paneWidth - GUTTER);
  const visibleDays = visibleDaysFor(canvasWidth, ppd);
  const xOf = (date: string) => daysBetween(minDate, date) * ppd;

  const lanes = groupLanes(rows, spec.group_by, type, refIndex);
  const grouped = Boolean(spec.group_by);

  // Drag: capture the down point + density, track on window, commit one patch on up.
  const startDrag = (number: number, mode: DragMode, e: React.PointerEvent) => {
    if (busy) return;
    e.preventDefault();
    const row = rows.find((r) => r.e.number === number);
    if (!row) return;
    const downX = e.clientX;
    const dragPpd = ppd;
    const onMove = (ev: PointerEvent) => setDrag({ number, mode, days: deltaDays(ev.clientX - downX, dragPpd) });
    const onUp = (ev: PointerEvent) => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      setDrag(null);
      const days = deltaDays(ev.clientX - downX, dragPpd);
      if (days !== 0) onPatch(number, { [spanField]: spanValue(applyDrag(row.span, mode, days)) });
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    setDrag({ number, mode, days: 0 });
  };

  const previewSpan = (row: Row): Span =>
    drag && drag.number === row.e.number ? applyDrag(row.span, drag.mode, drag.days) : row.span;

  const axis = axisFor(minDate, visibleDays, ppd);

  const today = new Date().toISOString().slice(0, 10);
  const todayOffset = daysBetween(minDate, today);
  const todayInRange = todayOffset >= 0 && todayOffset < visibleDays;

  return (
    <div>
      <div role="group" aria-label="zoom" className="ev-gantt__toolbar" style={{ marginBottom: 8 }}>
        <div className="ev-gantt__zoom">
          <input
            type="range"
            className="ev-gantt__zoom-range"
            min={0}
            max={1}
            step={0.001}
            value={ppdToSlider(ppd)}
            aria-label="zoom"
            onChange={(e) => setPpd(sliderToPpd(Number(e.target.value)))}
          />
          <div className="ev-gantt__zoom-anchors">
            {ZOOMS.map((z) => (
              <button
                key={z}
                type="button"
                className="ev-gantt__zoom-anchor"
                data-active={Math.abs(ppd - PPD_ANCHORS[z]) < 0.5 || undefined}
                style={{ left: `${ppdToSlider(PPD_ANCHORS[z]) * 100}%` }}
                aria-label={`zoom ${z}`}
                onClick={() => setPpd(PPD_ANCHORS[z])}
              >
                {z}
              </button>
            ))}
          </div>
        </div>
      </div>

      <div className="ev-gantt__scroll scrollable" ref={scrollRef}>
        <div className="ev-gantt__grid" style={{ minWidth: GUTTER + canvasWidth }}>
          {/* left gutter: axis spacer + lane headers + row labels */}
          <div className="ev-gantt__gutter" style={{ width: GUTTER }}>
            <div style={{ height: AXIS_H }} />
            {lanes.map((lane) => (
              <div key={lane.key}>
                {grouped && (
                  <div className="ev-gantt__lane-label" style={{ height: LANE_H }}>
                    {lane.label}
                  </div>
                )}
                {lane.rows.map((row) => (
                  <div key={row.e.number} className="ev-gantt__row-label" style={{ height: ROW_H }}>
                    {fieldText(row.e.fields[labelField]) || `#${row.e.number}`}
                  </div>
                ))}
              </div>
            ))}
          </div>

          {/* right timeline: gridlines + axis ticks + today line + bars */}
          <div className="ev-gantt__canvas" style={{ width: canvasWidth }}>
            {axis.fine.map((t) => (
              <div key={`grid-${t.day}`} className="ev-gantt__gridline" style={{ left: t.day * ppd }} />
            ))}
            <div className="ev-gantt__axis" style={{ height: AXIS_H }}>
              <div className="ev-gantt__axis-coarse" style={{ height: COARSE_H }}>
                {axis.coarse.map((b) => (
                  <span
                    key={`coarse-${b.day}`}
                    className="ev-gantt__coarse-band"
                    style={{ left: b.day * ppd, width: b.days * ppd }}
                  >
                    {b.label}
                  </span>
                ))}
              </div>
              <div className="ev-gantt__axis-fine" style={{ height: FINE_H }}>
                {axis.fine.map((t) => (
                  <span key={`fine-${t.day}`} className="ev-gantt__tick" style={{ left: t.day * ppd }}>
                    {t.label}
                  </span>
                ))}
              </div>
            </div>

            {todayInRange && (
              <div data-testid="gantt-today" title="today" className="ev-gantt__today" style={{ left: xOf(today) }} />
            )}

            {lanes.map((lane) => (
              <div key={lane.key}>
                {grouped && <div className="ev-gantt__lane-band" style={{ height: LANE_H }} />}
                {lane.rows.map((row) => {
                  const ps = previewSpan(row);
                  const left = xOf(ps.start);
                  const width = Math.max(daysBetween(ps.start, ps.end), 1) * ppd;
                  return (
                    <div key={row.e.number} className="ev-gantt__bar-row" style={{ height: ROW_H }}>
                      <div
                        data-testid={`bar-${row.e.number}`}
                        title={spanValue(ps)}
                        className="ev-gantt__bar"
                        data-busy={busy ? "1" : undefined}
                        onPointerDown={(e) => startDrag(row.e.number, "move", e)}
                        style={{ left, width }}
                      >
                        <span className="ev-gantt__bar-label">
                          {fieldText(row.e.fields[labelField]) || `#${row.e.number}`}
                        </span>
                        <div
                          data-testid={`bar-${row.e.number}-start`}
                          className="ev-gantt__handle ev-gantt__handle--start"
                          onPointerDown={(e) => {
                            e.stopPropagation();
                            startDrag(row.e.number, "start", e);
                          }}
                        />
                        <div
                          data-testid={`bar-${row.e.number}-end`}
                          className="ev-gantt__handle ev-gantt__handle--end"
                          onPointerDown={(e) => {
                            e.stopPropagation();
                            startDrag(row.e.number, "end", e);
                          }}
                        />
                      </div>
                    </div>
                  );
                })}
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
