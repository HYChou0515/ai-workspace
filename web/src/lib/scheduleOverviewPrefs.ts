/**
 * How the schedules overview is arranged — grouped or not, and in which order
 * (`docs/plan-schedule-overview.md` decision 14: switchable on the page,
 * remembered per viewer).
 *
 * Per BROWSER in localStorage, the `wuiView` shape: a convenience about how
 * someone likes to look at a list. Both sides in try/catch — a storage that
 * throws makes the choice not sticky, not the page broken. Anything that is not
 * one of the known words reads as the default (no grouping, next run first).
 */
import { useCallback, useState } from "react";

const KEY = "rca.scheduleOverview";

export type ScheduleGrouping = "none" | "app";
export type ScheduleSort = "next" | "trouble";
export type ScheduleOverviewPrefs = { group: ScheduleGrouping; sort: ScheduleSort };

const DEFAULTS: ScheduleOverviewPrefs = { group: "none", sort: "next" };

export function readScheduleOverviewPrefs(): ScheduleOverviewPrefs {
  try {
    const parsed = JSON.parse(localStorage.getItem(KEY) ?? "{}") as Record<string, unknown>;
    return {
      group: parsed.group === "app" ? "app" : "none",
      sort: parsed.sort === "trouble" ? "trouble" : "next",
    };
  } catch {
    return DEFAULTS;
  }
}

function write(prefs: ScheduleOverviewPrefs): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(prefs));
  } catch {
    /* localStorage unavailable — the choice just isn't sticky */
  }
}

/** React state over the choice: initialised from storage, every change
 * written through. */
export function useScheduleOverviewPrefs(): [
  ScheduleOverviewPrefs,
  (patch: Partial<ScheduleOverviewPrefs>) => void,
] {
  const [prefs, setPrefs] = useState<ScheduleOverviewPrefs>(readScheduleOverviewPrefs);
  const update = useCallback((patch: Partial<ScheduleOverviewPrefs>) => {
    setPrefs((current) => {
      const next = { ...current, ...patch };
      write(next);
      return next;
    });
  }, []);
  return [prefs, update];
}
