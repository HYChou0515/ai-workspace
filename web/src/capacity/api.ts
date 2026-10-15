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
  reason: "source_missing" | "accumulating" | "too_few_people" | "no_regression" | null;
  missing: string[];
  have_days: number;
  need_days: number;
};

export type Stat = { avg: number; peak: number };
export type Group = { key: string; avg: number; peak: number };

export type Line = {
  id: string;
  component: string;
  resource: string;
  unit: Unit;
  model: string;
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
    | "distribution"
    | "trends"
    | "whatif"
    | "runway"
    | "thresholds"
    | "confidence",
    Readiness
  >;
  people: { n_now: number; active_30d: number[]; active_today: number[] };
  lines: Line[];
  headroom: {
    n_now: number;
    max_n: number | null;
    binding: string | null;
    by_line: Record<string, number>;
    ceiling: boolean;
  } | null;
  runway: {
    days: number[];
    n: number[];
    trend: Trend | null;
    disk: { line: string; trend: Trend | null; limit: number; now: number | null }[];
  };
  thresholds: Pair[];
  confidence: {
    regressions: {
      line: string;
      r2: number | null;
      holdout_mape: number | null;
      columns: number;
      collinear: string[][];
    }[];
    backtest: { mape: number; days: number[]; by_line: Record<string, number> } | null;
    coverage: Record<string, number | null>;
    unknown_share: number | null;
    untagged_gpu_share: Record<string, number>;
    dropped: number;
    mape_ok: number;
  };
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
    rank: number;
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
  n: number;
  n_now?: number | null;
  profile: string;
  app: string;
  draws?: number | null;
  ready: boolean;
  lines: {
    id: string;
    unit: Unit;
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
