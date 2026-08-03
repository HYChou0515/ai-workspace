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

import {
  DndContext,
  type DragEndEvent,
  PointerSensor,
  useDraggable,
  useDroppable,
  useSensor,
  useSensors,
} from "@dnd-kit/core";
import { useEffect, useRef, useState } from "react";

import type { EntityInstance } from "../../api/entities";
import type { User } from "../../api/types";
import { rowDropResult } from "./ganttOps";
import { type ScheduleReport, scheduleRows } from "./schedule";
import {
  applyDrag,
  axisFor,
  barColumns,
  canvasWidthFor,
  clampPpd,
  columnOf,
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
import { selectColor } from "./selectColor";
import { sortRows } from "./sortRows";
import type { EntityViewProps } from "./types";

/** What the run did, in one line. The surprise worth naming is the third one:
 * an automatic record you had dragged somewhere by hand comes back to the
 * chain, and silently moving someone's work is how a button loses their trust. */
function reportText(r: ScheduleReport): string {
  const parts = [`Scheduled ${r.scheduled}`];
  if (r.movedBack > 0) parts.push(`${r.movedBack} moved back onto the chain`);
  if (r.untouched > 0) parts.push(`${r.untouched} left alone (manual)`);
  return `${parts.join(" · ")}.`;
}

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

function groupLanes(
  rows: Row[],
  groupField: string | undefined,
  type: EntityViewProps["type"],
  refIndex: RefIndex | undefined,
  users: User[] | undefined,
): Lane[] {
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
    } else if (spec?.role === "actor") {
      // A resource view (group_by an assignee) labels each lane with the person's
      // NAME, not the raw user id (§②).
      key = fieldText(raw);
      label = users?.find((u) => u.id === key)?.name ?? key;
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

export function GanttView({
  spec,
  type,
  entities,
  users,
  refIndex,
  onPatch,
  onPatchAnchor,
  onOpenRecord,
  canWrite = true,
  busy,
}: EntityViewProps) {
  const spanField = spec.span ?? "span";
  const labelField = spec.label ?? "title";
  const assigneeField = spec.assignee;
  const assigneeDisplay = spec.assignee_display ?? "avatar";
  // #690 P2 — which field decides a bar's colour. Absent ⇒ bars keep the
  // single default colour they have always had, so no existing view changes
  // appearance the day this ships.
  const colorField = spec.color_by;
  const colorSpec = colorField ? roleOf(type, colorField) : undefined;
  const barColor = (e: EntityInstance): string | undefined => {
    if (!colorField) return undefined;
    // The palette the chips already use. A second one would put one `status`
    // value on two different colours in two places on the same screen.
    return selectColor(fieldText(e.fields[colorField]) ?? "", colorSpec).bg;
  };
  // null ⇒ auto-fit the whole project to the measured pane (fills the width on
  // open); a number ⇒ the user has taken over the zoom via the slider / anchors.
  const [manualPpd, setManualPpd] = useState<number | null>(null);
  const [drag, setDrag] = useState<Drag | null>(null);
  const [report, setReport] = useState<ScheduleReport | null>(null);
  // Injected into the pure scheduler; also the chart's "today" marker.
  const today = new Date().toISOString().slice(0, 10);
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
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 5 } }));

  // #GH-projects — order rows by the view's sort tiers, or (with none) the manual
  // `rank`, exactly like the table/board, so the Timeline reads in the SAME order.
  // Every record in Timeline order. `rows` is the subset that can be DRAWN; the
  // scheduler works from `ordered`, because a record with no dates yet is
  // exactly the one most in need of being given some.
  const ordered = sortRows(entities, spec.sort, type ?? null, refIndex, users);
  const rows: Row[] = ordered
    .map((e) => ({ e, span: spanToDates(e.fields[spanField]) }))
    .filter((r): r is Row => r.span !== null);

  // #PM auto-schedule — present only when the view names the fields that carry
  // the schedule, so a plain gantt stays a plain drawing of dates.
  const sched = spec.schedule;
  const isAutoRow = (e: EntityInstance) => (fieldText(e.fields[sched!.flag]) || "auto") !== "manual";
  // A length nobody chose: the record is scheduled but carries no estimate, so
  // the bar is a placeholder the run had to invent. Drawn differently, because
  // a guess that looks like a plan is the one thing worse than no bar at all.
  const isProvisional = (e: EntityInstance) =>
    !!sched && isAutoRow(e) && !(Number(e.fields[sched.duration]) > 0);
  const anchorTo = sched?.anchor ? (roleOf(type, sched.anchor)?.to ?? undefined) : undefined;
  const anchorRecords = anchorTo ? [...(refIndex?.get(anchorTo)?.values() ?? [])] : [];
  const recalculate = () => {
    if (!sched || busy) return;
    const result = scheduleRows({ issues: ordered, milestones: anchorRecords, today, fields: sched });
    for (const p of result.issues) if (p.changed) onPatch(p.number, { [sched.span]: p.span });
    for (const m of result.milestones) if (m.changed) onPatchAnchor?.(m.number, { [sched.span]: m.span });
    setReport(result.report);
  };
  const scheduleBar = sched ? (
    <div className="ev-gantt__schedule">
      <button
        type="button"
        className="btn"
        data-variant="secondary"
        data-size="sm"
        disabled={busy || !canWrite}
        onClick={recalculate}
      >
        Recalculate
      </button>
      {report && (
        <span role="status" className="ev-gantt__schedule-report">
          {reportText(report)}
        </span>
      )}
    </div>
  ) : null;

  if (rows.length === 0) {
    return (
      <div>
        {scheduleBar}
        <div style={{ color: "var(--text-paper-d)" }}>No records with a date range to chart yet.</div>
      </div>
    );
  }

  // #648: `skip_weekends` collapses Sat/Sun — every position is a COLUMN offset
  // (working days when on, calendar days when off) via columnOf, so the whole
  // gantt — axis, bars, drag, today — counts only working days.
  const skip = spec.skip_weekends ?? false;
  const minDate = rows.map((r) => r.span.start).reduce((m, s) => (s < m ? s : m));
  const maxDate = rows.map((r) => r.span.end).reduce((m, e) => (e > m ? e : m));
  const totalDays = columnOf(minDate, maxDate, skip) + 1;
  // Default density fits the whole project into the pane (fills the width with
  // no empty gap); once measured, the user's slider choice takes over. Fall back
  // to the week anchor before the pane is measured (first paint / SSR).
  const paneAvail = paneWidth - GUTTER;
  const fitPpd = paneAvail > 0 ? clampPpd(paneAvail / totalDays) : PPD_ANCHORS.week;
  const ppd = manualPpd ?? fitPpd;
  // Fill the pane when the project is short, scroll when it's long; the dated
  // grid then spans the whole canvas so there is no empty gap.
  const canvasWidth = canvasWidthFor(totalDays, ppd, paneAvail);
  const visibleDays = visibleDaysFor(canvasWidth, ppd);
  const xOf = (date: string) => columnOf(minDate, date, skip) * ppd;

  const lanes = groupLanes(rows, spec.group_by, type, refIndex, users);
  const grouped = Boolean(spec.group_by);

  // #GH-projects — drag a row's left label to reorder (writes the shared `rank`),
  // and across a swimlane to REGROUP: the drop moves the record into the lane it
  // landed in, exactly like dragging a board card into another column. Disabled
  // while a sort is active (sort takes over) or a write is busy.
  const manualReorder = !busy && !(spec.sort?.length ?? 0);
  const onRowDragEnd = (ev: DragEndEvent) => {
    const active = ev.active.id as number;
    const over = ev.over?.id as number | undefined;
    if (over == null) return;
    const drop = rowDropResult(
      rows.map((r) => r.e),
      spec.group_by,
      active,
      over,
    );
    if (drop) onPatch(drop.number, drop.patch);
  };

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
      if (days === 0) return;
      const next = applyDrag(row.span, mode, days, skip);
      // For a record the schedule owns, the bar's LENGTH is the duration it was
      // given and its POSITION is what the run worked out. So stretching the
      // right edge means "this takes longer", which survives the next run —
      // writing an end date here would just be overwritten by it. Moving the
      // bar still writes dates, and still does NOT touch the flag: a gesture
      // never decides that the schedule may no longer move your work.
      if (mode === "end" && sched && isAutoRow(row.e)) {
        onPatch(number, { [sched.duration]: barColumns(next, skip) });
        return;
      }
      onPatch(number, { [spanField]: spanValue(next) });
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    setDrag({ number, mode, days: 0 });
  };

  const previewSpan = (row: Row): Span =>
    drag && drag.number === row.e.number ? applyDrag(row.span, drag.mode, drag.days, skip) : row.span;

  // `today` also feeds the week axis's `by_today` cross-year boundary, so it is
  // computed before the axis. The clock is read here (the view shell) and
  // injected into the pure scale math — never read inside it.
  const axis = axisFor(minDate, visibleDays, ppd, spec.week, today, skip);

  const todayOffset = columnOf(minDate, today, skip);
  const todayInRange = todayOffset >= 0 && todayOffset < visibleDays;

  return (
    <DndContext sensors={sensors} onDragEnd={onRowDragEnd}>
      <div>
        {scheduleBar}
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
            onChange={(e) => setManualPpd(sliderToPpd(Number(e.target.value)))}
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
                onClick={() => setManualPpd(PPD_ANCHORS[z])}
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
                  <GutterRow key={row.e.number} number={row.e.number} enabled={manualReorder}>
                    {fieldText(row.e.fields[labelField]) || `#${row.e.number}`}
                  </GutterRow>
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
                  // Both ends of the range are coloured (barColumns) — the clamp
                  // this replaces was hiding the off-by-one: it made a same-day
                  // span look right while every longer bar stopped a day short.
                  const width = barColumns(ps, skip) * ppd;
                  return (
                    <div key={row.e.number} className="ev-gantt__bar-row" style={{ height: ROW_H }}>
                      <div
                        data-testid={`bar-${row.e.number}`}
                        title={spanValue(ps)}
                        className="ev-gantt__bar"
                        data-provisional={isProvisional(row.e) ? "true" : undefined}
                        data-busy={busy ? "1" : undefined}
                        onPointerDown={(e) => startDrag(row.e.number, "move", e)}
                        // #680 — a double-click opens the record. It coexists with
                        // the drag for two measured reasons (docs/plan-issue-680.md):
                        // startDrag's preventDefault suppresses only the
                        // compatibility mouse events, so click/dblclick still
                        // arrive; and a zero-day drag commits nothing, so the two
                        // presses underneath write no span.
                        onDoubleClick={() => onOpenRecord?.(row.e.number)}
                        style={{ left, width, background: barColor(row.e) }}
                      >
                        <span className="ev-gantt__bar-label">
                          {fieldText(row.e.fields[labelField]) || `#${row.e.number}`}
                        </span>
                        {assigneeField && assigneeDisplay !== "none" && (
                          <BarAssignee
                            number={row.e.number}
                            value={row.e.fields[assigneeField]}
                            users={users}
                            mode={assigneeDisplay}
                          />
                        )}
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
    </DndContext>
  );
}

/** A gantt gutter row: the left label doubles as a drag SOURCE + drop TARGET for
 * manual reorder (#GH-projects) — no grip, the label itself drags up/down. When
 * disabled (a sort is active / busy) it's an inert label. */
function GutterRow({ number, enabled, children }: { number: number; enabled: boolean; children: React.ReactNode }) {
  const { attributes, listeners, setNodeRef: setDrag } = useDraggable({ id: number, disabled: !enabled });
  const { setNodeRef: setDrop, isOver } = useDroppable({ id: number, disabled: !enabled });
  return (
    <div
      ref={(el) => {
        setDrag(el);
        setDrop(el);
      }}
      className="ev-gantt__row-label"
      style={{ height: ROW_H }}
      data-drag={enabled ? "" : undefined}
      data-over={enabled && isOver ? "" : undefined}
      {...(enabled ? attributes : {})}
      {...(enabled ? listeners : {})}
    >
      {children}
    </div>
  );
}

/** The assignee on a bar — "who is responsible" at a glance (§①). `mode` picks the
 * shape: `avatar` (round photo/initials, the shared `.ev-avatar` chrome) or `name`
 * (the full name as text). Callers skip it entirely for `none`. */
function BarAssignee({ number, value, users, mode }: { number: number; value: unknown; users?: User[]; mode: string }) {
  const id = fieldText(value);
  if (!id) return null;
  const u = users?.find((x) => x.id === id);
  const name = u?.name ?? id;
  if (mode === "name") {
    return (
      <span data-testid={`bar-${number}-assignee`} className="ev-gantt__bar-assignee-name" title={name}>
        {name}
      </span>
    );
  }
  const initials =
    (name || "?")
      .split(/[\s_-]+/)
      .filter(Boolean)
      .slice(0, 2)
      .map((s) => s[0]?.toUpperCase() ?? "")
      .join("") || "?";
  return (
    <span
      data-testid={`bar-${number}-assignee`}
      className="ev-avatar ev-gantt__bar-avatar"
      title={name}
      style={u?.photo_url ? { backgroundImage: `url(${u.photo_url})` } : undefined}
    >
      {u?.photo_url ? "" : initials}
    </span>
  );
}
