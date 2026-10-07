/**
 * An item's schedules (`.workflows/schedules.json`), read back the way the SWEEP
 * reads them — the backend interprets (same parser, same next-run rule, same
 * ledger) and this side only renders.
 *
 * Acting on ONE row — move its time, remove it, run it now — goes through the
 * row routes (`docs/plan-schedule-overview.md` §3), keyed by the file and the
 * row's identity (`trigger_id`): the server re-reads the file and finds the
 * row, so a row somebody changed meanwhile is a 409, never a rewrite of bytes
 * this page did not see. The panel and the overview share them.
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
  /** Why the workflow `run` names will not run although the item has it — its
   * file does not parse. Not `problems` (the ROW is wrong) and not `!known`
   * (no such workflow): the fix is to the workflow. */
  run_problem: string;
  /** THE verdict: will the sweep fire this row. False for a refused row, an
   * unknown workflow, a workflow whose file does not parse (`run_problem`), a
   * file over the cap, a deployment with the sweep off, or a file the sweep's
   * index does not name yet. The next-run fields are filled
   * only when this is true. */
  runnable: boolean;
  payload: Record<string, unknown>;
  /** The row's identity — what the row routes find it by. `""` for a row the
   * sweep refuses (it has no identity because it never fires). */
  trigger_id: string;
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
  /** What THIS viewer may do with a row: edit its time or remove it, and run
   * it now — the row routes' own gates, so the panel offers only those. */
  can_edit: boolean;
  can_run: boolean;
  /** May read the item's files — without it rows come without their `with`. */
  can_read: boolean;
};

/** The newest run of a schedule — fired or run now. */
export type LastRun = {
  run_id: string;
  /** `pending` | `running` | `awaiting_human` | `done` | `error` | `cancelled` */
  status: string;
  /** Epoch ms. */
  started: number | null;
  ended: number | null;
};

/** One row of the overview: a schedule, where it lives, its last run and its
 * next, and what THIS viewer may do with it. */
export type OverviewRow = {
  slug: string;
  item_id: string;
  item_title: string;
  item_owner: string;
  /** The schedules file — the item's own (`/.workflows/schedules.json`) or a
   * page's (`/<folder>/schedules.json`). */
  path: string;
  index: number;
  raw: unknown;
  problems: string[];
  run: string;
  describe: string;
  runnable: boolean;
  next_at: string;
  /** `next_at` as one instant (epoch ms) — what rows in different zones sort
   * on. `null` for a row that will not run. */
  next_ms: number | null;
  due_now: boolean;
  tz: string;
  known: boolean;
  run_problem: string;
  trigger_id: string;
  last_run: LastRun | null;
  can_edit: boolean;
  can_run: boolean;
  /** May read the item's files — without it `raw` comes without its `with`. */
  can_read: boolean;
  /** The Deployed page in the file's folder — where Open goes for a page's
   * row; `""` sends Open to the item. */
  page_path: string;
};

/** A schedules file with problems of its own (one that does not parse has no
 * rows to carry them). */
export type OverviewFile = {
  slug: string;
  item_id: string;
  item_title: string;
  path: string;
  problems: string[];
};

export type ScheduleOverview = {
  enabled: boolean;
  rows: OverviewRow[];
  files: OverviewFile[];
};

/** Whether a row offers Run now — ONE rule for the overview and the item's
 * panel (decision 9): the viewer may run work in the item, the row has an
 * identity (the sweep reads it), its workflow exists and parses. The server
 * refuses the rest anyway; this keeps the button off rows it would refuse. */
export function mayRunNow(
  row: Pick<ScheduleRow, "trigger_id" | "known" | "run_problem">,
  canRun: boolean,
): boolean {
  return canRun && row.trigger_id !== "" && row.known && !row.run_problem;
}

/** Which row: the file and the row's identity in it. A row the sweep refuses
 * has no identity (`trigger_id: ""`); Remove names it by its position
 * (`index`) and the value it was listed with (`raw`). */
export type RowRef = { path: string; trigger_id: string; raw?: unknown; index?: number };

export type Period = "minutes" | "hourly" | "daily" | "weekly" | "monthly";

/** A new time — the server keeps only the fields the chosen `every` reads. */
export type ScheduleTime = {
  every: Period;
  n?: number;
  at?: string;
  dow?: string;
  dom?: number;
  tz?: string;
};

/** A refused action, carrying the server's sentence — shown to the person as is. */
export class ScheduleActionError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

async function act(slug: string, itemId: string, verb: string, body: unknown): Promise<Response> {
  const resp = await apiFetch(`/a/${enc(slug)}/items/${enc(itemId)}/schedules/${verb}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!resp.ok) {
    let detail = `${verb} failed: ${resp.status}`;
    try {
      const parsed = (await resp.json()) as { detail?: unknown };
      if (typeof parsed.detail === "string") detail = parsed.detail;
    } catch {
      /* not JSON — keep the status line */
    }
    throw new ScheduleActionError(resp.status, detail);
  }
  return resp;
}

export const schedulesApi = {
  async list(slug: string, itemId: string): Promise<ItemSchedules> {
    const resp = await apiFetch(`/a/${enc(slug)}/items/${enc(itemId)}/schedules`);
    if (!resp.ok) throw new Error(`list schedules failed: ${resp.status}`);
    return resp.json();
  },
  /** Every schedule the viewer may read, across items. */
  async overview(): Promise<ScheduleOverview> {
    const resp = await apiFetch("/schedules");
    if (!resp.ok) throw new Error(`list schedules failed: ${resp.status}`);
    return resp.json();
  },
  async editTime(slug: string, itemId: string, ref: RowRef, time: ScheduleTime): Promise<void> {
    await act(slug, itemId, "edit", { ...ref, ...time });
  },
  async remove(slug: string, itemId: string, ref: RowRef): Promise<void> {
    await act(slug, itemId, "remove", ref);
  },
  /** Run it now; resolves to the run's id. */
  async runNow(slug: string, itemId: string, ref: RowRef): Promise<string> {
    const resp = await act(slug, itemId, "run", ref);
    const body = (await resp.json()) as { run_id?: string };
    return body.run_id ?? "";
  },
};

export type SchedulesApi = typeof schedulesApi;

/** Where the item's own schedules live — the same folder as its workflows. */
export const SCHEDULES_PATH = ".workflows/schedules.json";
