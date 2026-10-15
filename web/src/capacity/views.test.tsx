// @vitest-environment happy-dom
/**
 * The nine views' rules (docs/plan-capacity-usage.md §10): the headline's four
 * answers, allocation's three ways of showing fixed overhead (each adding up to
 * the same whole), the what-if's caution and the runway's date, and the
 * thresholds with the felt-most slowness first.
 */
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { translate } from "../lib/i18n";
import { renderWithQuery } from "../test/queryWrapper";

import type { CapacityClient, CapacitySummary, Person, WhatIf as WhatIfData } from "./api";
import personFixture from "./fixtures/person.json";
import summaryFixture from "./fixtures/summary.json";
import whatifFixture from "./fixtures/whatif.json";
import { Allocation, allocationRows } from "./views/Allocation";
import { headline, Overview } from "./views/Overview";
import { kneeText, Thresholds } from "./views/Thresholds";
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

function summary(): CapacitySummary {
  return structuredClone(summaryFixture) as unknown as CapacitySummary;
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

describe("the headline (view 1)", () => {
  it("names the most people today's habits hold and what runs short first", () => {
    const s = summary();
    s.headroom = { n_now: 16, max_n: 40, binding: "api.cpu", by_line: { "api.cpu": 40 }, ceiling: false };

    expect(headline(t, s)).toBe("照現在的用法，大約還能撐到 40 人（現在 16 人）。最先不夠的是API CPU。");
  });

  it("says today's people are already past the threshold", () => {
    const s = summary();
    s.headroom = { n_now: 16, max_n: 9, binding: "sandbox-host.cpu", by_line: {}, ceiling: false };

    expect(headline(t, s)).toContain("已經超過安全門檻：沙盒 CPU大約只撐得住 9 人");
  });

  it("says when no limit is in sight, and when nothing has a capacity", () => {
    const s = summary();
    s.headroom = { n_now: 16, max_n: 320, binding: "api.cpu", by_line: {}, ceiling: true };
    expect(headline(t, s)).toBe("照現在的用法，撐得住 320 人以上（現在 16 人）。");

    s.headroom = { n_now: 16, max_n: null, binding: null, by_line: {}, ceiling: false };
    expect(headline(t, s)).toContain("沒有任何一項有已知的容量");
  });

  it("says what it still needs before it can say anything", () => {
    const s = summary();
    s.readiness.whatif = { ready: false, reason: "accumulating", missing: [], have_days: 9, need_days: 14 };

    expect(headline(t, s)).toBe("已累積 9 天，需要 14 天。資料夠了就會自動出現，不必做任何事。");
  });

  it("marks the line that binds first in the table", () => {
    const s = summary();
    s.headroom = { n_now: 16, max_n: 40, binding: "api.cpu", by_line: { "api.cpu": 40 }, ceiling: false };

    render(<Overview summary={s} t={t} />);

    const row = screen.getByTestId("capacity-row-api.cpu");
    expect(row).toHaveAttribute("data-binding", "true");
    expect(row).toHaveTextContent("最先不夠");
    expect(row).toHaveTextContent("40");
  });
});

describe("allocation (view 2)", () => {
  const s = summary();
  const line = s.lines.find((l) => l.id === "api.cpu")!;
  const name = (id: string) => id;
  const groups = (mode: "separate" | "proportional" | "even") =>
    allocationRows(t, s, line, "person", "avg", mode, name);
  const sum = (rows: { value: number }[]) => rows.reduce((a, r) => a + r.value, 0);

  it("lists fixed overhead as its own row by default", () => {
    const rows = groups("separate");

    expect(rows.find((r) => r.kind === "fixed")?.value).toBeCloseTo(line.fixed!.avg);
  });

  it("spreads fixed overhead by use, or evenly — the same whole each way", () => {
    const separate = groups("separate");
    const byUse = groups("proportional");
    const even = groups("even");

    expect(byUse.find((r) => r.kind === "fixed")).toBeUndefined();
    expect(even.find((r) => r.kind === "fixed")).toBeUndefined();
    expect(sum(byUse)).toBeCloseTo(sum(separate), 6);
    expect(sum(even)).toBeCloseTo(sum(separate), 6);
  });

  it("by component lists every line of the same resource with its total", () => {
    const rows = allocationRows(t, s, line, "component", "avg", "separate", name);

    expect(rows.map((r) => r.key).sort()).toEqual(["api.cpu", "postgres.cpu", "sandbox-host.cpu", "worker.cpu"]);
  });

  it("opens one person's detail from their row", async () => {
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

  it("dates the limit by the people trend, and says when it never comes", () => {
    const s = summary();
    s.headroom = { n_now: 16, max_n: 26, binding: "api.cpu", by_line: {}, ceiling: false };
    s.runway = { ...s.runway, days: [0, 27], trend: { intercept: 6, slope: 0.5, residual_sd: 0 } };

    const at = runwayDate(s);

    // At day 27 the trend reads 19.5; 26 is 13 days further at half a person a day.
    expect(at).toEqual({ kind: "date", atMs: s.period.end_ms + 13 * 86_400_000, slope: 0.5, target: 26 });
    s.runway.trend = { intercept: 16, slope: 0, residual_sd: 0 };
    expect(runwayDate(s)).toEqual({ kind: "flat" });
    s.headroom.max_n = 10;
    expect(runwayDate(s)).toEqual({ kind: "past" });
  });

  it("lists what N people would need and whether it fits", async () => {
    const s = summary();
    renderWithQuery(<WhatIf summary={s} days={28} client={client()} t={t} />);

    const table = await screen.findByTestId("capacity-whatif-table");
    expect(within(table).getAllByRole("row").length).toBeGreaterThan(3);
    expect(table).toHaveTextContent(/放得下|不夠/);
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
});
