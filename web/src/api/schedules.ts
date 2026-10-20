/**
 * Schedules, read back the way the SWEEP reads them — the backend interprets
 * (same parser, same next-run rule, same ledger) and this side only renders.
 * The overview lists every item's; an item's Workflows panel lists the same
 * rows narrowed to that item (`docs/plan-schedule-run-as.md` G10).
 *
 * Acting on ONE row — move its time, remove it, run it now — goes through the
 * row routes (`docs/plan-schedule-overview.md` §3), keyed by the file and the
 * row's identity (`trigger_id`): the server re-reads the file and finds the
 * row, so a row somebody changed meanwhile is a 409, never a rewrite of bytes
 * this page did not see. The panel and the overview share them.
 */

import { apiFetch } from "./http";

const enc = encodeURIComponent;

/** The newest run of a schedule — fired or run now. */
export type LastRun = {
  run_id: string;
  /** `pending` | `running` | `awaiting_human` | `done` | `error` | `cancelled` */
  status: string;
  /** Epoch ms. */
  started: number | null;
  ended: number | null;
  /** Started by "run now", not by the schedule's time. */
  by_hand: boolean;
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
  /** That page's title as Deployed; `""` when it has none or there is no page. */
  page_title: string;
  /** The workflow's title; `""` when it has none. */
  run_title: string;
  /** Whose own values a fire carries (`docs/plan-schedule-run-as.md`); `""` =
   * nobody — the item's shared values only. */
  runs_as: string;
  /** Why it is them: `self` (chose it), `author` (wrote the row), `admin` (a
   * superuser named them); `""` when it runs as nobody. */
  runs_as_reason: RunsAsReason;
  /** The superuser who named them, for `admin`. */
  runs_as_by: string;
  /** Whether it runs as the viewer. */
  runs_as_mine: boolean;
};

export type RunsAsReason = "" | "self" | "author" | "admin";

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
  /** May the viewer choose someone ELSE to run a schedule as (a superuser). */
  can_assign: boolean;
  /** Files listed although the sweep's index does not name them yet — they do
   * not run until it does. Only an item's own listing has any. */
  unindexed: string[];
};

/** Whether a row offers Run now — ONE rule for the overview and the item's
 * panel (decision 9): the viewer may run work in the item, the row has an
 * identity (the sweep reads it), its workflow exists and parses. The server
 * refuses the rest anyway; this keeps the button off rows it would refuse. */
export function mayRunNow(
  row: Pick<OverviewRow, "trigger_id" | "known" | "run_problem">,
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
  /** Every schedule the viewer may read, across items. */
  async overview(): Promise<ScheduleOverview> {
    const resp = await apiFetch("/schedules");
    if (!resp.ok) throw new Error(`list schedules failed: ${resp.status}`);
    return resp.json();
  },
  /** The overview narrowed to ONE item — its own schedules and each page's —
   * for its Workflows panel: the same rows, so the two screens agree. */
  async itemOverview(slug: string, itemId: string): Promise<ScheduleOverview> {
    const resp = await apiFetch(`/a/${enc(slug)}/items/${enc(itemId)}/schedules/overview`);
    if (!resp.ok) throw new Error(`list schedules failed: ${resp.status}`);
    return resp.json();
  },
  async editTime(
    slug: string,
    itemId: string,
    ref: RowRef,
    time: ScheduleTime | { cron: string; tz: string },
  ): Promise<void> {
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

