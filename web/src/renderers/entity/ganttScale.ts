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

const PX_PER_DAY: Record<Zoom, number> = { day: 28, week: 10, month: 3 };

export function pxPerDay(zoom: Zoom): number {
  return PX_PER_DAY[zoom];
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

/** A horizontal pixel delta → the nearest whole number of days at this zoom. */
export function deltaDays(dx: number, zoom: Zoom): number {
  return Math.round(dx / pxPerDay(zoom));
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
