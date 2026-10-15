// @vitest-environment happy-dom
/**
 * The nine views' rules (docs/plan-capacity-usage.md §10): the headline's four
 * answers and what they rest on, what a view still needs, allocation's three
 * ways of showing fixed overhead (each adding up to the same whole), the
 * what-if's caution and the runway's dates, and the thresholds with the
 * felt-most slowness first.
 */
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { translate } from "../lib/i18n";
import { renderWithQuery } from "../test/queryWrapper";

import type { CapacityClient, CapacitySummary, Person, Readiness, WhatIf as WhatIfData } from "./api";
import personFixture from "./fixtures/person.json";
import summaryFixture from "./fixtures/summary.json";
import whatifFixture from "./fixtures/whatif.json";
import { readinessText } from "./Readiness";
import { Allocation, allocationRows, modeApplies } from "./views/Allocation";
import { headline, Overview } from "./views/Overview";
import { columnLabel, kneeText, pairTitle, Thresholds } from "./views/Thresholds";
import { advisory, runwayDate, WhatIf } from "./views/WhatIf";

vi.mock("./echarts", () => ({
  echarts: { init: () => ({ setOption: () => {}, resize: () => {}, dispose: () => {} }) },
}));
vi.mock("../hooks/useUsers", () => ({
  useUsers: () => [{ id: "user00", name: "Alice Chen", section: "", email: "" }],
  useUser: (id: string) => ({ id, name: id, section: "", email: "" }),
}));

const t = (key: Parameters<typeof translate>[1], vars?: Record<string, string | number>) =>
  translate("zh-TW", key, vars);
const en = (key: Parameters<typeof translate>[1], vars?: Record<string, string | number>) =>
  translate("en", key, vars);

const DAY = 86_400_000;

function summary(): CapacitySummary {
  return structuredClone(summaryFixture) as unknown as CapacitySummary;
}

function room(over: Partial<NonNullable<CapacitySummary["headroom"]>> = {}): NonNullable<CapacitySummary["headroom"]> {
  return { n_now: 16, max_n: 40, binding: "api.cpu", by_line: { "api.cpu": 40 }, ceiling: false, days: 20, ...over };
}

function notReady(over: Partial<Readiness>): Readiness {
  return {
    ready: false,
    reason: "accumulating",
    missing: [],
    error: null,
    have_days: 0,
    need_days: 0,
    period_days: 28,
    ...over,
  };
}

function client(): CapacityClient {
  return {
    summary: vi.fn(async () => summary()),
    person: vi.fn(async () => personFixture as unknown as Person),
    whatif: vi.fn(async () => whatifFixture as unknown as WhatIfData),
    refresh: vi.fn(async () => {}),
  } as CapacityClient;
}

afterEach(cleanup);

describe("what a view still needs (§9.10)", () => {
  it("a period too short for the view asks for the shortest one that holds it", () => {
    expect(readinessText(t, notReady({ reason: "period_too_short", need_days: 14, period_days: 7 }))).toBe(
      "這裡需要至少 14 天，這段期間只有 7 天：請改看 28 天。",
    );
  });

  it("a failing source is named with what it raised, not called unconnected", () => {
    const text = readinessText(
      t,
      notReady({ reason: "source_error", missing: ["resource_metrics"], error: "ConnectionError: refused" }),
    );

    expect(text).toContain("pod 與機器用量");
    expect(text).toContain("ConnectionError: refused");
  });

  it("lists sources in the language's own form", () => {
    const r = notReady({ reason: "source_missing", missing: ["usage_records", "resource_metrics"] });

    expect(readinessText(en, r)).toBe(
      "Not connected yet: Platform usage records, Pod and machine use. This appears once they are.",
    );
  });
});

describe("the headline (view 1)", () => {
  it("names the most people today's habits hold and what runs short first", () => {
    const s = summary();
    s.headroom = room();

    expect(headline(t, s)).toBe("照現在的用法，大約還能撐到 40 人（現在 16 人）。最先不夠的是API CPU。");
  });

  it("says today's people are already past the threshold", () => {
    const s = summary();
    s.headroom = room({ max_n: 9, binding: "sandbox-host.cpu", by_line: {} });

    expect(headline(t, s)).toContain("已經超過安全門檻：沙盒 CPU大約只撐得住 9 人");
  });

  it("says when no limit is in sight, and when nothing has a capacity", () => {
    const s = summary();
    s.headroom = room({ max_n: 320, ceiling: true, by_line: {} });
    expect(headline(t, s)).toBe("照現在的用法，撐得住 320 人以上（現在 16 人）。");

    s.headroom = room({ max_n: null, binding: null, by_line: {} });
    expect(headline(t, s)).toContain("沒有任何一項有已知的容量");
  });

  it("says what it still needs before it can say anything", () => {
    const s = summary();
    s.readiness.whatif = notReady({ have_days: 9, need_days: 14 });

    expect(headline(t, s)).toBe("已累積 9 天，需要 14 天。資料夠了就會自動出現，不必做任何事。");
  });

  it("says what the headroom rests on: the workdays it drew from", () => {
    const s = summary();
    s.headroom = room({ days: 17 });

    render(<Overview summary={s} t={t} />);

    expect(screen.getByText(/依據：17 個有紀錄的工作天/)).toBeInTheDocument();
  });

  it("marks the line that binds first in the table", () => {
    const s = summary();
    s.headroom = room();

    render(<Overview summary={s} t={t} />);

    const row = screen.getByTestId("capacity-row-api.cpu");
    expect(row).toHaveAttribute("data-binding", "true");
    expect(row).toHaveTextContent("最先不夠");
    expect(row).toHaveTextContent("40");
  });

  it("warns while the records are younger than the 30 days N counts over", () => {
    const s = summary();
    s.people.complete = false;
    s.people.recorded_since_ms = s.period.end_ms - 12 * DAY;

    render(<Overview summary={s} t={t} />);

    expect(screen.getByTestId("capacity-people-incomplete")).toHaveTextContent("開始記錄才 12 天");
  });

  it("does not warn once 30 days are recorded", () => {
    const s = summary();
    s.people.complete = true;
    s.people.recorded_since_ms = null;

    render(<Overview summary={s} t={t} />);

    expect(screen.queryByTestId("capacity-people-incomplete")).toBeNull();
  });

  it("says sandbox use is not measured on a backend that cannot", () => {
    const s = summary();
    s.sandbox_measured = false;
    const { unmount } = render(<Overview summary={s} t={t} />);
    expect(screen.getByTestId("capacity-sandbox-unmeasured")).toHaveTextContent("量不到每個沙盒的用量");
    unmount();

    s.sandbox_measured = null;
    render(<Overview summary={s} t={t} />);
    expect(screen.queryByTestId("capacity-sandbox-unmeasured")).toBeNull();
  });

  it("calls a default threshold 'not seen to slow' only where a pair watches the line", () => {
    const s = summary();
    for (const id of ["api.cpu", "postgres.memory"]) {
      const line = s.lines.find((l) => l.id === id)!;
      line.threshold = 0.7;
      line.threshold_source = "default";
    }

    render(<Overview summary={s} t={t} />);

    expect(screen.getByTestId("capacity-row-api.cpu")).toHaveTextContent("預設值（尚未觀察到變慢）");
    const memory = screen.getByTestId("capacity-row-postgres.memory");
    expect(memory).toHaveTextContent("預設值");
    expect(memory).not.toHaveTextContent("尚未觀察到變慢");
  });
});

describe("allocation (view 2)", () => {
  const name = (id: string) => id;
  const sum = (rows: { value: number }[]) => rows.reduce((a, r) => a + r.value, 0);

  function sandbox(s: CapacitySummary) {
    return s.lines.find((l) => l.id === "sandbox-host.cpu")!;
  }

  it("lists fixed overhead as its own row by default", () => {
    const s = summary();
    const line = sandbox(s);

    const rows = allocationRows(t, s, line, "person", "avg", "separate", name);

    expect(rows.find((r) => r.kind === "fixed")?.value).toBeCloseTo(line.fixed!.avg);
  });

  it("spreads fixed overhead by use, or evenly — the same whole each way", () => {
    const s = summary();
    const line = sandbox(s);
    const rows = (mode: "separate" | "proportional" | "even") =>
      allocationRows(t, s, line, "person", "avg", mode, name);

    expect(rows("proportional").find((r) => r.key === "__fixed")).toBeUndefined();
    expect(sum(rows("proportional"))).toBeCloseTo(sum(rows("separate")), 6);
    expect(sum(rows("even"))).toBeCloseTo(sum(rows("separate")), 6);
  });

  it("evenly spreads over the people active in the last 30 days, the ones without use in a row", () => {
    const s = summary();
    const line = sandbox(s);
    const listed = line.groups.person.filter((g) => !["unknown", "system", ""].includes(g.key)).length;
    s.people.n_now = listed + 3;

    const rows = allocationRows(t, s, line, "person", "avg", "even", name);

    const each = line.fixed!.avg / (listed + 3);
    for (const r of rows.filter((x) => x.kind === "group")) expect(r.carried).toBeCloseTo(each);
    const rest = rows.find((r) => r.key === "__rest")!;
    expect(rest.label).toBe("其他 3 位活躍的人（這一項沒有用量）");
    expect(rest.value).toBeCloseTo(3 * each);
    expect(sum(rows)).toBeCloseTo(sum(allocationRows(t, s, line, "person", "avg", "separate", name)), 6);
  });

  it("offers evenly for people only — an App is not a head", () => {
    const s = summary();

    expect(modeApplies("person", "even")).toBe(true);
    expect(modeApplies("app", "even")).toBe(false);
    expect(modeApplies("app", "proportional")).toBe(true);
    const rows = allocationRows(t, s, sandbox(s), "app", "avg", "even", name);
    expect(rows.find((r) => r.key === "__fixed")).toBeDefined();
  });

  it("by component lists every line of the same resource with its total", () => {
    const s = summary();

    const rows = allocationRows(t, s, sandbox(s), "component", "avg", "separate", name);

    expect(rows.map((r) => r.key).sort()).toEqual(["api.cpu", "postgres.cpu", "sandbox-host.cpu", "worker.index.cpu"]);
  });

  it("holds a line split by regression back until three days of both sources fit it", () => {
    const s = summary();
    s.readiness.regression = notReady({ have_days: 2, need_days: 3 });

    renderWithQuery(<Allocation summary={s} days={28} client={client()} t={t} />);

    // api.cpu, the first line, is split by regression
    expect(screen.getByText("已累積 2 天，需要 3 天。資料夠了就會自動出現，不必做任何事。")).toBeInTheDocument();
    expect(screen.queryByTestId("capacity-alloc-table")).toBeNull();
  });

  it("opens one person's detail from their row", async () => {
    const s = summary();
    const c = client();
    renderWithQuery(<Allocation summary={s} days={28} client={c} t={t} />);

    fireEvent.click(await screen.findByRole("button", { name: "Alice Chen" }));

    const detail = await screen.findByTestId("capacity-person");
    await waitFor(() => expect(c.person).toHaveBeenCalledWith("user00", 28));
    expect(within(detail).getByText("Alice Chen 的用量")).toBeInTheDocument();
  });
});

describe("what-if and runway (views 6, 7)", () => {
  it("is only indicative when the backtest missed by more than allowed", () => {
    const s = summary();
    s.confidence.backtest = { mape: 0.31, days: [20, 21], by_line: {} };
    expect(advisory(s)).toBe(0.31);

    s.confidence.backtest = { mape: 0.05, days: [20, 21], by_line: {} };
    expect(advisory(s)).toBeNull();
  });

  it("dates the limit by the people trend, within its 80% band", () => {
    const s = summary();
    const today = s.period.days.length - 1;
    s.headroom = room({ max_n: 26 });
    // 20 people today by the trend, half a person a day, ±2 people of spread
    s.runway = { ...s.runway, trend: { intercept: 20 - 0.5 * today, slope: 0.5, residual_sd: 2 } };

    const at = runwayDate(s);

    const band = 1.2816 * 2;
    expect(at.kind).toBe("date");
    if (at.kind !== "date") return;
    expect(at.atMs).toBeCloseTo(s.period.end_ms + 12 * DAY, -3);
    expect(at.earlyMs).toBeCloseTo(s.period.end_ms + ((26 - 20 - band) / 0.5) * DAY, -3);
    expect(at.lateMs).toBeCloseTo(s.period.end_ms + ((26 - 20 + band) / 0.5) * DAY, -3);
  });

  it("gives no date to order by when the headroom is only 'at least'", () => {
    const s = summary();
    s.headroom = room({ max_n: 320, ceiling: true });
    s.runway = { ...s.runway, trend: { intercept: 16, slope: 0.5, residual_sd: 0 } };

    expect(runwayDate(s).kind).toBe("beyond");
  });

  it("is past the limit only when today's people exceed it, and flat when nobody joins", () => {
    const s = summary();
    s.runway = { ...s.runway, trend: { intercept: 16, slope: 0.5, residual_sd: 0 } };
    s.headroom = room({ n_now: 16, max_n: 16 });
    expect(runwayDate(s).kind).toBe("date");

    s.headroom = room({ n_now: 16, max_n: 10 });
    expect(runwayDate(s).kind).toBe("past");

    s.headroom = room();
    s.runway.trend = { intercept: 16, slope: 0, residual_sd: 0 };
    expect(runwayDate(s).kind).toBe("flat");
  });

  it("lists what N people would need and whether it fits", async () => {
    const s = summary();
    renderWithQuery(<WhatIf summary={s} days={28} client={client()} t={t} />);

    const table = await screen.findByTestId("capacity-whatif-table");
    expect(within(table).getAllByRole("row").length).toBeGreaterThan(3);
    expect(table).toHaveTextContent(/放得下|不夠/);
  });

  it("says which N it answered when the one asked was beyond its reach", async () => {
    const s = summary();
    const c = client();
    c.whatif = vi.fn(async () => ({ ...(whatifFixture as unknown as WhatIfData), n: 320, n_asked: 100000 }));
    renderWithQuery(<WhatIf summary={s} days={28} client={c} t={t} />);

    expect(await screen.findByTestId("capacity-whatif-capped")).toHaveTextContent(
      "100000 人超出試算的範圍：試算最多看到現在人數的 20 倍，這裡算的是 320 人。",
    );
  });

  it("counts GPUs in cards", async () => {
    const s = summary();
    const c = client();
    const data = structuredClone(whatifFixture) as unknown as WhatIfData;
    const gpu = data.lines.find((l) => l.unit === "inflight")!;
    gpu.machines_needed = { p50: 3, p95: 4 };
    gpu.machines_now = 2;
    c.whatif = vi.fn(async () => data);
    renderWithQuery(<WhatIf summary={s} days={28} client={c} t={t} />);

    const table = await screen.findByTestId("capacity-whatif-table");
    expect(table).toHaveTextContent("4 張（現在 2）");
  });
});

describe("thresholds and confidence (views 8, 9)", () => {
  it("says where it slows down, and which line that sets", () => {
    const s = summary();
    const pair = s.thresholds.find((p) => p.id === "a")!;
    pair.knee = { at: 0.35, slope_before: 1, slope_after: 9, gain: 0.5 };

    expect(kneeText(t, s, pair)).toBe("超過 35% 就明顯變慢，這就是API CPU的安全門檻。");
    pair.knee = null;
    pair.span = 0.6;
    pair.x_max = 0.62;
    expect(kneeText(t, s, pair)).toBe("尚未觀察到變慢（最高到 62%）。");
  });

  it("calls a model's knee its capacity, not a threshold", () => {
    const s = summary();
    const pair = s.thresholds.find((p) => p.id === "gpu")!;
    pair.knee = { at: 0.7, slope_before: 0, slope_after: 3, gain: 0.9 };

    expect(kneeText(t, s, pair)).toBe("超過 0.7 就明顯變慢，這就是GPU qwen3的容量。");
  });

  it("says a kernel without pressure cannot show sandboxes waiting", () => {
    const s = summary();
    const pair = s.thresholds.find((p) => p.id === "d")!;
    pair.note = "no_psi";

    expect(kneeText(t, s, pair)).toContain("沒有提供 CPU 壓力（PSI）");
  });

  it("names (b) per model and (e) per job type", () => {
    const s = summary();

    const b = s.thresholds.find((p) => p.id === "b" && p.model === "qwen3")!;
    const e = s.thresholds.find((p) => p.id === "e")!;

    expect(pairTitle(t, b)).toBe("送出到 AI 開始回覆（p95） vs 同時處理的請求（qwen3）");
    expect(pairTitle(t, e)).toBe("背景工作排隊時間（p95） vs 背景工作 CPU 使用率（文件索引）");
  });

  it("puts the slowness felt most first, marked", () => {
    const s = summary();
    render(<Thresholds summary={s} t={t} />);

    const cards = screen.getAllByTestId(/^capacity-pair-/).map((el) => el.dataset.testid);
    expect(cards.slice(0, 2)).toEqual(["capacity-pair-a", "capacity-pair-b"]);
    expect(within(screen.getByTestId("capacity-pair-a")).getByText("你們最有感")).toBeInTheDocument();
  });

  it("reports the backtest against its bar", () => {
    const s = summary();
    s.confidence.backtest = { mape: 0.31, days: [1, 2, 3], by_line: {} };
    render(<Thresholds summary={s} t={t} />);

    expect(screen.getByTestId("capacity-backtest")).toHaveTextContent("超過 20%：試算和時程僅供參考。");
  });

  it("lists the gaps, the system share and each regression's held-out days", () => {
    const s = summary();
    s.confidence.gaps = { resource_metrics: [[s.period.start_ms, s.period.start_ms + 3_600_000]], gpu_metrics: [] };
    s.confidence.system_share = 0.04;
    s.confidence.regressions = [{ line: "api.cpu", r2: 0.9, holdout_mape: 0.05, holdout_days: 7, columns: 3, collinear: [] }];

    render(<Thresholds summary={s} t={t} />);

    const conf = screen.getByTestId("capacity-confidence");
    expect(within(conf).getAllByTestId("capacity-gap")).toHaveLength(1);
    expect(conf).toHaveTextContent("4.0% 的請求是系統工作");
    expect(conf).toHaveTextContent("用之前的日子預測最後 7 天");
  });

  it("names regression columns in words, not as route templates", () => {
    expect(columnLabel(t, "req:GET /api/a/{slug}/items/{item_id}/files")).toBe("GET …/{item_id}/files");
    expect(columnLabel(t, "req:(other POST)")).toBe("其他 POST 請求");
    expect(columnLabel(t, "job:index")).toBe("文件索引工作");
    expect(columnLabel(t, "turn-events:chat")).toBe("對話的回覆事件");
  });
});
