/**
 * An item's schedules (`.workflows/schedules.json`), read back the way the SWEEP
 * reads them — the backend interprets (same parser, same next-run rule, same
 * ledger) and this side only renders. There is no schedule-specific write
 * route: cancelling one is rewriting the file minus that row through the
 * ordinary file write, which lands on the path the platform indexes.
 */

import { apiFetch } from "./http";

const enc = encodeURIComponent;

export type ScheduleRow = {
  /** Position in the file — the handle a rewrite removes by. */
  index: number;
  /** The row EXACTLY as written — whatever JSON value it was, an object or not —
   * so a rewrite keeps what it did not touch, byte for byte. */
  raw: unknown;
  /** Why the sweep refuses this row; empty when it will fire. */
  problems: string[];
  run: string;
  /** English sentence for the agent; the panel renders from `raw` instead. */
  describe: string;
  next_run: string;
  /** `YYYY-MM-DD HH:MM` in the row's zone, or `""` when it is due right now. */
  next_at: string;
  due_now: boolean;
  tz: string;
  /** Whether `run` names a workflow this item offers — a deleted one is skipped
   * by the sweep with a log line nobody reads. */
  known: boolean;
  /** THE verdict: will the sweep fire this row. False for a refused row, an
   * unknown workflow, a file over the cap, a deployment with the sweep off, or
   * a file the sweep's index does not name yet. The next-run fields are filled
   * only when this is true. */
  runnable: boolean;
  payload: Record<string, unknown>;
};

export type ItemSchedules = {
  /** Whether this deployment runs scheduled work at all. */
  enabled: boolean;
  /** Whether the sweep's index names this file. A file that reached the store
   * past every hook is invisible to the sweep until the next turn's reconcile. */
  indexed: boolean;
  path: string;
  rows: ScheduleRow[];
  /** File-level problems (the file itself could not be read). */
  problems: string[];
};

export const schedulesApi = {
  async list(slug: string, itemId: string): Promise<ItemSchedules> {
    const resp = await apiFetch(`/a/${enc(slug)}/items/${enc(itemId)}/schedules`);
    if (!resp.ok) throw new Error(`list schedules failed: ${resp.status}`);
    return resp.json();
  },
};

/** Where the item's own schedules live — the same folder as its workflows. */
export const SCHEDULES_PATH = ".workflows/schedules.json";
