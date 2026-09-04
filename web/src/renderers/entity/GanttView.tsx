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
import { useEffect, useMemo, useRef, useState } from "react";

import type { EntityInstance } from "../../api/entities";
import type { User } from "../../api/types";
import { rowDropResult } from "./ganttOps";
import { type ScheduleReport, scheduleRows } from "./schedule";
import {
  applyDrag,
  axisFor,
  barColumns,
  canvasWidthFor,
  fitPpd,
  columnOf,
  columnPx,
  instantOf,
  deltaDays,
  grainFor,
  type Scale,
  type DragMode,
  PPD_ANCHORS,
  ppdToSlider,
  type Span,
  sliderToPpd,
  resolveSpan,
  type SpanSource,
  unionSpan,
  spanValue,
  visibleDaysFor,
  type Zoom,
} from "./ganttScale";
import { backrefBuckets, type RefIndex } from "./refTraversal";
import { fieldText, roleOf } from "./shared";
import { usePersistentSet } from "../../hooks/usePersistentSet";
import { actorPalette } from "./actorColor";
import { type ChipColor, selectColor } from "./selectColor";
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
/** The narrowest a bar is drawn, so a record whose work all falls in folded
 * time is still visibly there (#785). */
const HAIRLINE = 2;
const COARSE_H = 18; // top context band (month / year)
const FINE_H = 20; // fine tick row (weekdays / week codes / months)
/** What a second line under the fine row costs. Charged only when a tick
 * actually carries one — the axis is permanently in the way now that it sticks,
 * so it does not reserve room it is not using. */
const SUB_H = 11;
const LANE_H = 24;
const ROW_H = 26;
const ZOOMS: Zoom[] = ["day", "week", "month"];

type Row = { e: EntityInstance; span: Span; source: SpanSource; reach: Span };

/** Stands in for an absent ref index so the reach lookup has one shape. */
const NO_REFS: RefIndex = new Map();
type Lane = { key: string; label: string | null; rows: Row[] };
type Drag = { number: number; mode: DragMode; cols: number };

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
  viewKey,
}: EntityViewProps) {
  const spanField = spec.span ?? "span";
  const labelField = spec.label ?? "title";
  const assigneeField = spec.assignee;
  const assigneeDisplay = spec.assignee_display ?? "avatar";
  // #690 P2 — which field decides a bar's colour. Absent ⇒ bars keep the
  // single default colour they have always had, so no existing view changes
  // appearance the day this ships.
  // #690 P4 — collapsed lanes, per person and per view. In the browser rather
  // than the view file: the colour source is what the chart is ASKING, which
  // the project shares, and this is where one person is LOOKING.
  const collapsed = usePersistentSet(`gantt-collapsed:${viewKey ?? spec.entity}`);
  const colorField = spec.color_by;
  const colorSpec = colorField ? roleOf(type, colorField) : undefined;
  // Both halves of the palette entry or neither: `bg` is a translucent CHIP
  // fill, legible only under its paired `fg`. Handing the bar the fill alone
  // left it wearing the ink of the solid blue slab it used to be — cream on a
  // 93%-white fill, 1.07:1, invisible in light mode (#690). The pair travels
  // together now, guarded by ganttBarContrast.test.ts.
  // An ACTOR field is a directory, not a vocabulary: it has no fixed value list
  // to pin colours to and no ceiling on how many values it holds, so it gets a
  // GENERATED hue per person (`actorPalette`) instead of the six chip slots,
  // which four people already collide in. Seats come from the records in number
  // order — not the view's order — so re-sorting or regrouping the chart never
  // repaints anybody, and a newly assigned person takes the next free seat.
  const actorHues = useMemo(
    () =>
      colorField && colorSpec?.role === "actor"
        ? actorPalette(
            [...entities]
              .sort((a, b) => a.number - b.number)
              .map((e) => fieldText(e.fields[colorField]) ?? ""),
            colorSpec,
          )
        : undefined,
    [colorField, colorSpec, entities],
  );
  const barColor = (e: EntityInstance): ChipColor | undefined => {
    if (!colorField) return undefined;
    const value = fieldText(e.fields[colorField]) ?? "";
    // Anything else is a closed vocabulary — keep the palette the chips already
    // use. A second one would put one `status` value on two different colours in
    // two places on the same screen.
    return actorHues ? actorHues(value) : selectColor(value, colorSpec);
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
  // Every record in Timeline order, and every one of them gets a bar (#785).
  // The subset that could be DRAWN used to be smaller than this, and dropping
  // the rest did not read as "these have no dates yet" — it read as no such
  // work, so the issue nobody had scheduled was the one that vanished. That is
  // also why the scheduler always worked from `ordered`: a record with no dates
  // is exactly the one most in need of being given some.
  const ordered = sortRows(entities, spec.sort, type ?? null, refIndex, users);
  // #785 — what a record REACHES over: its own span, widened to contain the
  // spans of the records that point at it. A milestone's bar covers its issues,
  // because that is when the milestone actually happens.
  //
  // Drawn, never written. Writing the union back onto `milestone.span` would
  // make the milestone's own lower bound creep earlier every time one of its
  // issues moved earlier, and the next Recalculate would take the crept value
  // as the bound and schedule earlier still. Every step is just arithmetic on
  // the data, and the schedule drifts anyway — so the chart tells the truth
  // while the bound you set stays the bound you set.
  //
  // Only spans the records actually STATE count. An issue nobody has scheduled
  // is drawn as a week from today (P6), and letting the chart's own guess reach
  // into a milestone would move someone's roadmap on the strength of it.
  //
  // The pointing records are read through the view's own `span` field, which is
  // the only span name a view file names. Nothing here is in a position to know
  // that issues might keep their dates under a different key, and the schedule
  // block makes the same assumption about the same two types.
  //
  // Bucketed in ONE pass over the corpus, not scanned per row: a roadmap is
  // milestones × issues, so asking each milestone to filter every issue is
  // quadratic in the two numbers that both grow with the project.
  const index = refIndex ?? NO_REFS;
  const buckets = useMemo(() => backrefBuckets(type ?? null, index), [type, index]);
  const rows: Row[] = useMemo(
    () =>
      ordered.map((e) => {
        const resolved = resolveSpan(e.fields[spanField], today);
        const stated = (buckets.get(e.number) ?? [])
          .map((r) => resolveSpan(r.fields[spanField], today))
          .filter((r) => r.source === "given")
          .map((r) => r.span);
        return { e, ...resolved, reach: unionSpan([resolved.span, ...stated]) ?? resolved.span };
      }),
    [ordered, buckets, spanField, today],
  );

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

  // #648: `skip_weekends` collapses Sat/Sun — every position is a COLUMN offset
  // (working days when on, calendar days when off) via columnOf, so the whole
  // gantt — axis, bars, drag, today — counts only working days.
  const skip = spec.skip_weekends ?? false;
  // Compared as the instants the edges DENOTE. As text these agree with each
  // other on which DAY is furthest out (the date part is fixed-width), which is
  // all the chart used to need — but a whole-day "2026-01-05" runs to the next
  // midnight while "2026-01-05T17:00" stops at five, and once columns are hours
  // the difference is seven columns of canvas the bars still need.
  // Measured over the REACH, not the stated spans: a milestone bar drawn wider
  // than its record still has to fit on the canvas it is drawn on.
  //
  // Seeded from the first row rather than from `today`, and guarded for the
  // empty case: React forbids a hook after a conditional return, so the
  // "nothing to chart" branch has to come AFTER the axis memo below, and this
  // arithmetic has to survive being reached with no rows at all.
  const minDate = rows.length
    ? rows.map((r) => r.reach.start).reduce((m, s) => (instantOf(s, "start") < instantOf(m, "start") ? s : m))
    : today;
  const maxDate = rows.length
    ? rows.map((r) => r.reach.end).reduce((m, e) => (instantOf(e, "end") > instantOf(m, "end") ? e : m))
    : today;
  // #785: the same "time the chart does not draw" rule as `skip_weekends`, one
  // scale down. It has no effect at day grain — a day is one column however
  // many of its hours are worked — so it is carried on both scales and simply
  // does nothing until the columns are hours.
  const work = spec.work_hours;
  const dayScale: Scale = { grain: "day", skipWeekends: skip, work };
  const totalDays = barColumns({ start: minDate, end: maxDate }, dayScale);
  // Default density fits the whole project into the pane (fills the width with
  // no empty gap); once measured, the user's slider choice takes over. Fall back
  // to the week anchor before the pane is measured (first paint / SSR).
  const paneAvail = paneWidth - GUTTER;
  // Capped at PPD_MAX_FIT rather than the slider's own ceiling: the track now
  // runs on into hour columns (#785), and a short project must not be dropped
  // into that grain just because it fits.
  const fitted = paneAvail > 0 ? fitPpd(paneAvail, totalDays) : PPD_ANCHORS.week;
  const ppd = manualPpd ?? fitted;
  // Fill the pane when the project is short, scroll when it's long; the dated
  // grid then spans the whole canvas so there is no empty gap.
  // #785: the grain follows the slider — day columns until the density can
  // actually show an hour, then hour columns. `cpx` is what ONE column is worth
  // in px, and every position below multiplies by it. At day grain it IS `ppd`,
  // which is why nothing on screen moves when the switch happens under it.
  const grain = grainFor(ppd);
  const scale: Scale = { grain, skipWeekends: skip, work };
  const cpx = columnPx(ppd, grain);
  const totalColumns = barColumns({ start: minDate, end: maxDate }, scale);
  const canvasWidth = canvasWidthFor(totalColumns, cpx, paneAvail);
  const visibleDays = visibleDaysFor(canvasWidth, cpx);
  const xOf = (date: string) => columnOf(minDate, date, scale) * cpx;

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
    const dragCpx = cpx;
    const onMove = (ev: PointerEvent) =>
      setDrag({ number, mode, cols: deltaDays(ev.clientX - downX, dragCpx) });
    const onUp = (ev: PointerEvent) => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      setDrag(null);
      const cols = deltaDays(ev.clientX - downX, dragCpx);
      if (cols === 0) return;
      const next = applyDrag(row.span, mode, cols, scale);
      // For a record the schedule owns, the bar's LENGTH is the duration it was
      // given and its POSITION is what the run worked out. So stretching the
      // right edge means "this takes longer", which survives the next run —
      // writing an end date here would just be overwritten by it. Moving the
      // bar still writes dates, and still does NOT touch the flag: a gesture
      // never decides that the schedule may no longer move your work.
      if (mode === "end" && sched && isAutoRow(row.e)) {
        // In DAYS whatever the chart is drawing: `exp_days` is a number of
        // days, and measuring the stretched bar at hour grain would write 24
        // into it for a one-day task.
        //
        // At least one. A span lying entirely in folded time measures zero
        // columns, which is the right WIDTH for a bar and a meaningless
        // ESTIMATE — nobody can mean "this takes no days", and the record would
        // read "0 days" from then on. This floor is about what the number can
        // say, not about how wide to draw it; that is why it lives here and not
        // back inside `barColumns`.
        onPatch(number, { [sched.duration]: Math.max(1, barColumns(next, dayScale)) });
        return;
      }
      onPatch(number, { [spanField]: spanValue(next) });
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    setDrag({ number, mode, cols: 0 });
  };

  const previewSpan = (row: Row): Span =>
    drag && drag.number === row.e.number ? applyDrag(row.span, drag.mode, drag.cols, scale) : row.span;

  // `today` also feeds the week axis's `by_today` cross-year boundary, so it is
  // computed before the axis. The clock is read here (the view shell) and
  // injected into the pure scale math — never read inside it.
  // Memoised because a bar drag calls `setDrag` on every pointer move, and the
  // axis depends on none of what a drag changes. Building it walks the whole
  // visible range; at hour grain over a long project that is thousands of ticks
  // and bands, and doing it per pointer move is what makes dragging stutter.
  const axis = useMemo(
    () =>
      axisFor(
        minDate,
        visibleDays,
        ppd,
        spec.week,
        today,
        skip,
        { always_week: spec.always_week, weekday: spec.weekday, day_of_month: spec.day_of_month },
        work,
      ),
    [
      minDate,
      visibleDays,
      ppd,
      spec.week,
      today,
      skip,
      spec.always_week,
      spec.weekday,
      spec.day_of_month,
      work,
    ],
  );
  const fineH = FINE_H + (axis.fine.some((t) => t.sub) ? SUB_H : 0);
  const axisH = COARSE_H + fineH;

  const todayOffset = columnOf(minDate, today, scale);
  const todayInRange = todayOffset >= 0 && todayOffset < visibleDays;

  // Below every hook, deliberately. React counts hooks per render, so a return
  // above one makes the count depend on whether there is anything to chart —
  // and the render after a record appears throws instead of drawing.
  if (rows.length === 0) {
    return (
      <div>
        {scheduleBar}
        <div style={{ color: "var(--text-paper-d)" }}>No records to chart yet.</div>
      </div>
    );
  }

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
            <div style={{ height: axisH }} />
            {lanes.map((lane) => (
              <div key={lane.key}>
                {grouped && (
                  <button
                    type="button"
                    className="ev-gantt__lane-label"
                    title={lane.label ?? undefined}
                    style={{ height: LANE_H }}
                    aria-expanded={!collapsed.has(lane.key)}
                    onClick={() => collapsed.toggle(lane.key)}
                  >
                    {/* The arrow is its own node so the label stays one piece —
                        a caller looking the lane up by its text should not have
                        to know a chevron was put in front of it. */}
                    <span aria-hidden="true" className="ev-gantt__lane-caret">
                      {collapsed.has(lane.key) ? "\u25b8" : "\u25be"}
                    </span>
                    <span>{lane.label}</span>
                  </button>
                )}
                {(collapsed.has(lane.key) ? [] : lane.rows).map((row) => (
                  <GutterRow
                    key={row.e.number}
                    number={row.e.number}
                    enabled={manualReorder}
                    title={fieldText(row.e.fields[labelField]) || `#${row.e.number}`}
                  >
                    {fieldText(row.e.fields[labelField]) || `#${row.e.number}`}
                  </GutterRow>
                ))}
              </div>
            ))}
          </div>

          {/* right timeline: gridlines + axis ticks + today line + bars */}
          <div className="ev-gantt__canvas" style={{ width: canvasWidth }}>
            {axis.fine.map((t) => (
              <div key={`grid-${t.day}`} className="ev-gantt__gridline" style={{ left: t.day * cpx }} />
            ))}
            <div className="ev-gantt__axis" style={{ height: axisH }}>
              <div className="ev-gantt__axis-coarse" style={{ height: COARSE_H }}>
                {axis.coarse.map((b) => (
                  <span
                    key={`coarse-${b.day}`}
                    className="ev-gantt__coarse-band"
                    style={{ left: b.day * cpx, width: b.days * cpx }}
                  >
                    {b.label}
                  </span>
                ))}
              </div>
              <div className="ev-gantt__axis-fine" style={{ height: fineH }}>
                {axis.fine.map((t) => (
                  <span key={`fine-${t.day}`} className="ev-gantt__tick" style={{ left: t.day * cpx }} title={t.title}>
                    {t.label}
                    {t.sub && <span className="ev-gantt__tick-sub">{t.sub}</span>}
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
                {(collapsed.has(lane.key) ? [] : lane.rows).map((row) => {
                  // At rest the bar is the REACH, computed once per row.
                  // While dragging it is the record's OWN span — the thing the
                  // gesture is moving. Keeping the reach during a drag pins the
                  // left edge to the earliest record pointing at this one, so a
                  // move reads as a stretch, the bar does not follow the
                  // pointer, and the title shows a range that is not what gets
                  // saved; someone who drags again because "it didn't take"
                  // walks the stored dates further every time. The reach comes
                  // back on release.
                  const ps = previewSpan(row);
                  const drawn = drag?.number === row.e.number ? ps : row.reach;
                  const left = xOf(drawn.start);
                  // Both ends of the range are coloured (barColumns) — the clamp
                  // this replaces was hiding the off-by-one: it made a same-day
                  // span look right while every longer bar stopped a day short.
                  const columns = barColumns(drawn, scale);
                  // A bar with no working time in it — a Saturday-to-Sunday
                  // issue on a working-day chart, a job booked for the middle
                  // of the night — measures zero columns, and this is the only
                  // floor left. It is a floor in PIXELS, at the point of
                  // drawing, and it says "there is a record here"; the floor it
                  // replaces was in the COLUMN COUNT and said "this takes a
                  // day", which was not true. The record shows as a line
                  // pressed into the seam the folded time collapsed to.
                  const width = Math.max(columns * cpx, HAIRLINE);
                  // Dashed means "this is a guess" whichever guess it is — a
                  // length the scheduler had to invent, or dates the chart
                  // proposed because the record states none. One cue, because
                  // to a reader it is one fact.
                  const provisional = isProvisional(row.e) || row.source === "derived";
                  // A provisional row keeps its colour (#786). The hollow rule
                  // this used to defer to was written when a bar had no colour
                  // of its own, and deferring to it cost far more than it was
                  // worth: `exp_days` is optional and the Timeline ships a
                  // `schedule:` block, so an ordinary unsized issue is
                  // provisional — the COMMON case, which meant most of a real
                  // project painted as identical dashed outlines and the
                  // colouring was gone where it was most needed. #785 widens
                  // `provisional` again, to records the chart dated itself, so
                  // that reasoning now covers even more of the chart: the dashed
                  // edge already says "a guess", it says it just as clearly over
                  // a fill, and it is the only cue that has to survive.
                  const c = barColor(row.e);
                  return (
                    <div key={row.e.number} className="ev-gantt__bar-row" style={{ height: ROW_H }}>
                      <div
                        data-testid={`bar-${row.e.number}`}
                        title={spanValue(drawn)}
                        className="ev-gantt__bar"
                        data-provisional={provisional ? "true" : undefined}
                        data-empty={columns === 0 ? "true" : undefined}
                        data-busy={busy ? "1" : undefined}
                        onPointerDown={(e) => startDrag(row.e.number, "move", e)}
                        // #680 — a double-click opens the record. It coexists with
                        // the drag for two measured reasons (docs/plan-issue-680.md):
                        // startDrag's preventDefault suppresses only the
                        // compatibility mouse events, so click/dblclick still
                        // arrive; and a zero-day drag commits nothing, so the two
                        // presses underneath write no span.
                        onDoubleClick={() => onOpenRecord?.(row.e.number)}
                        // `--bar-ink` publishes the ink for furniture that
                        // touches the FILL rather than inheriting onto it: the
                        // avatar sets its own `color` for the initials, so
                        // `currentColor` inside it is the avatar's, not the
                        // bar's (P5). Pinning that furniture to one token
                        // instead only held while every bar was a dark slab.
                        style={
                          {
                            left,
                            width,
                            background: c?.bg,
                            color: c?.fg,
                            borderColor: c?.fg,
                            "--bar-ink": c?.fg,
                          } as React.CSSProperties
                        }
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
function GutterRow({
  number,
  enabled,
  title,
  children,
}: {
  number: number;
  enabled: boolean;
  /** The whole label. The column ellipsises, and without this the only way to
   * read a cut-off title was to open the record. */
  title?: string;
  children: React.ReactNode;
}) {
  const { attributes, listeners, setNodeRef: setDrag } = useDraggable({ id: number, disabled: !enabled });
  const { setNodeRef: setDrop, isOver } = useDroppable({ id: number, disabled: !enabled });
  return (
    <div
      ref={(el) => {
        setDrag(el);
        setDrop(el);
      }}
      className="ev-gantt__row-label"
      title={title}
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
