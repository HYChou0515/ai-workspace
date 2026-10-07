/**
 * A schedule row's recurrence in the reader's words — shared by the item's
 * Workflows panel and the schedules overview (`docs/plan-schedule-overview.md`),
 * so the two pages cannot describe one row two ways.
 */
import type { MsgKey, useT } from "./i18n";

/** The recurrence in the reader's words, from the row as written (`every`, `at`,
 * `dow`, `dom`, `n`, `tz`). Only vocabulary: when a row fires NEXT is computed on
 * the backend, by the same rule the sweep fires it by, and arrives as `next_at`. */
export function describeSchedule(value: unknown, t: ReturnType<typeof useT>): string {
  // A row that is not an object has no fields to read; the backend has already
  // said so in `problems`, and the rewrite still carries the value as written.
  const raw: Record<string, unknown> =
    value !== null && typeof value === "object" && !Array.isArray(value)
      ? (value as Record<string, unknown>)
      : {};
  // The parser's rule for every one of these is Python's `or`: a falsy value
  // is the default. Python's falsy JSON values are null, false, 0, "", [] and
  // {} — the last two are truthy to `||`, so `||` is not the mirror.
  const at = typeof raw.at === "string" && raw.at ? raw.at : "00:00";
  const tz = typeof raw.tz === "string" && raw.tz ? raw.tz : "UTC";
  const every = pyFalsy(raw.every) ? "daily" : raw.every;
  let words: string;
  switch (every) {
    case "minutes":
      words = t("schedules.every.minutes", { n: Number(raw.n) || 0 });
      break;
    case "hourly":
      words = t("schedules.every.hourly");
      break;
    case "weekly": {
      const dow = typeof raw.dow === "string" ? raw.dow : "";
      const key = DOW_KEYS[dow];
      words = t("schedules.every.weekly", { dow: key ? t(key) : dow, at });
      break;
    }
    case "monthly":
      words = t("schedules.every.monthly", { dom: Number(raw.dom) || 0, at });
      break;
    case "daily":
      words = t("schedules.every.daily", { at });
      break;
    default:
      words = String(every);
  }
  return `${words} (${tz})`;
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

const DOW_KEYS: Record<string, MsgKey> = {
  mon: "schedules.dow.mon",
  tue: "schedules.dow.tue",
  wed: "schedules.dow.wed",
  thu: "schedules.dow.thu",
  fri: "schedules.dow.fri",
  sat: "schedules.dow.sat",
  sun: "schedules.dow.sun",
};
