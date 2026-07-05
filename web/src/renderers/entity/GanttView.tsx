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

import { useState } from "react";

import type { EntityInstance } from "../../api/entities";
import { pxToRem } from "../../lib/pxToRem";
import {
  applyDrag,
  daysBetween,
  deltaDays,
  type DragMode,
  pxPerDay,
  shiftDate,
  type Span,
  spanToDates,
  spanValue,
  type Zoom,
} from "./ganttScale";
import type { RefIndex } from "./refTraversal";
import { fieldText, roleOf } from "./shared";
import type { EntityViewProps } from "./types";

const GUTTER = 150;
const AXIS_H = 22;
const LANE_H = 24;
const ROW_H = 26;
const ZOOMS: Zoom[] = ["day", "week", "month"];
const TICK_DAYS: Record<Zoom, number> = { day: 1, week: 7, month: 30 };

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
  const [zoom, setZoom] = useState<Zoom>("week");
  const [drag, setDrag] = useState<Drag | null>(null);

  const rows: Row[] = entities
    .map((e) => ({ e, span: spanToDates(e.fields[spanField]) }))
    .filter((r): r is Row => r.span !== null);

  if (rows.length === 0) {
    return <div style={{ color: "var(--text-paper-d)" }}>No records with a date range to chart yet.</div>;
  }

  const ppd = pxPerDay(zoom);
  const minDate = rows.map((r) => r.span.start).reduce((m, s) => (s < m ? s : m));
  const maxDate = rows.map((r) => r.span.end).reduce((m, e) => (e > m ? e : m));
  const totalDays = daysBetween(minDate, maxDate) + 1;
  const chartWidth = totalDays * ppd;
  const xOf = (date: string) => daysBetween(minDate, date) * ppd;

  const lanes = groupLanes(rows, spec.group_by, type, refIndex);
  const grouped = Boolean(spec.group_by);

  // Drag: capture the down point + zoom, track on window, commit one patch on up.
  const startDrag = (number: number, mode: DragMode, e: React.PointerEvent) => {
    if (busy) return;
    e.preventDefault();
    const row = rows.find((r) => r.e.number === number);
    if (!row) return;
    const downX = e.clientX;
    const z = zoom;
    const onMove = (ev: PointerEvent) => setDrag({ number, mode, days: deltaDays(ev.clientX - downX, z) });
    const onUp = (ev: PointerEvent) => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      setDrag(null);
      const days = deltaDays(ev.clientX - downX, z);
      if (days !== 0) onPatch(number, { [spanField]: spanValue(applyDrag(row.span, mode, days)) });
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    setDrag({ number, mode, days: 0 });
  };

  const previewSpan = (row: Row): Span =>
    drag && drag.number === row.e.number ? applyDrag(row.span, drag.mode, drag.days) : row.span;

  const ticks: string[] = [];
  for (let d = 0; d < totalDays; d += TICK_DAYS[zoom]) ticks.push(shiftDate(minDate, d));

  const today = new Date().toISOString().slice(0, 10);
  const todayInRange = today >= minDate && today <= maxDate;

  return (
    <div>
      <div role="group" aria-label="zoom" style={{ display: "flex", gap: 4, marginBottom: 8 }}>
        {ZOOMS.map((z) => (
          <button
            key={z}
            type="button"
            className="btn"
            data-variant={zoom === z ? "primary" : "secondary"}
            data-size="sm"
            data-active={zoom === z}
            aria-label={`zoom ${z}`}
            onClick={() => setZoom(z)}
          >
            {z}
          </button>
        ))}
      </div>

      <div style={{ overflowX: "auto" }}>
        <div style={{ display: "flex", minWidth: GUTTER + chartWidth }}>
          {/* left gutter: axis spacer + lane headers + row labels */}
          <div style={{ width: GUTTER, flex: "0 0 auto" }}>
            <div style={{ height: AXIS_H }} />
            {lanes.map((lane) => (
              <div key={lane.key}>
                {grouped && (
                  <div style={{ height: LANE_H, fontWeight: 600, fontSize: pxToRem(13), display: "flex", alignItems: "center" }}>
                    {lane.label}
                  </div>
                )}
                {lane.rows.map((row) => (
                  <div
                    key={row.e.number}
                    style={{
                      height: ROW_H,
                      display: "flex",
                      alignItems: "center",
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                      paddingRight: 8,
                    }}
                  >
                    {fieldText(row.e.fields[labelField]) || `#${row.e.number}`}
                  </div>
                ))}
              </div>
            ))}
          </div>

          {/* right timeline: axis ticks + today line + bars */}
          <div style={{ position: "relative", width: chartWidth, flex: "0 0 auto" }}>
            <div style={{ position: "relative", height: AXIS_H, borderBottom: "1px solid var(--paper-3)" }}>
              {ticks.map((t) => (
                <span
                  key={t}
                  style={{ position: "absolute", left: xOf(t), fontSize: pxToRem(11), color: "var(--text-paper-d)", whiteSpace: "nowrap" }}
                >
                  {t.slice(5)}
                </span>
              ))}
            </div>

            {todayInRange && (
              <div
                data-testid="gantt-today"
                title="today"
                style={{ position: "absolute", top: 0, bottom: 0, left: xOf(today), width: 2, background: "var(--accent)", opacity: 0.5 }}
              />
            )}

            {lanes.map((lane) => (
              <div key={lane.key}>
                {grouped && <div style={{ height: LANE_H }} />}
                {lane.rows.map((row) => {
                  const ps = previewSpan(row);
                  const left = xOf(ps.start);
                  const width = Math.max(daysBetween(ps.start, ps.end), 1) * ppd;
                  return (
                    <div key={row.e.number} style={{ position: "relative", height: ROW_H }}>
                      <div
                        data-testid={`bar-${row.e.number}`}
                        title={spanValue(ps)}
                        onPointerDown={(e) => startDrag(row.e.number, "move", e)}
                        style={{
                          position: "absolute",
                          left,
                          width,
                          top: 3,
                          bottom: 3,
                          background: "var(--accent)",
                          borderRadius: 4,
                          cursor: busy ? "default" : "grab",
                          touchAction: "none",
                        }}
                      >
                        <div
                          data-testid={`bar-${row.e.number}-start`}
                          onPointerDown={(e) => {
                            e.stopPropagation();
                            startDrag(row.e.number, "start", e);
                          }}
                          style={{ position: "absolute", left: 0, top: 0, bottom: 0, width: 6, cursor: busy ? "default" : "ew-resize" }}
                        />
                        <div
                          data-testid={`bar-${row.e.number}-end`}
                          onPointerDown={(e) => {
                            e.stopPropagation();
                            startDrag(row.e.number, "end", e);
                          }}
                          style={{ position: "absolute", right: 0, top: 0, bottom: 0, width: 6, cursor: busy ? "default" : "ew-resize" }}
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
