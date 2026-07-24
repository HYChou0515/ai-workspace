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
 * month labels over year bands. The fine row is always thinned to fit. */
export function axisFor(minDate: string, visibleDays: number, ppd: number): Axis {
  if (ppd >= DETAIL_PPD) {
    const step = [1, 2, 5, 7, 14].find((s) => s * ppd >= AXIS_MIN_LABEL_PX) ?? 14;
    const fine: FineTick[] = [];
    for (let day = 0; day < visibleDays; day += step) {
      fine.push({ day, label: String(ymd(shiftDate(minDate, day)).d) });
    }
    return { unit: step >= 7 ? "week" : "day", fine, coarse: monthBands(minDate, visibleDays) };
  }
  return { unit: "month", fine: monthTicks(minDate, visibleDays, ppd), coarse: yearBands(minDate, visibleDays) };
}
