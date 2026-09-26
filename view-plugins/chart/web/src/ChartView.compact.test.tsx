// @vitest-environment happy-dom
/**
 * #847/#848 PR 5 P31: ChartView lays a chart out compact from the width it
 * MEASURES (`compactAt`), and back when the pane widens. The chart is real
 * ECharts (SSR); the ResizeObserver is a double that reports what a browser
 * would.
 */
import { act, cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { MarkingProvider } from "../../../../web/src/hooks/useMarking";
import { MarkingStore } from "../../../../web/src/lib/markings";
import { COMPACT_BELOW } from "./option";
import { answer, f64, layer, q8 } from "./testAnswer";

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

// A ResizeObserver double: `resize(w)` tells every observer its element is now w px wide.
const realRO = globalThis.ResizeObserver;
let resize: (width: number) => void = () => {};
beforeEach(() => {
  made.charts.length = 0;
  const observers: { cb: ResizeObserverCallback; els: Element[] }[] = [];
  globalThis.ResizeObserver = class {
    entry: { cb: ResizeObserverCallback; els: Element[] };
    constructor(cb: ResizeObserverCallback) {
      this.entry = { cb, els: [] };
      observers.push(this.entry);
    }
    observe(el: Element) {
      this.entry.els.push(el);
    }
    unobserve() {}
    disconnect() {
      this.entry.els = [];
    }
  } as unknown as typeof ResizeObserver;
  resize = (width) =>
    act(() => {
      for (const o of observers) {
        const entries = o.els.map((target) => ({ target, contentRect: { width, height: 400 } }) as unknown as ResizeObserverEntry);
        if (entries.length) o.cb(entries, {} as ResizeObserver);
      }
    });
});
afterEach(() => {
  globalThis.ResizeObserver = realRO;
  cleanup();
});

const GRID = {
  view: "chart",
  source: "data/a.csv",
  mark: "grid",
  encoding: { x: { field: "x", type: "ordinal" }, y: { field: "y", type: "ordinal" }, color: { field: "v", type: "quantitative" } },
};
const CELLS = answer(layer("grid", 4, { x: f64([0, 1, 0, 1]), y: f64([0, 0, 1, 1]), v: q8([0, 80, 160, 254], 0, 1) }));

function mount() {
  sdk.useSandboxRun.mockReturnValue({ data: { stdout: JSON.stringify(CELLS), stderr: "", exit_code: 0 }, error: null, isLoading: false, refetch: vi.fn() });
  render(
    <MarkingProvider store={new MarkingStore()}>
      <ChartView spec={{ __doc: GRID } as never} marking={null} path="/v/a.ai.yaml" type={null} entities={[]} onCreate={() => {}} onPatch={() => {}} />
    </MarkingProvider>,
  );
  return made.charts.at(-1)!;
}

/** Where the chart as drawn places its colour bar, and the plot's left margin. */
function layoutOf(chart: import("echarts/core").ECharts) {
  type Box = { top?: unknown; bottom?: unknown; left?: unknown; right?: unknown; itemHeight?: unknown };
  const option = chart.getOption() as { visualMap: Box[]; grid: Box[]; yAxis: { nameLocation?: unknown }[] };
  const { top, bottom, left, right, itemHeight } = option.visualMap[0]!;
  return { bar: { top, bottom, left, right, length: itemHeight }, plotLeft: option.grid[0]!.left, yName: option.yAxis[0]!.nameLocation };
}

// under the plot at its left, or beside it at the middle of its right -- and
// never both: merged into the other, a layout kept the positions it did not set
const UNDER = { bar: { top: null, bottom: 4, left: 8, right: null, length: 40 }, plotLeft: 8, yName: "end" };
const BESIDE = { bar: { top: "middle", bottom: null, left: null, right: 8, length: null }, plotLeft: 48, yName: "middle" };

describe("a chart lays itself out by the width it measures", () => {
  it("compact in a narrow pane: the colour bar under the plot", () => {
    const chart = mount();
    resize(139);
    expect(layoutOf(chart)).toEqual(UNDER);
  });

  it("wide again when the pane widens, and compact once more when it narrows", () => {
    const chart = mount();
    resize(139);
    resize(COMPACT_BELOW);
    expect(layoutOf(chart)).toEqual(BESIDE);
    resize(COMPACT_BELOW - 1);
    expect(layoutOf(chart)).toEqual(UNDER);
  });

  it("wide in a wide pane", () => {
    const chart = mount();
    resize(600);
    expect(layoutOf(chart)).toEqual(BESIDE);
  });
});
