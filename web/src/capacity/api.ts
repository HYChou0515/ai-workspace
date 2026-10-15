/**
 * The capacity dashboard's API (docs/plan-capacity-usage.md §11) — superusers
 * only; the server answers 403 to anyone else. Mirrors the response models in
 * `src/workspace_app/api/capacity_routes.py`; the fixtures beside this file are
 * real responses (`scripts/capacity_fixtures.py`), checked against those models
 * by `tests/capacity/test_fe_fixtures.py`.
 */
import { apiFetch, httpErrorFrom } from "../api/http";

export type CapacityDays = 7 | 28 | 90;
export const CAPACITY_DAYS: CapacityDays[] = [7, 28, 90];

export type Unit = "cores" | "bytes" | "inflight";

export type Readiness = {
  ready: boolean;
  reason:
    | "source_error"
    | "source_missing"
    | "period_too_short"
    | "accumulating"
    | "too_few_people"
    | "no_regression"
    | null;
  /** The sources failing (source_error) or not connected (source_missing). */
  missing: string[];
  /** What the first failing source raised. */
  error: string | null;
  have_days: number;
  need_days: number;
  /** The whole days this period holds; fewer than need_days → period_too_short. */
  period_days: number;
};

export type Stat = { avg: number; peak: number };
export type Group = { key: string; avg: number; peak: number };

export type Line = {
  id: string;
  component: string;
  resource: string;
  unit: Unit;
  model: string;
  /** A worker line's job type ("other": pods that ran none); empty otherwise. */
  job: string;
  method: "regression" | "direct" | "fixed";
  capacity: number | null;
  machines: number | null;
  machine_label: string;
  threshold: number | null;
  threshold_source: "knee" | "default" | null;
  peak: number | null;
  avg: number | null;
  utilization_peak: number | null;
  fixed: Stat | null;
  other: Stat | null;
  residual: Stat | null;
  total: Stat | null;
  groups: { person: Group[]; app: Group[]; purpose: Group[] };
  daily: { peak: (number | null)[]; avg: (number | null)[] };
  fit: {
    r2: number | null;
    holdout_mape: number | null;
    /** How many of the last days `holdout_mape` predicted. */
    holdout_days: number;
    rows: number;
    columns: [string, number][];
    collinear: string[][];
  } | null;
  distribution: {
    p50: number;
    p90: number;
    p99: number;
    top10_share: number | null;
    values: number[];
  } | null;
};

export type Trend = { intercept: number; slope: number; residual_sd: number };

export type Pair = {
  id: "a" | "b" | "c" | "d" | "e" | "gpu";
  model: string;
  x: string;
  y: string;
  sets: string | null;
  points: [number, number][];
  /** (d) only: the share of sandbox time held at the sandbox's own limit. */
  throttled: [number, number][] | null;
  /** Why there is nothing to show, when that is not "it never slowed". */
  note: "no_psi" | null;
  /** (e) only: the job type whose queue it is. */
  job: string;
  x_max: number | null;
  span: number;
  knee: { at: number; slope_before: number; slope_after: number; gain: number } | null;
};

export type CapacitySummary = {
  period: {
    start_ms: number;
    end_ms: number;
    step_s: number;
    timezone: string;
    days: string[];
    full_days: number[];
    workdays: number[];
    generated_ms: number;
  };
  sources: Record<string, { connected: boolean; error: string | null; rows: number }>;
  readiness: Record<
    | "overview"
    | "allocation"
    | "person"
    | "regression"
    | "distribution"
    | "trends"
    | "whatif"
    | "runway"
    | "thresholds"
    | "confidence",
    Readiness
  >;
  people: {
    n_now: number;
    active_30d: number[];
    active_today: number[];
    /** Whether today's N rests on 30 recorded days; false: it reads low. */
    complete: boolean;
    recorded_since_ms: number | null;
  };
  lines: Line[];
  headroom: {
    n_now: number;
    max_n: number | null;
    binding: string | null;
    by_line: Record<string, number>;
    ceiling: boolean;
    /** The days the headroom drew from (workdays with data). */
    days: number;
  } | null;
  runway: {
    days: number[];
    n: number[];
    trend: Trend | null;
    disk: {
      line: string;
      trend: Trend | null;
      limit: number;
      now: number | null;
      /** The trend's growth a month ÷ today's N: one more person's disk a month. */
      per_person_month: number | null;
    }[];
  };
  thresholds: Pair[];
  confidence: {
    regressions: {
      line: string;
      r2: number | null;
      holdout_mape: number | null;
      holdout_days: number;
      columns: number;
      collinear: string[][];
    }[];
    backtest: { mape: number; days: number[]; by_line: Record<string, number> } | null;
    coverage: Record<string, number | null>;
    unknown_share: number | null;
    system_share: number | null;
    /** source → [start_ms, end_ms] runs with nothing (pods and cards). */
    gaps: Record<string, [number, number][]>;
    untagged_gpu_share: Record<string, number>;
    dropped: number;
    mape_ok: number;
  };
  /** Whether anything measures sandboxes one by one: false — nothing does on
   * this backend; null — sandbox-host decides. */
  sandbox_measured: boolean | null;
};

export type Person = {
  user: string;
  lines: {
    id: string;
    unit: Unit;
    avg: number;
    daily_avg: (number | null)[];
    daily_peak: (number | null)[];
    share: number;
    /** among people; null for the `unknown` and `system` rows */
    rank: number | null;
  }[];
  apps: string[];
  items: {
    id: string;
    sandbox_cpu_s?: number | null;
    disk_bytes?: number | null;
    scope?: string | null;
    turns?: number | null;
  }[];
  turns: number;
  jobs: number;
  active_days: number;
};

export type WhatIfProfile = "all" | "heavy" | "app";

export type WhatIf = {
  /** The N answered: the one asked, up to n_max. */
  n: number;
  n_asked: number;
  /** The furthest the what-if looks (20× today's N); null when not ready. */
  n_max: number | null;
  n_now?: number | null;
  profile: string;
  app: string;
  draws?: number | null;
  ready: boolean;
  lines: {
    id: string;
    unit: Unit;
    /** A line N cannot push: it needs what it uses at any N, and gets no verdict. */
    fixed: boolean;
    p50: number;
    p95: number;
    capacity: number | null;
    threshold: number | null;
    needed_p50: number | null;
    needed_p95: number | null;
    machines_now: number | null;
    machine_label: string;
    machines_needed: Record<string, number | null>;
    fits: Record<string, boolean | null>;
  }[];
};

/** An analysis that would load more than `capacity.max_rows` (§11). */
export class TooMuchData extends Error {
  constructor(
    readonly rows: number,
    readonly maxRows: number,
  ) {
    super(`too many rows: ${rows} > ${maxRows}`);
  }
}

async function failed(r: Response, what: string): Promise<Error> {
  if (r.status === 422) {
    const body = (await r.clone().json().catch(() => null)) as {
      detail?: { error?: string; rows?: number; max_rows?: number };
    } | null;
    if (body?.detail?.error === "too_many_rows") {
      return new TooMuchData(body.detail.rows ?? 0, body.detail.max_rows ?? 0);
    }
  }
  return httpErrorFrom(r, `${what} failed: ${r.status}`);
}

const enc = encodeURIComponent;

export const capacityApi = {
  async summary(days: CapacityDays): Promise<CapacitySummary> {
    const r = await apiFetch(`/admin/capacity?days=${days}`);
    if (!r.ok) throw await failed(r, "capacity");
    return (await r.json()) as CapacitySummary;
  },

  async person(user: string, days: CapacityDays): Promise<Person> {
    const r = await apiFetch(`/admin/capacity/person?user=${enc(user)}&days=${days}`);
    if (!r.ok) throw await failed(r, "capacity person");
    return (await r.json()) as Person;
  },

  async whatif(
    days: CapacityDays,
    n: number,
    profile: WhatIfProfile = "all",
    app = "",
  ): Promise<WhatIf> {
    const q = new URLSearchParams({ n: String(n), days: String(days), profile });
    if (profile === "app") q.set("app", app);
    const r = await apiFetch(`/admin/capacity/whatif?${q}`);
    if (!r.ok) throw await failed(r, "capacity what-if");
    return (await r.json()) as WhatIf;
  },

  async refresh(days: CapacityDays): Promise<void> {
    const r = await apiFetch(`/admin/capacity/refresh?days=${days}`, { method: "POST" });
    if (!r.ok) throw await failed(r, "capacity refresh");
  },
};

export type CapacityClient = typeof capacityApi;
