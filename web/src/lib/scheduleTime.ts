/**
 * Schedule times in the VIEWER's clock (`docs/plan-schedule-overview-polish.md`
 * decisions 1–6).
 *
 * The backend answers in instants (`next_ms`, a run's `started`/`ended`) and
 * rows as written (`raw`, whose `at` is a wall time in the row's `tz`, UTC when
 * unset — the zone the sweep fires it by). Everything a person reads is turned
 * into their own zone here, with no zone label: one table that mixed `UTC` and
 * `Asia/Taipei` made every reader do the arithmetic.
 *
 * Pure functions over `Intl`. The viewer's zone and `now` are parameters so a
 * test pins them — the machine a test runs on has a zone of its own.
 */
import type { MsgKey, useT } from "./i18n";

type T = ReturnType<typeof useT>;

/** A wall-clock reading: `mo` 1–12, `dow` 0 = Monday … 6 = Sunday. */
export type Wall = { y: number; mo: number; d: number; dow: number; hh: number; mm: number };

export const DOWS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"] as const;
const EN_DOW: Record<string, number> = { Mon: 0, Tue: 1, Wed: 2, Thu: 3, Fri: 4, Sat: 5, Sun: 6 };

const fmtCache = new Map<string, Intl.DateTimeFormat>();
function wallFormat(zone: string): Intl.DateTimeFormat {
  let fmt = fmtCache.get(zone);
  if (!fmt) {
    fmt = new Intl.DateTimeFormat("en-US", {
      timeZone: zone,
      year: "numeric",
      month: "numeric",
      day: "numeric",
      weekday: "short",
      hour: "numeric",
      minute: "numeric",
      hourCycle: "h23",
    });
    fmtCache.set(zone, fmt);
  }
  return fmt;
}

/** Whether the browser knows this zone. */
export function validZone(zone: string): boolean {
  try {
    wallFormat(zone);
    return true;
  } catch {
    return false;
  }
}

/** The viewer's own zone — the browser's. */
export function viewerZone(): string {
  return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
}

/** The instant `ms` read on a clock in `zone`. */
export function wallOf(ms: number, zone: string): Wall {
  const p: Record<string, string> = {};
  for (const part of wallFormat(zone).formatToParts(new Date(ms))) p[part.type] = part.value;
  return {
    y: Number(p.year),
    mo: Number(p.month),
    d: Number(p.day),
    dow: EN_DOW[p.weekday] ?? 0,
    hh: Number(p.hour) % 24,
    mm: Number(p.minute),
  };
}

/** The instant a clock in `zone` reads `y-mo-d hh:mm` — `next_run_ms`'s answer
 * (Python's `datetime(..., tzinfo=ZoneInfo(zone))`, fold=0), so a period moved
 * here agrees with the `next_ms` beside it: a wall time a DST change repeats is
 * its FIRST reading; one it skips is read with the offset in force before the
 * change, so it lands that much after it (02:30 on a New York spring-forward
 * day is 03:30 EDT). Not when the sweep FIRES on that one day: it compares the
 * zone's wall clock, which jumps past 02:30 straight to 03:00, and fires then.
 * The two candidate offsets are the ones a day either side. */
export function zonedMs(y: number, mo: number, d: number, hh: number, mm: number, zone: string): number {
  const asUtc = Date.UTC(y, mo - 1, d, hh, mm);
  const offset = (ms: number) => {
    const w = wallOf(ms, zone);
    return Date.UTC(w.y, w.mo - 1, w.d, w.hh, w.mm) - Math.floor(ms / 60000) * 60000;
  };
  const before = asUtc - offset(asUtc - 86400000);
  const after = asUtc - offset(asUtc + 86400000);
  const reads = (ms: number) => {
    const w = wallOf(ms, zone);
    return w.y === y && w.mo === mo && w.d === d && w.hh === hh && w.mm === mm;
  };
  const real = [before, after].filter(reads);
  if (real.length > 0) return Math.min(...real);
  return before;
}

/** A zone's name as a person says it — "世界標準時間", "台北標準時間",
 * "Eastern Time" — never an IANA id when the browser has a name for it. */
export function zoneName(zone: string, locale: string): string {
  const name = (style: "longGeneric" | "long") => {
    try {
      return (
        new Intl.DateTimeFormat(locale, { timeZone: zone, timeZoneName: style })
          .formatToParts(new Date(0))
          .find((p) => p.type === "timeZoneName")?.value ?? ""
      );
    } catch {
      return "";
    }
  };
  const generic = name("longGeneric");
  if (generic && !generic.startsWith("GMT")) return generic;
  return name("long") || zone;
}

const pad = (n: number) => String(n).padStart(2, "0");
const hm = (w: Pick<Wall, "hh" | "mm">) => `${pad(w.hh)}:${pad(w.mm)}`;
const dayNumber = (w: Pick<Wall, "y" | "mo" | "d">) => Date.UTC(w.y, w.mo - 1, w.d) / 86400000;

/** `"HH:MM"` as the sweep reads it (`_looks_like_time`): digits either side
 * of one colon, unpadded or zero-led too — "9:5" fires at 09:05 — within a day.
 * `null` for anything else. */
export function parseAt(at: string): { hh: number; mm: number } | null {
  const m = /^(\d+):(\d+)$/.exec(at);
  if (!m) return null;
  const hh = Number(m[1]);
  const mm = Number(m[2]);
  return hh <= 23 && mm <= 59 ? { hh, mm } : null;
}

/** The day of week in the reader's words — 週一 / Mon. */
export function dowWord(dow: number, t: T): string {
  return t(`schedules.dow.${DOWS[dow]}` as MsgKey);
}

/** An instant as a person wants it next to a schedule: "15 分鐘後" / "3 分鐘前"
 * within the hour, else "今天 / 明天 / 昨天 HH:MM", else the date — on the
 * viewer's clock, 24-hour.
 *
 * `kind` says which side of `now` the instant is known to be on: a run that
 * started is "past", a next run is "future". The page's `now` moves only every
 * 30 s, so a run that started after the last tick is a few seconds "ahead" of
 * it — and read "不到 1 分鐘後" in the recorded demo. Known past is never
 * ahead; known future is never gone (a next run the sweep has yet to pick up
 * reads "不到 1 分鐘後", not "3 分鐘前"). */
export function whenText(ms: number, now: number, zone: string, t: T, kind?: "past" | "future"): string {
  if (kind === "past") ms = Math.min(ms, now);
  if (kind === "future") ms = Math.max(ms, now);
  const ahead = ms - now;
  if (Math.abs(ahead) < 3600000) {
    const n = Math.floor(Math.abs(ahead) / 60000);
    // At `now` exactly, the side is the one `kind` knows.
    const isAhead = kind === undefined ? ahead >= 0 : kind === "future";
    if (isAhead) return n < 1 ? t("time.inUnderAMinute") : t("time.inMinutes", { n });
    return n < 1 ? t("time.justNow") : t("time.minutesAgo", { n });
  }
  const w = wallOf(ms, zone);
  const days = dayNumber(w) - dayNumber(wallOf(now, zone));
  if (days === 0) return t("time.today", { hm: hm(w) });
  if (days === 1) return t("time.tomorrow", { hm: hm(w) });
  if (days === -1) return t("time.yesterday", { hm: hm(w) });
  return fullOrShort(w, w.y !== wallOf(now, zone).y, t);
}

/** The whole date and time, for a hover. */
export function fullTime(ms: number, zone: string, t: T): string {
  return fullOrShort(wallOf(ms, zone), true, t);
}

function fullOrShort(w: Wall, withYear: boolean, t: T): string {
  const vars = { y: w.y, m: w.mo, d: w.d, dow: dowWord(w.dow, t), hm: hm(w) };
  return withYear ? t("time.dateYear", vars) : t("time.date", vars);
}

/** A row's time fields, read the parser's way (Python's `or`: a falsy value
 * is the default) — `every` daily, `at` 00:00, `tz` UTC. */
export type RowTime = {
  every: string;
  n: number;
  at: string;
  dow: string;
  dom: number;
  tz: string;
};

export function rowTime(value: unknown): RowTime {
  const raw: Record<string, unknown> =
    value !== null && typeof value === "object" && !Array.isArray(value)
      ? (value as Record<string, unknown>)
      : {};
  return {
    every: pyFalsy(raw.every) ? "daily" : String(raw.every),
    n: Number(raw.n) || 0,
    at: typeof raw.at === "string" && raw.at ? raw.at : "00:00",
    dow: typeof raw.dow === "string" ? raw.dow : "",
    dom: Number(raw.dom) || 0,
    tz: typeof raw.tz === "string" && raw.tz ? raw.tz : "UTC",
  };
}

/** Python's truth test over a decoded JSON value: `null`, `false`, `0`, `""`,
 * `[]` and `{}` are falsy; everything else is truthy. */
function pyFalsy(value: unknown): boolean {
  if (value === null || value === undefined || value === false || value === 0 || value === "") {
    return true;
  }
  if (Array.isArray(value)) return value.length === 0;
  if (typeof value === "object") return Object.keys(value).length === 0;
  return false;
}

/** A daily / weekly / monthly time moved from the row's zone onto another
 * clock, through a real occurrence: the one in the period holding `refMs`
 * (the row's next run, else now), so a zone's daylight saving is the one in
 * force when it next runs. `null` when it does not move cleanly — a monthly
 * whose occurrence lands on another day of the month (day 1 at 02:00 Taipei
 * is the LAST day of the month before in UTC, a rule no row can write) — or
 * when the row's zone or time is not one the browser can read. */
export function moveTime(
  time: Pick<RowTime, "every" | "at" | "dow" | "dom" | "tz">,
  to: string,
  refMs: number,
): { at: string; dow: string; dom: number } | null {
  const parsed = parseAt(time.at);
  if (!parsed || !validZone(time.tz) || !validZone(to)) return null;
  const { hh, mm } = parsed;
  const ref = wallOf(refMs, time.tz);
  let day = Date.UTC(ref.y, ref.mo - 1, ref.d);
  if (time.every === "weekly") {
    const want = DOWS.indexOf(time.dow as (typeof DOWS)[number]);
    if (want < 0) return null;
    day += (want - ref.dow) * 86400000;
  } else if (time.every === "monthly") {
    const last = new Date(Date.UTC(ref.y, ref.mo, 0)).getUTCDate();
    day = Date.UTC(ref.y, ref.mo - 1, Math.min(Math.max(time.dom, 1), last));
  } else if (time.every !== "daily") {
    return null;
  }
  const date = new Date(day);
  const ms = zonedMs(date.getUTCFullYear(), date.getUTCMonth() + 1, date.getUTCDate(), hh, mm, time.tz);
  const there = wallOf(ms, to);
  if (time.every === "monthly" && there.d !== date.getUTCDate()) return null;
  return { at: hm(there), dow: DOWS[there.dow], dom: time.dom };
}

/** Whether a minutes / hourly row fires on the same minutes on `to`'s clock
 * as on its own, all year. They fire at the minutes of the hour their zone's
 * clock reads (hourly at :00, every 15 at :00/:15/:30/:45), so a zone a whole
 * hour away reads the same — and Kolkata (+05:30) does not: a UTC hourly runs
 * at :30 there. Asked at `refMs` and half a year either side, because a DST
 * shift can change the gap: Lord Howe moves by 30 minutes for summer. */
export function subDailyMoves(time: Pick<RowTime, "every" | "n" | "tz">, to: string, refMs: number): boolean {
  if (!validZone(time.tz) || !validZone(to)) return false;
  const width = time.every === "hourly" ? 60 : time.n;
  const half = 182 * 86400000;
  return [refMs - half, refMs, refMs + half].every((at) => {
    const offset = (zone: string) => {
      const w = wallOf(at, zone);
      return (Date.UTC(w.y, w.mo - 1, w.d, w.hh, w.mm) - Math.floor(at / 60000) * 60000) / 60000;
    };
    const apart = Math.abs(offset(time.tz) - offset(to));
    if (apart % 60 === 0) return true;
    return width > 0 && 60 % width === 0 && apart % width === 0;
  });
}

/** How often, in words, from fields already on the clock they are read on. */
function periodWords(time: RowTime, t: T): string {
  switch (time.every) {
    case "minutes":
      return time.n === 1 ? t("schedules.every.minute") : t("schedules.every.minutes", { n: time.n });
    case "hourly":
      return t("schedules.every.hourly");
    case "weekly": {
      const i = DOWS.indexOf(time.dow as (typeof DOWS)[number]);
      return t("schedules.every.weekly", { dow: i >= 0 ? dowWord(i, t) : time.dow, at: time.at });
    }
    case "monthly":
      return t("schedules.every.monthly", { dom: time.dom, at: time.at });
    case "daily":
      return t("schedules.every.daily", { at: time.at });
    default:
      return time.every;
  }
}

/** A row's period on the viewer's clock (decision 3), and what it was set as —
 * for the hover; `set` is "" when there is nothing to add (minutes, hourly). */
export function periodText(
  value: unknown,
  opts: { viewer: string; now: number; nextMs?: number | null; locale: string },
  t: T,
): { text: string; set: string } {
  const time = rowTime(value);
  const asWritten = periodWords(time, t);
  const where = validZone(time.tz) ? zoneName(time.tz, opts.locale) : time.tz;
  const set = t("schedules.setAs", { what: asWritten, zone: where });
  if (time.every === "minutes" || time.every === "hourly") {
    // The same minutes on the viewer's clock: nothing to say about a zone.
    if (subDailyMoves(time, opts.viewer, opts.nextMs ?? opts.now)) return { text: asWritten, set: "" };
    return { text: t("schedules.inZone", { what: asWritten, zone: where }), set };
  }
  if (time.every !== "daily" && time.every !== "weekly" && time.every !== "monthly") {
    return { text: asWritten, set: "" };
  }
  const moved = moveTime(time, opts.viewer, opts.nextMs ?? opts.now);
  if (moved === null) return { text: t("schedules.inZone", { what: asWritten, zone: where }), set };
  return { text: periodWords({ ...time, ...moved }, t), set };
}
