/**
 * gantt time-scale + drag math (#448 A2 / #450) — pure, unit-tested, so the
 * `GanttView` component stays a thin pointer-event shell. The timeline is a
 * fixed px-per-day scale (zoom picks the density); a pixel drag converts to a
 * whole number of days, and a bar drag rewrites the record's `daterange` value.
 * A span edge is one of two shapes, both UTC, both fixed-width in their date
 * part so a same-shape comparison is still chronological: `YYYY-MM-DD`, which
 * names a WHOLE day, and `YYYY-MM-DDTHH:mm`, which names one minute of one
 * (#785). The distinction is the point — a plain date has always been read
 * inclusively (7/13–7/15 is three days), and that reading is only expressible
 * if the value can still say "a day" rather than collapsing to midnight. Ask
 * {@link instantOf} what an edge MEANS at a given end; ask {@link dayOf} which
 * day it falls on. Comparing the two shapes as text does not work and is the
 * one thing to avoid here.
 *
 * Every position is a COLUMN offset, and a {@link Scale} says what a column is
 * worth: a day, or — once the slider is dense enough — an hour. The same Scale
 * carries the folding rule, and there is only one of those. A weekend and a
 * night are the same statement at two scales ("the chart does not draw this
 * time"), so they go through the same code: folded time costs no columns, and
 * whatever is left is what the chart is made of. Hour counts are built ON the
 * day count, which is what keeps the two grains agreeing and the switch between
 * them invisible.
 */

export type Zoom = "day" | "week" | "month";
export type DragMode = "move" | "start" | "end";
export type Span = { start: string; end: string };

const DAY_MS = 86_400_000;

/** The three named zoom stops, in px-per-day — labelled anchor points the slider
 * snaps to. They are NOT the ends of the track: it travels past `day` (zoom in
 * further, days grow wider) and past `month` (zoom out further, months compress).
 * So the anchors sit INSIDE [PPD_MIN, PPD_MAX]. */
export const PPD_ANCHORS: Record<Zoom, number> = { day: 28, week: 10, month: 3 };
export const PPD_MIN = 1; // most zoomed-out (further out than the `month` anchor)

/** How much room an hour column needs before hours are worth drawing at all.
 * Below this they are illegible and the chart is only a wider day view. */
const MIN_HOUR_COLUMN_PX = 6;

/** The density at which a column stops being a day and becomes an hour. Stated
 * in px-per-DAY like everything else on this scale, so it is directly
 * comparable with the anchors: 144 is a bit over five times the `day` anchor. */
export const PPD_HOUR_GRAIN = MIN_HOUR_COLUMN_PX * 24;

/** The densest the slider goes — 24px per hour column, which is roughly what
 * the `day` anchor gives a day. Raised from 56 (the old end of the track, which
 * is now {@link PPD_MAX_FIT}) so the track can reach hours at all (#785). */
export const PPD_MAX = 24 * 24;

/** The densest FIT-TO-PANE goes. Fitting a two-day project into a wide pane
 * lands at 450 px/day, well past {@link PPD_HOUR_GRAIN} — so without this the
 * chart would open in hours for no better reason than the project being short.
 * Going finer than days is a deliberate drag. This is the old `PPD_MAX`, so
 * fit-to-pane behaves exactly as it did before the track was extended. */
export const PPD_MAX_FIT = 56;

export function pxPerDay(zoom: Zoom): number {
  return PPD_ANCHORS[zoom];
}

/** Keep a (possibly slider- or drag-derived) px-per-day within the zoom range
 * the anchors define — never more zoomed-in than `day`, never more out than
 * `month`. */
export function clampPpd(ppd: number): number {
  return Math.min(PPD_MAX, Math.max(PPD_MIN, ppd));
}

/** Map a slider position in [0, 1] to px-per-day. Log-scaled — equal drags feel
 * like equal zoom multipliers — with the `month` anchor at 0 and `day` at 1.
 * Out-of-track positions clamp to the anchor densities. */
export function sliderToPpd(pos: number): number {
  const p = Math.min(1, Math.max(0, pos));
  return PPD_MIN * (PPD_MAX / PPD_MIN) ** p;
}

/** The inverse of {@link sliderToPpd}: the slider position [0, 1] that shows a
 * given px-per-day. */
export function ppdToSlider(ppd: number): number {
  return Math.log(clampPpd(ppd) / PPD_MIN) / Math.log(PPD_MAX / PPD_MIN);
}

/** Which grain the columns are at this density. The slider is the only thing
 * that decides it — there is no separate hour/day switch to get out of step
 * with the zoom, which is what "the granularity follows the slider" means. */
export function grainFor(ppd: number): Grain {
  return ppd >= PPD_HOUR_GRAIN ? "hour" : "day";
}

/** The width of ONE column at this density. A day column is the density
 * itself; an hour column is a twenty-fourth of it. That is what makes crossing
 * {@link PPD_HOUR_GRAIN} continuous — a day is 1 column of width `ppd` on one
 * side and 24 columns of width `ppd/24` on the other, so nothing on screen
 * moves when the grain changes under it. */
export function columnPx(ppd: number, grain: Grain): number {
  return grain === "hour" ? ppd / 24 : ppd;
}

/** The density that fits `columns` columns into the pane — never denser than
 * {@link PPD_MAX_FIT}, so opening a project can't land it in hours. */
export function fitPpd(paneAvail: number, columns: number): number {
  return Math.min(PPD_MAX_FIT, clampPpd(paneAvail / columns));
}

/** The chart canvas width: at least the pane it sits in (so a short project
 * fills the width instead of leaving a half-empty card), at least the content
 * it needs (so a long project scrolls). `max(pane, content)`. A `paneAvail` of
 * 0 (unmeasured, e.g. first paint / SSR) degrades to the natural content width. */
export function canvasWidthFor(dataDays: number, ppd: number, paneAvail: number): number {
  return Math.max(dataDays * ppd, paneAvail);
}

/** How many whole day-columns span a canvas of `width` at `ppd` px/day —
 * rounded up so the dated grid always reaches the canvas edge, never below 1.
 * When the canvas is wider than the data (a filled-to-pane short project) this
 * is how far past the data the axis keeps drawing dates. */
export function visibleDaysFor(width: number, ppd: number): number {
  return Math.max(1, Math.ceil(width / ppd));
}

function toISODate(ms: number): string {
  return new Date(ms).toISOString().slice(0, 10);
}

/** Add (or subtract) whole days to a date, in UTC. Takes the day out of an edge
 * that also names a clock, and always returns a bare `YYYY-MM-DD`. */
export function shiftDate(date: string, days: number): string {
  return toISODate(Date.parse(`${dayOf(date)}T00:00:00Z`) + days * DAY_MS);
}

/** Whole UTC days from `a` to `b` (negative if `b` precedes `a`).
 *
 * Counted between the two DAYS, not by dividing elapsed time: 01:00 to 23:00
 * the next day is one day apart, though only 46 hours passed, and 09:00 to
 * 20:00 the same day is zero though eleven hours did. Rounding the quotient
 * got both wrong the moment spans could carry a clock. */
export function daysBetween(a: string, b: string): number {
  const ms = Date.parse(`${dayOf(b)}T00:00:00Z`) - Date.parse(`${dayOf(a)}T00:00:00Z`);
  return Math.round(ms / DAY_MS);
}

/** A horizontal pixel delta → the nearest whole number of days at this
 * (continuous) px-per-day density. */
export function deltaDays(dx: number, ppd: number): number {
  return Math.round(dx / ppd);
}

/** Parse a `daterange` value (`"start/end"` string, `[start, end]`, or
 * `{start,end}` / `{from,to}`) into `YYYY-MM-DD` strings, or `null` for junk /
 * a reversed range.
 *
 * ONE end is enough. "Starts here, the end isn't settled yet" is a thing people
 * need to say — it is what a milestone whose end comes from its issues says —
 * and refusing to read it meant such a record simply had no bar, with nothing
 * on screen to explain the absence. A half-open range reads as the single day
 * it does know; what fills the other end is a scheduling decision, not a
 * parsing one. */
export function spanToDates(value: unknown): Span | null {
  const { start, end } = edgesOf(value);
  if (start === null && end === null) return null;
  const sa = start ?? (end as string);
  const sb = end ?? sa;
  // Compared as the instants they DENOTE, not as bytes: a plain end date runs
  // to the next midnight, so `09:30/the same date` is a morning's work, not a
  // reversed range. String order would have called it reversed and dropped it.
  if (instantOf(sb, "end") < instantOf(sa, "start")) return null;
  return { start: sa, end: sb };
}

/** The two canonical edges a `daterange` value carries, each `null` when it is
 * absent or unreadable. The ONE place the accepted shapes (`"a/b"`, `[a, b]`,
 * `{start,end}`, `{from,to}`) are taken apart — {@link spanToDates} folds a
 * missing edge onto the other one, {@link resolveSpan} computes a week from it,
 * and neither can disagree with the other about what was actually written. */
function edgesOf(value: unknown): { start: string | null; end: string | null } {
  let a: unknown;
  let b: unknown;
  if (typeof value === "string" && value.includes("/")) {
    [a, b] = value.split("/", 2);
  } else if (Array.isArray(value) && value.length === 2) {
    [a, b] = value;
  } else if (value && typeof value === "object") {
    const o = value as Record<string, unknown>;
    a = o.start ?? o.from;
    b = o.end ?? o.to;
  } else {
    return { start: null, end: null };
  }
  return { start: canonicalEdge(a), end: canonicalEdge(b) };
}

/** Whether a bar's dates are the record's own or the chart's suggestion. */
export type SpanSource = "given" | "derived";
export type ResolvedSpan = { span: Span; source: SpanSource };

/** How long the chart proposes a piece of work is, when nobody has said. Six,
 * because a plain end date is inclusive of its own day: start + 6 is a week. */
const PROPOSED_DAYS = 6;

/** The span to DRAW for a record, and whether the record actually said it.
 *
 * Every record gets a bar. Dropping the ones with no dates does not say "this
 * has no dates yet" — it says nothing at all, and an absent row reads as no
 * such work, which is how an unscheduled issue quietly stops existing. A
 * proposal can be seen, argued with, and dragged into place; `source` is what
 * lets the chart draw it as a proposal rather than pass it off as a decision.
 *
 * One stated end anchors the proposal (a week out from a start, a week back
 * from an end); with neither, the week starts today. A range that is back to
 * front is the same silence as no range at all — it cannot be drawn as written
 * — so it gets the same answer rather than vanishing, which is what it used
 * to do. */
export function resolveSpan(value: unknown, today: string): ResolvedSpan {
  const { start, end } = edgesOf(value);
  if (start !== null && end !== null) {
    const span = spanToDates(value);
    if (span) return { span, source: "given" };
  } else if (start !== null) {
    return { span: { start, end: withClockOf(start, PROPOSED_DAYS) }, source: "derived" };
  } else if (end !== null) {
    return { span: { start: withClockOf(end, -PROPOSED_DAYS), end }, source: "derived" };
  }
  return { span: { start: today, end: shiftDate(today, PROPOSED_DAYS) }, source: "derived" };
}

/** Shift an edge by whole days and put its clock back on — a proposal derived
 * from "starts 09:30 on the 1st" ends at 09:30, not at midnight. */
function withClockOf(edge: string, days: number): string {
  return shiftDate(edge, days) + edge.slice(10);
}

/** A span edge that names only a day — the form every span had before #785, and
 * still the form the chart writes back on a drag. */
const DATE_ONLY = /^\d{4}-\d{2}-\d{2}$/;

/** Whether a value already carries a UTC offset (`Z` or `±HH:MM`). */
const ZONED = /(?:Z|[+-]\d{2}:?\d{2})$/;

/** One span edge → its canonical text, or `null` if it is not a time at all.
 *
 * Canonical means: a day stays a bare `YYYY-MM-DD`, and a clock becomes
 * `YYYY-MM-DDTHH:mm` — minutes, which is the precision a span is specified to.
 * Keeping the two forms distinct is what lets a plain date go on meaning a
 * WHOLE day (see {@link instantOf}) instead of silently becoming midnight. */
function canonicalEdge(value: unknown): string | null {
  const raw = String(value).trim().replace(" ", "T");
  if (!raw) return null;
  const ms = parseUtc(raw);
  if (Number.isNaN(ms)) return null;
  return DATE_ONLY.test(raw)
    ? new Date(ms).toISOString().slice(0, 10)
    : new Date(ms).toISOString().slice(0, 16);
}

/** Parse a span edge as UTC. ES reads a zone-less DATE as UTC but a zone-less
 * DATE-TIME as LOCAL, which would put the two halves of one span in two
 * different calendars and make a late-evening bar jump a day for anyone east
 * of London. Every other date in this module is UTC; so is this. */
function parseUtc(raw: string): number {
  if (DATE_ONLY.test(raw)) return Date.parse(`${raw}T00:00:00Z`);
  return Date.parse(ZONED.test(raw) ? raw : `${raw}Z`);
}

/** The instant a span edge DENOTES, in epoch ms.
 *
 * A plain `YYYY-MM-DD` names a whole DAY, so it means different things at the
 * two ends — as a start, that day's first minute; as an end, the midnight that
 * closes it. That is the same inclusive reading the chart has always drawn
 * (7/13–7/15 is three days, not two), said in the value instead of patched on
 * afterwards with a `+1`. An edge that names a clock means exactly that minute
 * at either end: "09:30–17:00" is seven and a half hours to everyone who
 * writes it, so a timed end is where work stops, not the last minute of it.
 *
 * The end is therefore an EXCLUSIVE bound, which is what makes every width in
 * the chart a plain `end - start`. The last minute you can be inside a plain
 * date is still 23:59; that is the same statement, from the other side. */
export function instantOf(value: string, edge: "start" | "end"): number {
  const ms = parseUtc(value);
  if (edge === "start" || !DATE_ONLY.test(value)) return ms;
  return ms + DAY_MS; // up to, not through, the next midnight
}

/** An interval's exclusive upper bound written back as an edge string, so it
 * can go through the column machinery (which speaks edges, and which knows how
 * to walk a weekend) instead of being converted to milliseconds and back. */
function boundEdge(value: string): string {
  return DATE_ONLY.test(value) ? `${shiftDate(value, 1)}T00:00` : value;
}

/** The UTC calendar day an edge falls on. Every function that asks a question
 * about a DAY (which column, which weekday, which week) reads this first, so a
 * value carrying a clock lands on the day that clock is in rather than parsing
 * as garbage. */
export function dayOf(value: string): string {
  return value.slice(0, 10);
}

/** Apply a drag of `days` to a span: `move` shifts both ends (keeps duration);
 * `start` / `end` resize one edge, clamped so the range never inverts. With
 * `skip`, `days` is a count of WORKING days so the drag hops over weekends. */
export function applyDrag(span: Span, mode: DragMode, cols: number, scale: ScaleArg = false): Span {
  const s = toScale(scale);
  const hour = s.grain === "hour";
  // A drag moves the bar by whole COLUMNS — days at day grain, hours at hour
  // grain. At day grain the time of day rides along: every shift helper answers
  // in bare dates, so without re-attaching the clock one drag would flatten a
  // 09:30–17:00 bar into two whole days.
  const shiftStart = (d: string) =>
    hour ? dateAtColumn(d, cols, s) : shiftWorkingDays(d, cols, s) + d.slice(10);
  // The END edge is the one that catches people out. A start edge denotes
  // itself, but a plain end date denotes the midnight it runs UP TO — so it is
  // that bound which has to move. Shifting "2026-01-07" as though it were Jan 7
  // 00:00 lands on Jan 8 00:00, which is the instant it already denoted: the
  // bar would silently lose a day on every drag.
  const shiftEnd = (d: string) =>
    hour ? dateAtColumn(boundEdge(d), cols, s) : shiftWorkingDays(d, cols, s) + d.slice(10);
  // Ordering compares the edges AS WRITTEN (both as starts), which is what the
  // string comparison this replaces did — and unlike it, works between two
  // edges of different shapes.
  const before = (x: string, y: string) => instantOf(x, "start") < instantOf(y, "start");
  if (mode === "move") {
    return { start: shiftStart(span.start), end: shiftEnd(span.end) };
  }
  if (mode === "start") {
    const start = shiftStart(span.start);
    return { start: before(span.end, start) ? span.end : start, end: span.end };
  }
  const end = shiftEnd(span.end);
  return { start: span.start, end: before(end, span.start) ? span.start : end };
}

/** The canonical stored form of a span (matches the table daterange picker). */
export function spanValue(span: Span): string {
  return `${span.start}/${span.end}`;
}

// ── two-tier axis (#448 responsive redesign) ───────────────────────────────
// A coarse context band (months, or years when zoomed way out) over a fine
// tick row (day numbers → week starts → month names), the fine row THINNED so
// two labels can never collide at any density — the cure for the day-zoom
// "MM-DD every 28px" overlap. All positions are day-offsets from minDate; the
// view multiplies by px-per-day.

/** One label on the fine row. `sub` is a second, smaller line under it (the day
 * of the month, when the view asks for it); `title` is what hovering shows. Both
 * are absent unless a setting turns them on — an axis row costs vertical space
 * permanently, and the sticky header already spends some. */
export type FineTick = { day: number; label: string; sub?: string; title?: string };
export type CoarseBand = { day: number; days: number; label: string };
/** What the fine row is counting. A superset of {@link Zoom}: the slider's
 * labelled stops are still day / week / month, but the track now runs on past
 * the last of them into hours (#785), and the axis has to be able to say so. */
export type AxisUnit = Zoom | "hour";
export type Axis = { unit: AxisUnit; fine: FineTick[]; coarse: CoarseBand[] };

/** Horizontal room (px) reserved per fine-tier label. A fine step is only
 * chosen if `stepDays * ppd` clears this, so labels never touch. */
export const AXIS_MIN_LABEL_PX = 36;

/** How the days of the week are written. Digits are the default because that is
 * how the user's shop floor writes them; the names are there for everyone else. */
export type WeekdayFormat = "number" | "short";

/** Whether the day of the month rides along under the weekday: not at all, as a
 * second line, or only when you hover. */
export type DayOfMonth = "hidden" | "always" | "hover";

/** Per-view axis settings, read straight off the view file. Every one has a
 * default, so an axis built without them is the one the platform ships. */
export type AxisOptions = {
  always_week?: boolean;
  weekday?: WeekdayFormat;
  day_of_month?: DayOfMonth;
};

/** Room ONE weekday label needs. A digit is far narrower than the `MM-DD` the
 * fine row used to carry, which is what makes a label-per-day affordable at all;
 * spelling the day out costs more, so that choice moves the density at which the
 * row appears rather than letting names overprint. */
export const AXIS_WEEKDAY_PX: Record<WeekdayFormat, number> = { number: 16, short: 26 };

/** Room the day-of-month line needs under it — two digits, so wider than one. */
export const AXIS_DAY_OF_MONTH_PX = 20;

/** The narrowest column this axis will label per day, given what the view asked
 * to see in it. Below this the axis is a week axis instead. */
function weekdayTickPx(opts: AxisOptions): number {
  const dayOfMonth = (opts.day_of_month ?? "hidden") === "always" ? AXIS_DAY_OF_MONTH_PX : 0;
  return Math.max(AXIS_WEEKDAY_PX[opts.weekday ?? "number"], dayOfMonth);
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
/** ppd at/above which the axis shows within-month detail (days/weeks) rather
 * than a month overview. */
const DETAIL_PPD = 5;

function ymd(date: string): { y: number; m: number; d: number } {
  const t = new Date(`${date}T00:00:00Z`);
  return { y: t.getUTCFullYear(), m: t.getUTCMonth(), d: t.getUTCDate() };
}

function firstOfMonth(y: number, m: number): string {
  return `${y}-${String(m + 1).padStart(2, "0")}-01`;
}

/** Where a band ends: clipped to the window, and ALWAYS past where it started.
 *
 * The clamp is a backstop, not the fix — a band that does not advance means the
 * column arithmetic has contradicted itself, and the loop that walks these
 * bands would spin until the tab dies. One wrong label is a defect; a frozen
 * browser is not something to leave reachable from bad arithmetic. The
 * arithmetic itself is fixed in {@link dateAtColumn}. */
function advance(cursor: number, end: number, visibleDays: number): number {
  return Math.max(cursor + 1, Math.min(end, visibleDays));
}

/** Calendar-month bands clipped to [0, visibleDays), in COLUMN offsets from
 * minDate (working-day columns when `skip`, else calendar days). A band opening
 * before minDate is clamped to column 0. */
function monthBands(minDate: string, visibleDays: number, skip: boolean): CoarseBand[] {
  const bands: CoarseBand[] = [];
  let cursor = 0;
  while (cursor < visibleDays) {
    const { y, m } = ymd(dateAtColumn(minDate, cursor, skip));
    const nextStart = firstOfMonth(m === 11 ? y + 1 : y, m === 11 ? 0 : m + 1);
    const bandEnd = advance(cursor, columnOf(minDate, nextStart, skip), visibleDays);
    bands.push({ day: cursor, days: bandEnd - cursor, label: `${MONTHS[m]} ${y}` });
    cursor = bandEnd;
  }
  return bands;
}

/** Calendar-year bands clipped to [0, visibleDays), in COLUMN offsets from minDate. */
function yearBands(minDate: string, visibleDays: number, skip: boolean): CoarseBand[] {
  const bands: CoarseBand[] = [];
  let cursor = 0;
  while (cursor < visibleDays) {
    const { y } = ymd(dateAtColumn(minDate, cursor, skip));
    const bandEnd = advance(cursor, columnOf(minDate, `${y + 1}-01-01`, skip), visibleDays);
    bands.push({ day: cursor, days: bandEnd - cursor, label: String(y) });
    cursor = bandEnd;
  }
  return bands;
}

/** Fine ticks at calendar-month starts, thinned so labels fit at this density. */
function monthTicks(minDate: string, visibleDays: number, ppd: number, skip: boolean): FineTick[] {
  const monthCols = skip ? 22 : 30; // a month is ~22 working days when skipping weekends
  const step = [1, 2, 3, 6, 12].find((s) => s * monthCols * ppd >= AXIS_MIN_LABEL_PX) ?? 12;
  const ticks: FineTick[] = [];
  let { y, m } = ymd(minDate);
  if (ymd(minDate).d !== 1) {
    // the first WHOLE month starts next month
    if (m === 11) {
      y += 1;
      m = 0;
    } else {
      m += 1;
    }
  }
  for (let count = 0; ; count += 1) {
    const day = columnOf(minDate, firstOfMonth(y, m), skip);
    if (day >= visibleDays) break;
    if (day >= 0 && count % step === 0) ticks.push({ day, label: MONTHS[m] });
    if (m === 11) {
      y += 1;
      m = 0;
    } else {
      m += 1;
    }
  }
  return ticks;
}

/** Build the two-tier axis for a visible window of `visibleDays` from `minDate`
 * at `ppd` px/day. Zoomed in → day/week detail over month bands; zoomed out →
 * month labels over year bands. The fine row is always thinned to fit.
 *
 * When a `week` rule is supplied, the detail-zone fine row shows CUSTOM WEEK
 * CODES at week starts (e.g. `W627`) instead of day numbers — `today` feeds the
 * `by_today` cross-year boundary. Zoomed all the way out (month zone) still
 * shows months, since a week code per column would be far too dense there. */
/** The fine row at hour grain: `HH`, thinned to a step that keeps two labels
 * from touching — the same rule the day row has had since #448.
 *
 * Snapped to the whole hour. The chart's left edge is a record's START, which
 * is as likely to be 09:30 as midnight, and an hour axis labelled at :30 past
 * every hour reads as broken rather than as precise. */
function hourTicks(minDate: string, visibleColumns: number, ppd: number, scale: Scale): FineTick[] {
  const px = columnPx(ppd, "hour");
  const step = [1, 2, 3, 6, 12, 24].find((s) => s * px >= AXIS_MIN_LABEL_PX) ?? 24;
  // Snapped from the CLOCK at column zero rather than from the column offset,
  // so it lands on a whole hour whatever the working window starts at.
  const clock0 = clockOf(dateAtColumn(minDate, 0, scale));
  const ticks: FineTick[] = [];
  for (let col = Math.ceil(clock0) - clock0; col < visibleColumns; col += step) {
    const at = dateAtColumn(minDate, col, scale);
    ticks.push({ day: col, label: at.slice(11, 13), title: `${dayOf(at)} ${at.slice(11, 16)}` });
  }
  return ticks;
}

/** The context band at hour grain: one per DAY, 24 columns wide, except a first
 * band the chart starts partway into.
 *
 * Advances by the width it just emitted rather than by a fixed day, so a
 * weekend the columns skip is skipped here too — and it stops on a band that
 * would be zero wide. `monthBands` once looped forever on exactly that (#690,
 * a weekend origin), and this walks the same kind of ground. */
function dayBands(minDate: string, visibleColumns: number, scale: Scale): CoarseBand[] {
  const bands: CoarseBand[] = [];
  let col = 0;
  while (col < visibleColumns) {
    const at = dateAtColumn(minDate, col, scale);
    const width = Math.min(hoursPerDay(scale.work) - hoursIntoDay(at, scale.work), visibleColumns - col);
    if (width <= 0) break;
    bands.push({ day: col, days: width, label: dayBandLabel(dayOf(at)) });
    col += width;
  }
  return bands;
}

/** `Mon 5 Jan` — the weekday because an hour axis is read for planning a day's
 * work, the date because two Mondays look alike. */
function dayBandLabel(day: string): string {
  const { m, d } = ymd(day);
  return `${SHORT_DAYS[weekdayOf(day)]} ${d} ${MONTHS[m]}`;
}

export function axisFor(
  minDate: string,
  visibleDays: number,
  ppd: number,
  week?: WeekRule,
  today = "",
  skip = false,
  opts: AxisOptions = {},
  work?: WorkHours,
): Axis {
  // Densest of all (#785): the columns are hours, so the fine row says WHICH
  // hour and the band above names the day. Decided from `ppd` alone rather than
  // from a parameter — the grain follows the slider, and a second way to ask
  // for hours is a second thing to fall out of step with the zoom.
  if (grainFor(ppd) === "hour") {
    const scale: Scale = { grain: "hour", skipWeekends: skip, work };
    return {
      unit: "hour",
      fine: hourTicks(minDate, visibleDays, ppd, scale),
      coarse: dayBands(minDate, visibleDays, scale),
    };
  }
  // Densest: every column is one day, so the fine row can say WHICH day of the
  // week it is and the band above it can name the week. Needs a week rule —
  // without one there is no code to head the band with, and the axis stays the
  // month-banded one it has always been.
  if (week && ppd >= weekdayTickPx(opts)) {
    return {
      unit: "day",
      fine: weekdayTicks(minDate, visibleDays, week, skip, opts),
      coarse: weekBands(minDate, visibleDays, week, today, skip),
    };
  }
  if (ppd >= DETAIL_PPD) {
    const step = [1, 2, 5, 7, 14].find((s) => s * ppd >= AXIS_MIN_LABEL_PX) ?? 14;
    // The middle tier. With a week rule this is ALWAYS week codes: the tier
    // above already answers "which day", so a row of day-of-month numbers here
    // would only repeat it more thinly and in a different unit. Which tier you
    // are in is decided by `weekdayTickPx` above, not by this step — the step
    // is what keeps the codes from touching. Without a week rule there is no
    // code to show and the row stays day numbers, as it always was.
    if (week) {
      return { unit: "week", fine: weekTicks(minDate, visibleDays, ppd, week, today, skip), coarse: monthBands(minDate, visibleDays, skip) };
    }
    const fine: FineTick[] = [];
    let d = minDate; // date at column 0
    for (let col = 0; col < visibleDays; col += step) {
      fine.push({ day: col, label: String(ymd(d).d) });
      if (col + step < visibleDays) d = shiftWorkingDays(d, step, skip);
    }
    return { unit: step >= 7 ? "week" : "day", fine, coarse: monthBands(minDate, visibleDays, skip) };
  }
  // Sparsest. Months over years, unless the view says the week is what it wants
  // to read — then the week codes stay and simply thin out further. This is the
  // ONLY end the setting has anything to say about: the two denser tiers are
  // showing weeks already.
  if (week && opts.always_week) {
    return {
      unit: "week",
      fine: weekTicks(minDate, visibleDays, ppd, week, today, skip),
      coarse: yearBands(minDate, visibleDays, skip),
    };
  }
  return { unit: "month", fine: monthTicks(minDate, visibleDays, ppd, skip), coarse: yearBands(minDate, visibleDays, skip) };
}

// ── custom week numbering (非 ISO) ──────────────────────────────────────────
// A configurable "week code" scheme (e.g. manufacturing work-weeks) driven by a
// per-view `week:` rule, NOT hardcoded to ISO-8601. All math is UTC + pure so it
// unit-tests cleanly; the one clock input (`today`, for the `by_today` boundary)
// is passed in, never read here.

export type Weekday = "monday" | "tuesday" | "wednesday" | "thursday" | "friday" | "saturday" | "sunday";

const WEEKDAY_INDEX: Record<Weekday, number> = {
  sunday: 0,
  monday: 1,
  tuesday: 2,
  wednesday: 3,
  thursday: 4,
  friday: 5,
  saturday: 6,
};

/** The UTC day-of-week of a `YYYY-MM-DD` date, 0 = Sunday … 6 = Saturday. */
function weekdayOf(date: string): number {
  return new Date(`${dayOf(date)}T00:00:00Z`).getUTCDay();
}

/** The first day of the week `date` falls in, for a week that begins on `start`
 * (default Monday). Returned as `YYYY-MM-DD`. */
export function weekStart(date: string, start: Weekday = "monday"): string {
  const offset = (weekdayOf(date) - WEEKDAY_INDEX[start] + 7) % 7;
  return shiftDate(date, -offset);
}

// ── working-day (skip-weekends) columns ─────────────────────────────────────
// A "column" is a unit of horizontal gantt space. With skip-weekends on, one
// column = one WORKING day (Mon–Fri) and weekends collapse to zero width; off,
// one column = one calendar day (so every fn below is a no-op passthrough then).

/** What one gantt column is worth. `day` is what the chart has always drawn;
 * `hour` is the same timeline read closer, and the slider picks between them
 * (see {@link grainFor}). */
export type Grain = "day" | "hour";

/** The column rule: how wide a column is, and which time gets none.
 *
 * These two settings are one mechanism at two scales — a skipped weekend and a
 * skipped night are both "time the chart does not draw" — so they are decided
 * together, here, rather than by each caller. */
export type Scale = { grain: Grain; skipWeekends: boolean; work?: WorkHours };

/** The part of a day the chart draws, in hours past midnight — `{from: 7, to:
 * 21}` is a 07:00–21:00 working day. Absent means all twenty-four.
 *
 * This is the weekend rule at a finer scale and it goes through the same code:
 * folded time takes no width, so an overnight gap is as wide as a weekend is,
 * which is not at all. At day grain it changes nothing, because a day is one
 * column however many of its hours are worked. */
export type WorkHours = { from: number; to: number };

/** How many columns a whole day is worth at hour grain. */
function hoursPerDay(work?: WorkHours): number {
  return work ? work.to - work.from : HOURS_PER_DAY;
}

/** A {@link Scale}, or the bare `skip` boolean that meant "day columns, with or
 * without weekends" before columns could be finer. Shorthand for the same one
 * rule, normalised at the door by {@link toScale} — not a second rule. */
export type ScaleArg = boolean | Scale;

export function toScale(scale: ScaleArg): Scale {
  return typeof scale === "boolean" ? { grain: "day", skipWeekends: scale } : scale;
}

const HOURS_PER_DAY = 24;
const HOUR_MS = 3_600_000;

/** How far into its day an edge sits, in hours — `0` for a bare date, `9.5` for
 * `T09:30`. Fractional on purpose: spans are specified to the minute and the
 * CHART to the hour, so a half hour is half a column, not a rounding error. */
function clockOf(edge: string): number {
  if (edge.length <= 10) return 0;
  return Number(edge.slice(11, 13)) + Number(edge.slice(14, 16)) / 60;
}

/** How many COLUMNS into its day an edge sits — the clock, less any part of it
 * the working window folds away. Before the window opens that is zero; after it
 * closes it is the whole day's worth, so an evening and the following small
 * hours sit on the same column and the night between two working days costs
 * nothing. */
function hoursIntoDay(edge: string, work?: WorkHours): number {
  const clock = clockOf(edge);
  if (!work) return clock;
  return Math.min(Math.max(clock, work.from), work.to) - work.from;
}

/** An edge reduced to the day that holds its column, plus how far into that day
 * it sits. A weekend edge has no column of its own, so it reports the first
 * hour of the next working day — the same collapse the day grain performs, and
 * the reason the two grains agree instead of drifting a column apart (the
 * disagreement that froze a tab in #690). */
function workingOrigin(edge: string, s: Scale): { day: string; hours: number } {
  let day = dayOf(edge);
  if (s.skipWeekends && isWeekend(day)) {
    while (isWeekend(day)) day = shiftDate(day, 1);
    return { day, hours: 0 };
  }
  return { day, hours: hoursIntoDay(edge, s.work) };
}

/** {@link columnOf} at hour grain: whole days still cost what they cost at day
 * grain (so a day is exactly 24 columns and a skipped weekend still nothing),
 * and the clocks at either end add the part-days. Keeping it built ON the day
 * count is what makes crossing the grain threshold continuous — the same date
 * sits at the same place before and after. */
function hourColumns(from: string, date: string, s: Scale): number {
  const a = workingOrigin(from, s);
  const b = workingOrigin(date, s);
  const key = (o: { day: string; hours: number }) =>
    Date.parse(`${o.day}T00:00:00Z`) + o.hours * HOUR_MS;
  const sign = key(b) >= key(a) ? 1 : -1;
  const lo = sign > 0 ? a : b;
  const hi = sign > 0 ? b : a;
  const days = columnOf(lo.day, hi.day, { grain: "day", skipWeekends: s.skipWeekends });
  return sign * (days * hoursPerDay(s.work) + hi.hours - lo.hours);
}

/** Whether `date` is a Saturday or Sunday. */
export function isWeekend(date: string): boolean {
  const d = weekdayOf(date);
  return d === 0 || d === 6;
}

/** The column index of `date` relative to `from`: working days when `skip`, else
 * calendar days. Signed; a weekend date collapses onto the following working
 * column (so Fri, Sat, Sun, next-Mon are cols n, n+1, n+1, n+1). */
export function columnOf(from: string, date: string, scale: ScaleArg): number {
  const s = toScale(scale);
  if (s.grain === "hour") return hourColumns(from, date, s);
  // Reduced to bare days FIRST. Both the ordering below and the week walk are
  // string comparisons, and those are only sound between two edges of the same
  // shape — `"2026-01-05"` sorts before `"2026-01-05T00:00"` though they name
  // the same moment.
  const skip = s.skipWeekends;
  const a = dayOf(from);
  const b = dayOf(date);
  if (!skip) return daysBetween(a, b);
  if (b === a) return 0;
  const sign = b > a ? 1 : -1;
  const lo = sign > 0 ? a : b;
  const total = daysBetween(lo, sign > 0 ? b : a); // ≥ 0
  const fullWeeks = Math.floor(total / 7);
  let wd = fullWeeks * 5;
  const dow = weekdayOf(lo);
  for (let i = fullWeeks * 7; i < total; i++) {
    const w = (dow + i) % 7;
    if (w !== 0 && w !== 6) wd++;
  }
  return sign * wd;
}

/** How many columns a span COVERS: the distance from where it starts to where
 * it ends, counted in columns the chart actually draws.
 *
 * The inclusive reading survives without a `+1` — a plain end date runs to the
 * next midnight, so 7/13–7/15 measures three days on its own. Removing that
 * `+1` also removes what it was hiding. It carried a "never below 1" floor, and
 * a task lying entirely in folded time — a Saturday-to-Sunday issue on a
 * working-day chart, a job booked at 22:00 on a 07:00–21:00 one — measures
 * ZERO columns. The floor stretched it to exactly the width of a full working
 * day, so the chart said a weekend task takes as long as a Monday one. It
 * doesn't; it now measures nothing, and the view draws that nothing as a line
 * so the record is still visibly there (#785 §1.3). */
export function barColumns(span: Span, scale: ScaleArg): number {
  const s = toScale(scale);
  // At day grain a span occupies a whole day column even when it only uses part
  // of one — the column IS the unit there, and an eight-hour task still happens
  // on that day. Only the finer grain can say how much of it.
  const end = s.grain === "hour" ? span.end : dayOf(span.end);
  return columnOf(span.start, boundEdge(end), s);
}

/** The calendar date at working-day column `col` from `minDate` (inverse of
 * {@link columnOf}); plain `shiftDate` when not skipping. */
export function dateAtColumn(minDate: string, col: number, scale: ScaleArg): string {
  const s = toScale(scale);
  if (s.grain === "hour") return instantAtHourColumn(minDate, col, s);
  const skip = s.skipWeekends;
  if (!skip) return shiftDate(minDate, col);
  const dir = col >= 0 ? 1 : -1;
  let remaining = Math.abs(col);
  // A weekend origin is NOT a column of its own: `columnOf` already puts a
  // Saturday, a Sunday and the following Monday on the same column, so the
  // date AT that column has to be the Monday. Counting from the Saturday
  // instead made the two functions disagree by one, which `monthBands` turned
  // into a zero-width band and an endless loop — a frozen tab for any project
  // whose earliest date falls on a weekend. Normalising here changes nothing
  // `columnOf` reports (weekend days contribute no working days, so measuring
  // from the Saturday or from the Monday gives the same answer for every date
  // at or after it) — it only makes this function its true inverse.
  let d = dayOf(minDate);
  while (isWeekend(d)) d = shiftDate(d, 1);
  while (remaining > 0) {
    d = shiftDate(d, dir);
    if (!isWeekend(d)) remaining--;
  }
  return d;
}

/** {@link dateAtColumn} at hour grain — the inverse of {@link hourColumns}, and
 * built the same way round: reduce to whole days at the DAY grain (so the
 * weekend walk is the one that already works) and put the leftover hours back
 * on. Counted in whole minutes rather than fractional hours so an origin at
 * `T09:20` cannot accumulate a float error into a 19-minute answer. */
function instantAtHourColumn(minDate: string, col: number, s: Scale): string {
  const o = workingOrigin(minDate, s);
  const perDay = hoursPerDay(s.work) * 60;
  const total = Math.round((o.hours + col) * 60);
  const dayDelta = Math.floor(total / perDay);
  const inDay = total - dayDelta * perDay + (s.work?.from ?? 0) * 60;
  const day = dateAtColumn(o.day, dayDelta, { grain: "day", skipWeekends: s.skipWeekends });
  const hh = String(Math.floor(inDay / 60)).padStart(2, "0");
  const mm = String(Math.round(inDay % 60)).padStart(2, "0");
  return `${day}T${hh}:${mm}`;
}

/** Add (or subtract) `n` WORKING days to a date, hopping over weekends; plain
 * `shiftDate` when not skipping. */
export function shiftWorkingDays(date: string, n: number, scale: ScaleArg): string {
  const s = toScale(scale);
  if (s.grain === "day" && !s.skipWeekends) return shiftDate(date, n);
  return dateAtColumn(date, n, s);
}

/** How a year's first week is anchored. `jan1` = the week containing Jan 1;
 * `iso` = the ISO-8601 week (the one holding the year's first Thursday / Jan 4);
 * `first_full` = the first week lying wholly inside the new year. */
export type WeekAnchor = "jan1" | "iso" | "first_full";

/** What to do with the cross-year week that holds BOTH Dec 31 and Jan 1 — it can
 * be read as the old year's last week OR the new year's W01. `new_year` anchors
 * every year at its Jan-1 week, so that week is the new year's W01 (the old year
 * stops the week before); `old_year` anchors every year at its first FULL week,
 * so that week is the old year's last; `by_today` picks per crossing by comparing
 * `today` to that Jan 1 (before → old year's last, on/after → new year's W01). */
export type WeekBoundary = "new_year" | "old_year" | "by_today";

/** A per-view week-numbering rule. Every field has a default so a bare `{}` is
 * a valid (ISO-ish `jan1`) rule; the FE reads it verbatim off the view file. */
export type WeekRule = {
  start?: Weekday;
  first_week?: WeekAnchor;
  reset?: "yearly" | "none";
  boundary?: WeekBoundary;
  epoch?: string;
  label?: string;
};

export type WeekNumber = { year: number; week: number };

/** The `YYYY-MM-DD` on which year `y`'s W01 begins, per the STATIC anchor rules
 * (`by_today` is resolved in {@link weekNumberOf}, not here). This is where the
 * anchor / `new_year` vs `old_year` choice lives; numbering is then a plain week
 * count from here. */
function yearW01Start(y: number, rule: WeekRule): string {
  const start = rule.start ?? "monday";
  const anchor = rule.first_week ?? "jan1";
  // ISO carries its own boundary rule (the first-Thursday week), so the `jan1`
  // boundary knobs don't apply to it.
  if (anchor === "iso") return weekStart(`${y}-01-04`, start);
  const jan1 = `${y}-01-01`;
  const jan1Week = weekStart(jan1, start); // the week holding Jan 1
  const firstFull = jan1Week === jan1 ? jan1Week : shiftDate(jan1Week, 7);
  if (anchor === "first_full") return firstFull;
  // anchor === "jan1": new_year keeps the Jan-1 week as W01; old_year gives it to
  // the previous year, so this year opens at its first full week.
  return (rule.boundary ?? "new_year") === "old_year" ? firstFull : jan1Week;
}

/** The (year, week) a date carries under a custom week rule. `today` only
 * matters for `boundary: by_today` (the cross-year week); every other date is
 * clock-independent. */
export function weekNumberOf(date: string, rule: WeekRule, today: string): WeekNumber {
  const start = rule.start ?? "monday";
  const ws = weekStart(date, start);
  if ((rule.reset ?? "yearly") === "none") {
    const epoch = weekStart(rule.epoch ?? "1970-01-01", start);
    return { year: ymd(date).y, week: Math.floor(daysBetween(epoch, ws) / 7) + 1 };
  }
  // `by_today` numbers off the plain jan1 calendar and RELABELS only the
  // cross-year week (below) — it must NOT shift the anchor, or every week after it
  // would renumber (that pushed W01 a week late — #648 review).
  const boundary = rule.boundary ?? "new_year";
  const base: WeekRule = boundary === "by_today" ? { ...rule, boundary: "new_year" } : rule;
  const g = ymd(date).y;
  const weekIn = (y: number) => ({ year: y, week: Math.round(daysBetween(yearW01Start(y, base), ws) / 7) + 1 });
  // The date's week belongs to the latest year whose W01 has already started;
  // the earliest candidate (g−1) always qualifies, so it is the default.
  const n = ws >= yearW01Start(g + 1, base) ? weekIn(g + 1) : ws >= yearW01Start(g, base) ? weekIn(g) : weekIn(g - 1);
  // The cross-year week is `n.year`'s W01 yet starts in the previous December; if
  // its New Year hasn't arrived (today before it), show it as the OLD year's last
  // week instead. Weeks after it keep `n.year`'s numbering either way.
  if (boundary === "by_today" && n.week === 1) {
    const crossing = `${n.year}-01-01`;
    if (ws < crossing && today < crossing) {
      return { year: n.year - 1, week: Math.round(daysBetween(yearW01Start(n.year - 1, base), ws) / 7) + 1 };
    }
  }
  return n;
}

/** Render a {@link WeekNumber} through a token template. Tokens: `{yyyy}` full
 * year, `{yy}` last two year digits, `{y1}` last one, `{ww}` zero-padded week,
 * `{w}` bare week. Defaults to `{yyyy}-W{ww}`. */
export function formatWeekLabel(n: WeekNumber, template = "{yyyy}-W{ww}"): string {
  const yyyy = String(n.year);
  return template
    .replace(/\{yyyy\}/g, yyyy)
    .replace(/\{yy\}/g, yyyy.slice(-2))
    .replace(/\{y1\}/g, yyyy.slice(-1))
    .replace(/\{ww\}/g, String(n.week).padStart(2, "0"))
    .replace(/\{w\}/g, String(n.week));
}

/** The custom week-code label a date carries — the one string the axis renders.
 * Convenience over {@link weekNumberOf} + {@link formatWeekLabel}. */
export function weekLabelOf(date: string, rule: WeekRule, today: string): string {
  return formatWeekLabel(weekNumberOf(date, rule, today), rule.label);
}

const SHORT_DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

/** Which day of its week a date is, 1-based, counted from the day the week rule
 * starts on. Monday-start weeks read 1–7 Mon→Sun; with weekends skipped only
 * 1–5 ever reach the axis, which is the `1 2 3 4 5` the user asked for. */
function weekdayNumber(date: string, start: Weekday): number {
  return ((weekdayOf(date) - WEEKDAY_INDEX[start] + 7) % 7) + 1;
}

/** One fine tick per column, saying which day of the week that column is. No
 * thinning: this tier is only chosen when every column has room for a label, so
 * skipping some would leave gaps for no reason. */
function weekdayTicks(minDate: string, visibleDays: number, week: WeekRule, skip: boolean, opts: AxisOptions): FineTick[] {
  const start = week.start ?? "monday";
  const format = opts.weekday ?? "number";
  const dayOfMonth = opts.day_of_month ?? "hidden";
  const ticks: FineTick[] = [];
  let d = dateAtColumn(minDate, 0, skip);
  for (let col = 0; col < visibleDays; col += 1) {
    const tick: FineTick = {
      day: col,
      label: format === "short" ? SHORT_DAYS[weekdayOf(d)] : String(weekdayNumber(d, start)),
    };
    if (dayOfMonth === "always") tick.sub = String(ymd(d).d);
    // The whole date, not just the day — a tooltip has the room, and "which
    // month am I in" is the question a week-first axis makes you ask.
    if (dayOfMonth !== "hidden") tick.title = d;
    ticks.push(tick);
    d = shiftWorkingDays(d, 1, skip);
  }
  return ticks;
}

/** Week bands — the coarse row under the densest zoom, where a month band would
 * span more screen than anyone can see at once. */
function weekBands(minDate: string, visibleDays: number, week: WeekRule, today: string, skip: boolean): CoarseBand[] {
  const start = week.start ?? "monday";
  const bands: CoarseBand[] = [];
  let cursor = 0;
  while (cursor < visibleDays) {
    const ws = weekStart(dateAtColumn(minDate, cursor, skip), start);
    const bandEnd = advance(cursor, columnOf(minDate, shiftDate(ws, 7), skip), visibleDays);
    bands.push({ day: cursor, days: bandEnd - cursor, label: weekLabelOf(ws, week, today) });
    cursor = bandEnd;
  }
  return bands;
}

/** Fine ticks at week starts, labelled with the custom week code and thinned to
 * whole-week steps so two labels never collide (7·`stepWeeks`·ppd ≥ min width).
 * Ticks land on real week boundaries; the partial week left of the first
 * boundary is covered by the month band above. */
function weekTicks(minDate: string, visibleDays: number, ppd: number, week: WeekRule, today: string, skip: boolean): FineTick[] {
  const start = week.start ?? "monday";
  const weekCols = skip ? 5 : 7; // a week spans 5 working columns when skipping weekends
  const stepWeeks = Math.max(1, Math.ceil(AXIS_MIN_LABEL_PX / (weekCols * ppd)));
  const ticks: FineTick[] = [];
  let d = weekStart(minDate, start); // the week-start DATE (≤ minDate)
  if (columnOf(minDate, d, skip) < 0) d = shiftDate(d, 7); // first week start at/after minDate
  for (;;) {
    const col = columnOf(minDate, d, skip);
    if (col >= visibleDays) break;
    ticks.push({ day: col, label: weekLabelOf(d, week, today) });
    d = shiftDate(d, 7 * stepWeeks);
  }
  return ticks;
}
