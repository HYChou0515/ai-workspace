// @vitest-environment happy-dom
/**
 * #847/#848 PR 5 P29: the chart a selection is made in lights by the marking,
 * as every other view on it does. With a marking by two columns (lot, wafer)
 * the brushed chart showed only ECharts' own brush visual -- every point
 * outside the box greyed -- so it showed the rows it brushed while the tables
 * showed every combination the marking lights, more. The chart here is REAL
 * ECharts (SSR); the marking is the host's real store, through the SDK double.
 */
import { act, cleanup, render } from "@testing-library/react";
import * as echarts from "echarts/core";
import { SVGRenderer } from "echarts/renderers";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { MarkingProvider } from "../../../../web/src/hooks/useMarking";
import { MarkingStore } from "../../../../web/src/lib/markings";
import { DIM_OPACITY } from "./highlight";
import { answer, cat, f64, layer } from "./testAnswer";

const sdk = vi.hoisted(() => ({
  useSandboxRun: vi.fn(),
  viewDocument: vi.fn((s: { __doc: unknown }) => s.__doc),
  registerViewKind: vi.fn(),
}));
vi.mock("@aiws/view-sdk", async () => {
  const hooks = await import("../../../../web/src/hooks/useMarking");
  const lib = await import("../../../../web/src/lib/markings");
  return { ...sdk, useMarking: hooks.useMarking, useMarkingNames: hooks.useMarkingNames, isLit: lib.isLit };
});

// The chart's own module (its series, components and line brush selector),
// with the one instance it makes kept for the test: SSR, no DOM to draw in.
const made = vi.hoisted(() => ({ charts: [] as import("echarts/core").ECharts[] }));
vi.mock("./echarts", async (original) => {
  const real = await original<typeof import("./echarts")>();
  const core = await import("echarts/core");
  const { SVGRenderer: svg } = await import("echarts/renderers");
  core.use([svg]);
  return {
    ...real,
    createChart: () => {
      const chart = core.init(null, null, { renderer: "svg", ssr: true, width: 600, height: 400 });
      made.charts.push(chart);
      return chart;
    },
  };
});

import { ChartView } from "./ChartView";

echarts.use([SVGRenderer]);

// The chart debounces brush events (throttleDelay 250 ms in the option).
const settle = () => act(() => new Promise((r) => setTimeout(r, 400)));

// Rows 0..3: (L1,1) (L1,2) (L2,1) (L2,2), at x 1..4; row 4 (L3,3) at x 5.
const ANSWER = answer(
  layer("scatter", 5, {
    a: f64([1, 2, 3, 4, 5]),
    b: f64([1, 1, 1, 1, 1]),
    lot: cat(["L1", "L1", "L2", "L2", "L3"]),
    wafer: cat(["1", "2", "1", "2", "3"]),
  }),
);
const DOC = {
  view: "chart",
  source: "data/a.csv",
  keys: ["lot", "wafer"],
  mark: "scatter",
  encoding: { x: { field: "a", type: "quantitative" }, y: { field: "b", type: "quantitative" } },
};

function mount(store: MarkingStore, marking: string | null, doc: object = DOC) {
  const el = (m: string | null) => (
    <MarkingProvider store={store}>
      <ChartView spec={{ __doc: doc } as never} marking={m} path="/v/a.ai.yaml" type={null} entities={[]} onCreate={() => {}} onPatch={() => {}} />
    </MarkingProvider>
  );
  const view = render(el(marking));
  return { rerender: (m: string | null) => view.rerender(el(m)), chart: made.charts.at(-1)! };
}

/** Per row (the one series' points), how the chart draws it. */
function drawn(chart: echarts.ECharts): string[] {
  type Data = { count(): number; getItemVisual(i: number, k: "style"): { fill?: string; opacity?: number } };
  const model = (chart as unknown as { getModel(): { getSeriesByIndex(i: number): { getData(): Data } } }).getModel();
  const data = model.getSeriesByIndex(0).getData();
  return Array.from({ length: data.count() }, (_, i) => {
    const style = data.getItemVisual(i, "style");
    if (style.fill === "#ddd") return "brush-grey";
    return style.opacity === DIM_OPACITY ? "dim" : "lit";
  });
}

/** A brush over x 0.5..1.5 and 3.5..4.5: rows 0 (L1,1) and 3 (L2,2). */
async function brush(chart: echarts.ECharts) {
  act(() =>
    chart.dispatchAction({
      type: "brush",
      areas: [
        { brushType: "rect", xAxisIndex: 0, coordRange: [[0.5, 1.5], [0, 2]] },
        { brushType: "rect", xAxisIndex: 0, coordRange: [[3.5, 4.5], [0, 2]] },
      ],
    }),
  );
  await settle();
}

beforeEach(() => {
  made.charts.length = 0;
  const stdout = JSON.stringify(ANSWER);
  sdk.useSandboxRun.mockReturnValue({ data: { stdout, stderr: "", exit_code: 0 }, error: null, isLoading: false, refetch: vi.fn() });
});
afterEach(() => {
  cleanup();
  for (const c of made.charts) c.dispose();
});

describe("the chart a selection is made in", () => {
  it("lights what the marking lights, not only the rows its brush took (red before: brush-grey)", async () => {
    const store = new MarkingStore();
    const { chart } = mount(store, "fail");
    await brush(chart);
    // the brush wrote lot {L1, L2} x wafer {1, 2}: all four combinations light
    expect(Object.fromEntries(Object.entries(store.get("fail")!.marking).map(([k, v]) => [k, [...v].sort()]))).toEqual({
      lot: ["L1", "L2"],
      wafer: ["1", "2"],
    });
    expect(drawn(chart)).toEqual(["lit", "lit", "lit", "lit", "dim"]);
    // the box the person drew stays, to see and to clear
    const model = (chart as unknown as { getModel(): { getComponent(main: string): { areas: unknown[] } } }).getModel();
    expect(model.getComponent("brush").areas).toHaveLength(2);
  });

  it("on no marking keeps showing its own brush selection", async () => {
    const { chart } = mount(new MarkingStore(), null);
    await brush(chart);
    expect(drawn(chart)).toEqual(["lit", "brush-grey", "brush-grey", "lit", "brush-grey"]);
  });

  it("on a marking it cannot write (no `keys:`) keeps showing its own brush selection", async () => {
    const store = new MarkingStore();
    const { chart } = mount(store, "fail", { ...DOC, keys: undefined });
    await brush(chart);
    expect(store.get("fail")).toBeUndefined();
    expect(drawn(chart)).toEqual(["lit", "brush-grey", "brush-grey", "lit", "brush-grey"]);
  });

  it("shows its brush selection again once detached from the marking it wrote", async () => {
    const store = new MarkingStore();
    const { chart, rerender } = mount(store, "fail");
    await brush(chart);
    rerender(null);
    await settle();
    expect(drawn(chart)).toEqual(["lit", "brush-grey", "brush-grey", "lit", "brush-grey"]);
  });
});
