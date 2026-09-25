// @vitest-environment happy-dom
/**
 * #847/#848 P6 — `view: chart`'s thumbnail for a chat card: the live view's own
 * sandbox calls (so the cache is shared with it) drawn by the live view's own
 * renderer, small and static, once. Anything that cannot be drawn is reported
 * through `onFail`, and the host shows its plain card.
 */
import { cleanup, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { FacetIndex } from "./gallery";
import { answer, f64, layer, q8 } from "./testAnswer";

type RunData = { stdout: string; stderr: string; exit_code: number } | undefined;
type Run = { data: RunData; error: Error | null; isLoading: boolean; refetch: () => void };

const sdk = vi.hoisted(() => ({
  useSandboxRun: vi.fn(),
  viewDocument: vi.fn(),
  registerViewKind: vi.fn(),
  useMarking: vi.fn(() => [undefined, vi.fn()]),
  isLit: vi.fn(() => false),
}));
vi.mock("@aiws/view-sdk", () => sdk);

const chart = vi.hoisted(() => {
  const instance = { setOption: vi.fn(), dispose: vi.fn(), on: vi.fn(), resize: vi.fn(), dispatchAction: vi.fn() };
  return { instance, createChart: vi.fn((_el: HTMLElement) => instance) };
});
vi.mock("./echarts", () => ({ createChart: chart.createChart }));

const thumbnailSpy = vi.hoisted(() => vi.fn());
vi.mock("./gallery", async (original) => {
  const real = await original<typeof import("./gallery")>();
  thumbnailSpy.mockImplementation(real.thumbnail);
  return { ...real, thumbnail: thumbnailSpy };
});

import { ChartThumbnail } from "./Thumbnail";

const PLOT = {
  view: "chart",
  source: "data/a.csv",
  title: "a vs b",
  mark: "scatter",
  encoding: { x: { field: "a", type: "quantitative" }, y: { field: "b", type: "quantitative" } },
};
const ANSWER = answer(layer("scatter", 3, { a: f64([1, 2, 3]), b: f64([4, 5, 6]) }));

const GALLERY = {
  view: "chart",
  source: "data/w.csv",
  facet: { field: ["lot", "wafer"], sort: { field: "rate", order: "descending" } },
  mark: "grid",
  encoding: {
    x: { field: "x", type: "ordinal" },
    y: { field: "y", type: "ordinal" },
    color: { field: "v", type: "quantitative" },
  },
};
const GROUPS = 40;
const INDEX: FacetIndex = {
  build: "b".repeat(32),
  scale: { kind: "continuous", lo: 0, hi: 254 },
  facet: ["lot", "wafer"],
  cells: 1,
  layout: { x: [0], y: [0] },
  groups: Array.from({ length: GROUPS }, (_, i) => ({ key: ["L1", String(i)], sort: { rate: i } })),
  columns: [], // P4's sort menu: not what a thumbnail draws
};
const KEY = "k".repeat(64);

const ok = (out: unknown) => ({ stdout: JSON.stringify(out), stderr: "", exit_code: 0 });
let answers: Record<string, RunData | ((args: Record<string, unknown>) => RunData)>;
let errors: Record<string, Error>;
const cache = new Map<string, RunData>();

beforeEach(() => {
  vi.clearAllMocks();
  cache.clear();
  errors = {};
  answers = {
    query: ok(ANSWER),
    facet_build: ok({ key: KEY, build: INDEX.build, groups: GROUPS, cells: 1, built: true }),
    facet_index: ok(INDEX),
    facet_page: (args) =>
      ok({ build: INDEX.build, groups: (args.positions as number[]).map((p) => q8([p], 0, 254)) }),
  };
  sdk.useSandboxRun.mockImplementation(
    (_plugin: string, cmd: string, args: Record<string, unknown>, opts?: { enabled?: boolean }): Run => {
      const base = { error: null, isLoading: false, refetch: vi.fn() };
      if (opts?.enabled === false) return { ...base, data: undefined };
      if (errors[cmd]) return { ...base, error: errors[cmd], data: undefined };
      // one data object per (command, args), as the query cache hands back
      const key = `${cmd} ${JSON.stringify(args)}`;
      if (!cache.has(key)) {
        const a = answers[cmd];
        cache.set(key, typeof a === "function" ? a(args) : a);
      }
      return { ...base, data: cache.get(key) };
    },
  );
});
afterEach(cleanup);

function draw(doc: object) {
  sdk.viewDocument.mockReturnValue(doc);
  const onFail = vi.fn();
  const r = render(<ChartThumbnail spec={{} as never} path="/views/a.ai.yaml" onFail={onFail} />);
  return { ...r, onFail };
}
const calls = (cmd: string) => sdk.useSandboxRun.mock.calls.filter((c) => c[1] === cmd && c[3]?.enabled !== false);

describe("ChartThumbnail — a plot", () => {
  it("asks the sandbox exactly what the live view asks, so the two share one answer", () => {
    draw(PLOT);
    expect(calls("query")[0].slice(0, 3)).toEqual(["chart", "query", { spec: JSON.stringify(PLOT) }]);
  });

  it("draws the answer with ECharts, once, static: no brush, no toolbox, no tooltip", async () => {
    const { rerender } = draw(PLOT);
    await waitFor(() => expect(chart.instance.setOption).toHaveBeenCalled());
    rerender(<ChartThumbnail spec={{} as never} path="/views/a.ai.yaml" onFail={vi.fn()} />);
    expect(chart.createChart).toHaveBeenCalledTimes(1);
    expect(chart.instance.setOption).toHaveBeenCalledTimes(1);
    const option = chart.instance.setOption.mock.calls[0][0] as Record<string, unknown>;
    expect(option.brush).toBeUndefined();
    expect(option.toolbox).toEqual({ show: false });
    expect(option.tooltip).toEqual({ show: false });
    expect(option.animation).toBe(false);
    // the same series the live view draws
    expect((option.series as unknown[]).length).toBe(1);
  });

  it("a box too small to lay a chart out in gets the chart drawn at a workable size and scaled down to fit", async () => {
    // A layout card's pane is ~170 x 95: laid out at that size, the title,
    // legend and axes drew over each other.
    const size = { clientWidth: 160, clientHeight: 80 };
    const spies = Object.entries(size).map(([k, v]) => vi.spyOn(HTMLElement.prototype, k as never, "get").mockReturnValue(v as never));
    try {
      const { container } = draw(PLOT);
      await waitFor(() => expect(chart.createChart).toHaveBeenCalled());
      const drawnIn = chart.createChart.mock.calls[0][0];
      const w = Number.parseFloat(drawnIn.style.width);
      const h = Number.parseFloat(drawnIn.style.height);
      expect(w).toBeGreaterThanOrEqual(320);
      expect(h).toBeGreaterThanOrEqual(200);
      // scaled, it fills the box exactly, keeping its proportions
      const scale = Number(/scale\(([\d.]+)\)/.exec(drawnIn.style.transform)?.[1]);
      expect(w * scale).toBeCloseTo(160, 0);
      expect(h * scale).toBeCloseTo(80, 0);
      expect(container.firstElementChild).toHaveProperty("style.overflow", "hidden");
    } finally {
      spies.forEach((s) => s.mockRestore());
    }
  });

  it("a box big enough is drawn at its own size, unscaled", async () => {
    const size = { clientWidth: 400, clientHeight: 250 };
    const spies = Object.entries(size).map(([k, v]) => vi.spyOn(HTMLElement.prototype, k as never, "get").mockReturnValue(v as never));
    try {
      draw(PLOT);
      await waitFor(() => expect(chart.createChart).toHaveBeenCalled());
      const drawnIn = chart.createChart.mock.calls[0][0];
      expect(drawnIn.style.width).toBe("400px");
      expect(drawnIn.style.height).toBe("250px");
      expect(drawnIn.style.transform).toBe("");
    } finally {
      spies.forEach((s) => s.mockRestore());
    }
  });

  it("lets the chart go when the card unmounts", async () => {
    const { unmount } = draw(PLOT);
    await waitFor(() => expect(chart.instance.setOption).toHaveBeenCalled());
    unmount();
    expect(chart.instance.dispose).toHaveBeenCalledTimes(1);
  });

  it("a spec that does not fit fails without asking the sandbox", async () => {
    const { onFail } = draw({ ...PLOT, mark: "no-such-mark" });
    await waitFor(() => expect(onFail).toHaveBeenCalled());
    expect(calls("query")).toHaveLength(0);
    expect(chart.createChart).not.toHaveBeenCalled();
  });

  it.each([
    ["a non-zero exit", () => (answers.query = { stdout: "", stderr: "no column a", exit_code: 2 })],
    ["a refused call", () => (errors.query = new Error("could not run"))],
    ["an answer that is not JSON", () => (answers.query = { stdout: "<html>", stderr: "", exit_code: 0 })],
    ["an answer in another format", () => (answers.query = ok({ format: 99, layers: [] }))],
  ])("%s fails, and draws nothing", async (_why, arrange) => {
    arrange();
    const { onFail } = draw(PLOT);
    await waitFor(() => expect(onFail).toHaveBeenCalled());
    expect(chart.createChart).not.toHaveBeenCalled();
  });
});

describe("ChartThumbnail — a gallery", () => {
  it("builds and indexes through the gallery's own first calls, so opening it reuses them", () => {
    draw(GALLERY);
    expect(calls("facet_build")[0][2]).toEqual({ spec: JSON.stringify(GALLERY), epoch: 0 });
    expect(calls("facet_index")[0][2]).toEqual({ key: KEY, epoch: 0 });
  });

  it("asks ONE page, the first groups in the gallery's own order, and paints each with the gallery's painter", async () => {
    const { container } = draw(GALLERY);
    await waitFor(() => expect(container.querySelectorAll("canvas").length).toBeGreaterThan(0));
    const pages = calls("facet_page");
    const asked = new Set(pages.map((c) => JSON.stringify(c[2])));
    expect(asked.size).toBe(1);
    const positions = pages[0][2].positions as number[];
    // `rate` descending: the last-written groups first
    expect(positions[0]).toBe(GROUPS - 1);
    expect(positions).toEqual([...positions].sort((a, b) => b - a));
    expect(positions.length).toBeLessThan(GROUPS);
    expect(container.querySelectorAll("canvas").length).toBe(positions.length);
    expect(thumbnailSpy).toHaveBeenCalledTimes(positions.length);
  });

  it.each(["facet_build", "facet_index", "facet_page"])("a non-zero exit from %s fails", async (cmd) => {
    answers[cmd] = { stdout: "", stderr: "exit 3", exit_code: 3 };
    const { onFail, container } = draw(GALLERY);
    await waitFor(() => expect(onFail).toHaveBeenCalled());
    expect(container.querySelectorAll("canvas")).toHaveLength(0);
  });

  it("a refused call fails", async () => {
    errors.facet_build = new Error("could not run");
    const { onFail } = draw(GALLERY);
    await waitFor(() => expect(onFail).toHaveBeenCalled());
  });
});
