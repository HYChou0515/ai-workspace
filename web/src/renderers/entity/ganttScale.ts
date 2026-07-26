/**
 * gantt time-scale + drag math (#448 A2 / #450) — pure, unit-tested, so the
 * `GanttView` component stays a thin pointer-event shell. The timeline is a
 * fixed px-per-day scale (zoom picks the density); a pixel drag converts to a
 * whole number of days, and a bar drag rewrites the record's `daterange` value.
 * Dates are day-resolution `YYYY-MM-DD` strings compared lexicographically
 * (== chronologically, since they're zero-padded) and shifted in UTC to dodge
 * timezone drift.
 */

export type Zoom = "day" | "week" | "month";
export type DragMode = "move" | "start" | "end";
export type Span = { start: string; end: string };

const DAY_MS = 86_400_000;

/** The three named zoom stops, in px-per-day. They are no longer the ONLY
 * densities — the zoom is continuous (a slider) — but they remain the labelled
 * anchor points the slider snaps to, and bound its travel: `month` is the most
 * zoomed-out density we offer, `day` the most zoomed-in. */
export const PPD_ANCHORS: Record<Zoom, number> = { day: 28, week: 10, month: 3 };
export const PPD_MIN = PPD_ANCHORS.month;
export const PPD_MAX = PPD_ANCHORS.day;

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

/** Add (or subtract) whole days to a `YYYY-MM-DD` date, in UTC. */
export function shiftDate(date: string, days: number): string {
  return toISODate(Date.parse(date) + days * DAY_MS);
}

/** Whole UTC days from `a` to `b` (negative if `b` precedes `a`). */
export function daysBetween(a: string, b: string): number {
  return Math.round((Date.parse(b) - Date.parse(a)) / DAY_MS);
}

/** A horizontal pixel delta → the nearest whole number of days at this
 * (continuous) px-per-day density. */
export function deltaDays(dx: number, ppd: number): number {
  return Math.round(dx / ppd);
}

/** Parse a `daterange` value (`"start/end"` string, `[start, end]`, or
 * `{start,end}` / `{from,to}`) into `YYYY-MM-DD` strings, or `null` for junk /
 * a reversed range. */
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
  const sa = Date.parse(String(a));
  const sb = Date.parse(String(b));
  if (Number.isNaN(sa) || Number.isNaN(sb) || sb < sa) return null;
  return { start: toISODate(sa), end: toISODate(sb) };
}

/** Apply a drag of `days` to a span: `move` shifts both ends (keeps duration);
 * `start` / `end` resize one edge, clamped so the range never inverts. */
export function applyDrag(span: Span, mode: DragMode, days: number): Span {
  if (mode === "move") {
    return { start: shiftDate(span.start, days), end: shiftDate(span.end, days) };
  }
  if (mode === "start") {
    const start = shiftDate(span.start, days);
    return { start: start > span.end ? span.end : start, end: span.end };
  }
  const end = shiftDate(span.end, days);
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

export type FineTick = { day: number; label: string };
export type CoarseBand = { day: number; days: number; label: string };
export type Axis = { unit: Zoom; fine: FineTick[]; coarse: CoarseBand[] };

/** Horizontal room (px) reserved per fine-tier label. A fine step is only
 * chosen if `stepDays * ppd` clears this, so labels never touch. */
export const AXIS_MIN_LABEL_PX = 36;

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

/** Calendar-month bands clipped to [0, visibleDays), in day-offsets from
 * minDate. A band opening before minDate is clamped to day 0. */
function monthBands(minDate: string, visibleDays: number): CoarseBand[] {
  const bands: CoarseBand[] = [];
  let cursor = 0;
  while (cursor < visibleDays) {
    const { y, m } = ymd(shiftDate(minDate, cursor));
    const nextStart = firstOfMonth(m === 11 ? y + 1 : y, m === 11 ? 0 : m + 1);
    const bandEnd = Math.min(daysBetween(minDate, nextStart), visibleDays);
    bands.push({ day: cursor, days: bandEnd - cursor, label: `${MONTHS[m]} ${y}` });
    cursor = bandEnd;
  }
  return bands;
}

/** Calendar-year bands clipped to [0, visibleDays), in day-offsets from minDate. */
function yearBands(minDate: string, visibleDays: number): CoarseBand[] {
  const bands: CoarseBand[] = [];
  let cursor = 0;
  while (cursor < visibleDays) {
    const { y } = ymd(shiftDate(minDate, cursor));
    const bandEnd = Math.min(daysBetween(minDate, `${y + 1}-01-01`), visibleDays);
    bands.push({ day: cursor, days: bandEnd - cursor, label: String(y) });
    cursor = bandEnd;
  }
  return bands;
}

/** Fine ticks at calendar-month starts, thinned so labels fit at this density. */
function monthTicks(minDate: string, visibleDays: number, ppd: number): FineTick[] {
  const step = [1, 2, 3, 6, 12].find((s) => s * 30 * ppd >= AXIS_MIN_LABEL_PX) ?? 12;
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
    const day = daysBetween(minDate, firstOfMonth(y, m));
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
export function axisFor(minDate: string, visibleDays: number, ppd: number, week?: WeekRule, today = ""): Axis {
  if (ppd >= DETAIL_PPD) {
    const step = [1, 2, 5, 7, 14].find((s) => s * ppd >= AXIS_MIN_LABEL_PX) ?? 14;
    // With a week rule, week codes are a MIDDLE tier: once the day labels would
    // thin past every other day (step ≥ 5) the row switches to week codes;
    // zoomed in tighter than that it stays on day numbers (dates). So dragging
    // day → week visibly flips dates into week codes.
    if (week && step >= 5) {
      return { unit: "week", fine: weekTicks(minDate, visibleDays, ppd, week, today), coarse: monthBands(minDate, visibleDays) };
    }
    const fine: FineTick[] = [];
    for (let day = 0; day < visibleDays; day += step) {
      fine.push({ day, label: String(ymd(shiftDate(minDate, day)).d) });
    }
    return { unit: step >= 7 ? "week" : "day", fine, coarse: monthBands(minDate, visibleDays) };
  }
  return { unit: "month", fine: monthTicks(minDate, visibleDays, ppd), coarse: yearBands(minDate, visibleDays) };
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
  return new Date(`${date}T00:00:00Z`).getUTCDay();
}

/** The first day of the week `date` falls in, for a week that begins on `start`
 * (default Monday). Returned as `YYYY-MM-DD`. */
export function weekStart(date: string, start: Weekday = "monday"): string {
  const offset = (weekdayOf(date) - WEEKDAY_INDEX[start] + 7) % 7;
  return shiftDate(date, -offset);
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

/** Fine ticks at week starts, labelled with the custom week code and thinned to
 * whole-week steps so two labels never collide (7·`stepWeeks`·ppd ≥ min width).
 * Ticks land on real week boundaries; the partial week left of the first
 * boundary is covered by the month band above. */
function weekTicks(minDate: string, visibleDays: number, ppd: number, week: WeekRule, today: string): FineTick[] {
  const start = week.start ?? "monday";
  const stepWeeks = Math.max(1, Math.ceil(AXIS_MIN_LABEL_PX / (7 * ppd)));
  const ticks: FineTick[] = [];
  let day = daysBetween(minDate, weekStart(minDate, start)); // ≤ 0
  if (day < 0) day += 7; // first week start at/after minDate
  for (; day < visibleDays; day += 7 * stepWeeks) {
    ticks.push({ day, label: weekLabelOf(shiftDate(minDate, day), week, today) });
  }
  return ticks;
}
