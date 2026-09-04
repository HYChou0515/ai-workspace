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
 * The chart itself is still day-granular: every position is a column offset in
 * days, and the clock only decides which day an edge lands on. Finer columns
 * are P3.
 */

export type Zoom = "day" | "week" | "month";
export type DragMode = "move" | "start" | "end";
export type Span = { start: string; end: string };

const DAY_MS = 86_400_000;
const MINUTE_MS = 60_000;

/** The three named zoom stops, in px-per-day — labelled anchor points the slider
 * snaps to. They are NOT the ends of the track: it travels past `day` (zoom in
 * further, days grow wider) and past `month` (zoom out further, months compress).
 * So the anchors sit INSIDE [PPD_MIN, PPD_MAX]. */
export const PPD_ANCHORS: Record<Zoom, number> = { day: 28, week: 10, month: 3 };
export const PPD_MIN = 1; // most zoomed-out (further out than the `month` anchor)
export const PPD_MAX = 56; // most zoomed-in (further in than the `day` anchor)

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
    return null;
  }
  let sa = canonicalEdge(a);
  let sb = canonicalEdge(b);
  if (sa === null && sb === null) return null;
  sa ??= sb as string;
  sb ??= sa;
  // Compared as the instants they DENOTE, not as bytes: a plain end date means
  // that day's last minute, so `09:30/the same date` is a morning's work, not a
  // reversed range. String order would have called it reversed and dropped it.
  if (instantOf(sb, "end") < instantOf(sa, "start")) return null;
  return { start: sa, end: sb };
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
 * two ends — as a start, that day's first minute; as an end, its last. That is
 * the same inclusive reading the chart has always drawn (7/13–7/15 is three
 * days, not two), just said in the value instead of patched on afterwards with
 * a `+1`. An edge that names a clock means exactly that minute at either end. */
export function instantOf(value: string, edge: "start" | "end"): number {
  const ms = parseUtc(value);
  if (edge === "start" || !DATE_ONLY.test(value)) return ms;
  return ms + DAY_MS - MINUTE_MS; // 23:59 — the day's last minute
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
export function applyDrag(span: Span, mode: DragMode, days: number, skip = false): Span {
  // A drag moves the bar by whole DAYS (P3 makes the step finer), so the time
  // of day rides along — it is not the drag's to discard. Every shift helper
  // answers in bare dates, so without re-attaching the clock one drag would
  // silently flatten a 09:30–17:00 bar into two whole days.
  const shift = (d: string) => shiftWorkingDays(d, days, skip) + d.slice(10);
  if (mode === "move") {
    return { start: shift(span.start), end: shift(span.end) };
  }
  if (mode === "start") {
    const start = shift(span.start);
    return { start: start > span.end ? span.end : start, end: span.end };
  }
  const end = shift(span.end);
  return { start: span.start, end: end < span.start ? span.start : end };
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
export type Axis = { unit: Zoom; fine: FineTick[]; coarse: CoarseBand[] };

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
export function axisFor(
  minDate: string,
  visibleDays: number,
  ppd: number,
  week?: WeekRule,
  today = "",
  skip = false,
  opts: AxisOptions = {},
): Axis {
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

/** Whether `date` is a Saturday or Sunday. */
export function isWeekend(date: string): boolean {
  const d = weekdayOf(date);
  return d === 0 || d === 6;
}

/** The column index of `date` relative to `from`: working days when `skip`, else
 * calendar days. Signed; a weekend date collapses onto the following working
 * column (so Fri, Sat, Sun, next-Mon are cols n, n+1, n+1, n+1). */
export function columnOf(from: string, date: string, skip: boolean): number {
  // Reduced to bare days FIRST. Both the ordering below and the week walk are
  // string comparisons, and those are only sound between two edges of the same
  // shape — `"2026-01-05"` sorts before `"2026-01-05T00:00"` though they name
  // the same moment.
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

/** How many columns a span COVERS — both ends included, because a `daterange`
 * is inclusive: 7/13–7/15 is a three-day task, not a two-day one. This is the
 * same counting the chart's own width uses (`columnOf(min, max) + 1`); a bar
 * measured with the bare `columnOf` stopped at the START of its end date, so
 * that day never got coloured and the range read a day short. Never below 1 —
 * `spanToDates` rejects a reversed range, so start ≤ end always. */
export function barColumns(span: Span, skip: boolean): number {
  return columnOf(span.start, span.end, skip) + 1;
}

/** The calendar date at working-day column `col` from `minDate` (inverse of
 * {@link columnOf}); plain `shiftDate` when not skipping. */
export function dateAtColumn(minDate: string, col: number, skip: boolean): string {
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

/** Add (or subtract) `n` WORKING days to a date, hopping over weekends; plain
 * `shiftDate` when not skipping. */
export function shiftWorkingDays(date: string, n: number, skip: boolean): string {
  if (!skip) return shiftDate(date, n);
  return dateAtColumn(date, n, true);
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
