/**
 * A time column's clock (#847/#848 P14): how its instants show.
 *
 * A zoned column (the wire names its `zone`) shows the wall time in that zone
 * and names it; a zone-less one shows as written, which the sandbox reads as
 * UTC. Neither ever reads the VIEWER's zone: `Date`'s local getters and
 * ECharts' default time axis both do, which put Taipei midnight at 16:00 for
 * a viewer in UTC and at 00:00 for one in Taipei.
 *
 * On a time axis the chart runs ECharts with `useUTC` and places each point at
 * its wall time read as UTC (`wall`), so ECharts' own ticks fall on the zone's
 * midnights and its labels read the zone's clock.
 */

const MINUTE = 60_000;
/** Offsets and their changes fall on quarter hours: one reading per quarter. */
const QUARTER = 15 * MINUTE;
const FIXED = /^([+-])(\d{2}):(\d{2})$/;

/** How finely a time is written: the date alone, or to the minute, second or
 * millisecond. */
export type Precision = "day" | "minute" | "second" | "ms";
const PRECISIONS: Precision[] = ["day", "minute", "second", "ms"];
const DAY = 24 * 60 * MINUTE;

export type Clock = {
  /** The zone named beside a time, or null for a zone-less column. */
  zone: string | null;
  /** The zone's offset from UTC at instant `ms`, in ms. */
  offset(ms: number): number;
  /** The wall time at `ms`, as epoch ms read as UTC. */
  wall(ms: number): number;
  /** The wall time at `ms` as text: the date, then the finer parts it has,
   * and at least those `at` names. */
  text(ms: number, at?: Precision): string;
  /** The finest part any of these instants has on this clock (#847/#848 PR 5
   * P24): a column's times are all written to it, so an hourly column's
   * midnight shows 00:00 rather than the date alone. */
  precision(instants: Iterable<number>): Precision;
  /** The one instant whose wall time on this clock is `wall` (epoch ms read
   * as UTC), or null when the zone had that wall time twice (its clocks went
   * back) or never (they went forward past it) (#847/#848 PR 5 P26): a rule
   * needs one place. The sandbox's `datum_instant` is held to it by
   * wire-corpus/datum-axes.json. */
  instantAt(wall: number): number | null;
};

const pad = (n: number, width = 2) => String(n).padStart(width, "0");

/** The finest part a wall time (epoch ms read as UTC) has. */
export function precisionOf(wall: number): Precision {
  // before 1970 the remainder is negative, but zero exactly when it is here
  const within = wall % DAY;
  if (within % 1000) return "ms";
  if (within % MINUTE) return "second";
  return within ? "minute" : "day";
}

/** A wall time (epoch ms read as UTC) as text: `2026-03-01`, `… 12:30`,
 * `… 12:30:05`, `… 12:30:05.250` — the parts it has, and at least `at`'s. */
export function wallText(wall: number, at: Precision = "day"): string {
  const d = new Date(wall);
  let out = `${pad(d.getUTCFullYear(), 4)}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}`;
  const level = Math.max(PRECISIONS.indexOf(precisionOf(wall)), PRECISIONS.indexOf(at));
  if (level >= 1) out += ` ${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`;
  if (level >= 2) out += `:${pad(d.getUTCSeconds())}`;
  if (level >= 3) out += `.${pad(d.getUTCMilliseconds(), 3)}`;
  return out;
}

function ianaOffset(zone: string): ((ms: number) => number) | null {
  let parts: Intl.DateTimeFormat;
  try {
    parts = new Intl.DateTimeFormat("en-US", {
      timeZone: zone,
      hourCycle: "h23",
      year: "numeric",
      month: "numeric",
      day: "numeric",
      hour: "numeric",
      minute: "numeric",
      second: "numeric",
    });
  } catch {
    return null; // a zone this browser does not know
  }
  const cache = new Map<number, number>();
  return (ms) => {
    const quarter = Math.floor(ms / QUARTER) * QUARTER;
    let offset = cache.get(quarter);
    if (offset === undefined) {
      const f: Record<string, number> = {};
      for (const p of parts.formatToParts(quarter)) if (p.type !== "literal") f[p.type] = Number(p.value);
      const d = new Date(0);
      d.setUTCFullYear(f.year, f.month - 1, f.day); // not Date.UTC: that reads years 0-99 as 1900s
      d.setUTCHours(f.hour, f.minute, f.second);
      offset = d.getTime() - quarter;
      cache.set(quarter, offset);
    }
    return offset;
  };
}

export function clockFor(zone: string | undefined): Clock {
  let offset: (ms: number) => number = () => 0;
  let name: string | null = null;
  const fixed = zone === undefined ? null : FIXED.exec(zone);
  if (fixed) {
    const minutes = (fixed[1] === "-" ? -1 : 1) * (Number(fixed[2]) * 60 + Number(fixed[3]));
    offset = () => minutes * MINUTE;
    name = zone as string;
  } else if (zone !== undefined) {
    const iana = ianaOffset(zone);
    // one this browser cannot read shows as UTC, and is named so
    if (iana) offset = iana;
    name = iana ? zone : "UTC";
  }
  return {
    zone: name,
    offset,
    wall: (ms) => ms + offset(ms),
    text: (ms, at) => wallText(ms + offset(ms), at),
    precision: (instants) => {
      let level = 0;
      for (const ms of instants) level = Math.max(level, PRECISIONS.indexOf(precisionOf(ms + offset(ms))));
      return PRECISIONS[level];
    },
    instantAt: (wall) => {
      // an instant naming `wall` is `wall` less the offset in force then; the
      // offsets a day either side are the two a change of clocks can mix
      const named = new Set<number>();
      for (const near of [wall - DAY, wall + DAY]) {
        const at = wall - offset(near);
        if (at + offset(at) === wall) named.add(at);
      }
      return named.size === 1 ? [...named][0] : null;
    },
  };
}
