// @vitest-environment happy-dom
/**
 * #847/#848 PR 5 P34 row 2: crossing `COMPACT_BELOW` is a LAYOUT switch, not
 * a rebuild. It rebuilt the chart in full (`setOption(option, true)`), which
 * dropped the brush, the "N selected" count, the legend's hidden series and a
 * pie's or grid's own lit pick -- while the pie's remembered click survived
 * and swallowed its next click. Only a change of doc or answer is a full
 * rebuild; a layout switch replaces the layout alone; a 0 px width is no
 * width. The chart is real ECharts (SSR); the ResizeObserver is a double; the
 * marking is the host's real store.
 */
import { act, cleanup, render, screen } from "@testing-library/react";
import type * as echarts from "echarts/core";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { MarkingProvider } from "../../../../web/src/hooks/useMarking";
import { MarkingStore } from "../../../../web/src/lib/markings";
import { DIM_OPACITY } from "./highlight";
import { type Answer } from "./option";
import { answer, cat, f64, layer, q8 } from "./testAnswer";
import { clickAt, sliceAt } from "./testGesture";

const sdk = vi.hoisted(() => ({
  useSandboxRun: vi.fn(),
  viewDocument: vi.fn((s: { __doc: unknown }) => s.__doc),
  registerViewKind: vi.fn(),
}));
// Every render of a Plot calls useMarking once: counting the calls counts renders.
const renders = vi.hoisted(() => ({ n: 0 }));
vi.mock("@aiws/view-sdk", async () => {
  const hooks = await import("../../../../web/src/hooks/useMarking");
  const lib = await import("../../../../web/src/lib/markings");
  const useMarking = (name: string | null) => {
    renders.n += 1;
    return hooks.useMarking(name);
  };
  return { ...sdk, useMarking, useMarkingNames: hooks.useMarkingNames, isLit: lib.isLit };
});

// The chart's own module, with each instance kept for the test: SSR at a fixed
// 600 x 400 (its `resize` does nothing: the layout is chosen from the width
// the observer reports, which is what is under test).
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
      chart.resize = () => {};
      made.charts.push(chart);
      return chart;
    },
  };
});

// What a grid's raster is painted with, observed at the one call that paints it.
const painted = vi.hoisted(() => ({ lits: [] as (boolean[] | undefined)[] }));
vi.mock("./raster", async (original) => {
  const real = await original<typeof import("./raster")>();
  return {
    ...real,
    paintCells: (...args: Parameters<typeof real.paintCells>) => {
      painted.lits.push(args[2] ? [...args[2]] : undefined);
      return real.paintCells(...args);
    },
  };
});

import { ChartView } from "./ChartView";

// A ResizeObserver double: `resize(w, h)` tells every observer its element is now w x h.
const realRO = globalThis.ResizeObserver;
let resize: (width: number, height?: number) => void = () => {};
beforeEach(() => {
  made.charts.length = 0;
  painted.lits.length = 0;
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
  resize = (width, height = 400) =>
    act(() => {
      for (const o of observers) {
        const entries = o.els.map((target) => ({ target, contentRect: { width, height } }) as unknown as ResizeObserverEntry);
        if (entries.length) o.cb(entries, {} as ResizeObserver);
      }
    });
});
afterEach(() => {
  globalThis.ResizeObserver = realRO;
  cleanup();
  for (const c of made.charts) c.dispose();
});

function mount(doc: object, a: Answer, marking: string | null = null, store = new MarkingStore()) {
  sdk.useSandboxRun.mockReturnValue({ data: { stdout: JSON.stringify(a), stderr: "", exit_code: 0 }, error: null, isLoading: false, refetch: vi.fn() });
  const view = (d: object) => (
    <MarkingProvider store={store}>
      <ChartView spec={{ __doc: d } as never} marking={marking} path="/v/a.ai.yaml" type={null} entities={[]} onCreate={() => {}} onPatch={() => {}} />
    </MarkingProvider>
  );
  const { rerender } = render(view(doc));
  return { chart: made.charts.at(-1)!, store, redraw: (d: object) => rerender(view(d)) };
}

// The chart debounces brush events (throttleDelay 250 ms in the option).
const settle = () => act(() => new Promise((r) => setTimeout(r, 400)));

function pixels(chart: echarts.ECharts) {
  const at = (p: number[]) => chart.convertToPixel({ gridIndex: 0 }, p) as number[];
  return (x: number, y: number) => at([x, y]);
}

const selectedText = () => screen.queryByText(/selected/)?.textContent ?? null;
const opacities = (chart: echarts.ECharts) => {
  type Data = { count(): number; getItemVisual(i: number, k: "style"): { opacity?: number } };
  type Model = { getSeriesByIndex(i: number): { getData(): Data } };
  const data = (chart as unknown as { getModel(): Model }).getModel().getSeriesByIndex(0).getData();
  return Array.from({ length: data.count() }, (_, i) => data.getItemVisual(i, "style").opacity ?? 1);
};
type Opt = { legend: { selected?: Record<string, boolean> }[] };
const optionOf = (chart: echarts.ECharts) => chart.getOption() as unknown as Opt;
/** The areas the brush holds (on its model: `getOption` does not carry them). */
const brushAreas = (chart: echarts.ECharts) =>
  (chart as unknown as { getModel(): { getComponent(m: string): { areas: unknown[] } } }).getModel().getComponent("brush").areas.length;

// Four points in two groups, A (x 1, 2) and B (x 3, 4), each its own item.
const SCATTER = {
  view: "chart",
  source: "data/a.csv",
  keys: ["item"],
  mark: "scatter",
  encoding: {
    x: { field: "x", type: "quantitative" },
    y: { field: "y", type: "quantitative" },
    color: { field: "group", type: "nominal" },
  },
};
const POINTS = answer(
  layer("scatter", 4, { x: f64([1, 2, 3, 4]), y: f64([1, 2, 3, 4]), group: cat(["A", "A", "B", "B"]), item: cat(["i1", "i2", "i3", "i4"]) }),
);

const PIE = {
  view: "chart",
  source: "data/a.csv",
  keys: ["group"],
  mark: "pie",
  encoding: { theta: { field: "n", type: "quantitative" }, color: { field: "group", type: "nominal" } },
};
const SLICES = answer(layer("pie", 3, { n: f64([5, 3, 2]), group: cat(["G1", "G2", "G3"]) }));

const GRID = {
  view: "chart",
  source: "data/a.csv",
  mark: "grid",
  encoding: { x: { field: "x", type: "ordinal" }, y: { field: "y", type: "ordinal" }, color: { field: "v", type: "quantitative" } },
};
const CELLS = answer(layer("grid", 3, { x: f64([0, 1, 2]), y: f64([0, 0, 0]), v: q8([0, 127, 254], 0, 1) }));
const CATEGORY_GRID = { ...GRID, encoding: { ...GRID.encoding, color: { field: "g", type: "nominal" } } };
const CATEGORY_CELLS = answer(layer("grid", 3, { x: f64([0, 1, 2]), y: f64([0, 0, 0]), g: cat(["p", "q", "r"]) }));

describe("a layout switch keeps what the person did", () => {
  it("a brush on a scatter, its count and a legend's hidden group survive 600 -> 300 -> 600", async () => {
    const { chart } = mount(SCATTER, POINTS, "m");
    resize(600);
    act(() => chart.dispatchAction({ type: "legendToggleSelect", name: "A" }));
    const px = pixels(chart);
    const [p, q] = [px(2.5, 0), px(4.5, 5)];
    const range = [
      [Math.min(p[0]!, q[0]!), Math.max(p[0]!, q[0]!)],
      [Math.min(p[1]!, q[1]!), Math.max(p[1]!, q[1]!)],
    ];
    act(() => chart.dispatchAction({ type: "brush", areas: [{ brushType: "rect", range }] }));
    await settle();
    const before = selectedText();
    expect({ text: before, areas: brushAreas(chart) }).toEqual({ text: expect.stringMatching(/^2 selected/), areas: 1 });
    for (const w of [300, 600]) {
      resize(w);
      await settle();
      expect({ w, text: selectedText(), areas: brushAreas(chart), hidden: optionOf(chart).legend[0]!.selected?.A }).toEqual({
        w,
        text: before,
        areas: 1,
        hidden: false,
      });
    }
  });

  it("a pie's own pick stays lit across 600 -> 300 -> 600", () => {
    const { chart } = mount(PIE, SLICES);
    resize(600);
    act(() => clickAt(chart, sliceAt(chart, 1)));
    expect(opacities(chart)).toEqual([DIM_OPACITY, 1, DIM_OPACITY]);
    for (const w of [300, 600]) {
      resize(w);
      expect({ w, lit: opacities(chart), text: selectedText() }).toEqual({ w, lit: [DIM_OPACITY, 1, DIM_OPACITY], text: "1 selected" });
    }
  });

  it("a grid's lasso stays lit across 600 -> 300 -> 600", async () => {
    const { chart } = mount(GRID, CELLS);
    resize(600);
    // on grid 0, in cell coordinates (a grid's cells are found by the area's
    // coordinates: ECharts converts them to pixels for the drawn lasso)
    const poly = [[0.6, -0.4], [2.9, -0.4], [2.9, 0.4], [0.6, 0.4]]; // cells 1 and 2
    act(() => chart.dispatchAction({ type: "brush", areas: [{ brushType: "polygon", xAxisIndex: 0, coordRange: poly }] }));
    await settle();
    expect(painted.lits.at(-1)).toEqual([false, true, true]);
    for (const w of [300, 600]) {
      resize(w);
      await settle();
      expect({ w, lit: painted.lits.at(-1), text: selectedText() }).toEqual({ w, lit: [false, true, true], text: "2 selected" });
    }
  });

  it("after a doc change, the next click on the slice it held selects it", () => {
    const store = new MarkingStore();
    const { chart, redraw } = mount(PIE, SLICES, "m", store);
    act(() => clickAt(chart, sliceAt(chart, 1)));
    expect(store.get("m")?.marking.group).toEqual(new Set(["G2"]));
    redraw({ ...PIE, title: "Share by group" }); // a new doc: rebuilt in full
    act(() => clickAt(chart, sliceAt(chart, 1)));
    expect(store.get("m")?.marking.group).toEqual(new Set(["G2"]));
  });

  it("(control) with no doc change, the second click on that slice clears it", () => {
    const store = new MarkingStore();
    const { chart } = mount(PIE, SLICES, "m", store);
    act(() => clickAt(chart, sliceAt(chart, 1)));
    resize(300);
    act(() => clickAt(chart, sliceAt(chart, 1)));
    expect(store.get("m")).toBeUndefined();
  });
});

/** The chart's option as ECharts holds it, without the series (whose data
 * and closures differ between two mounts) -- JSON, so closures drop out. */
const layoutState = (chart: echarts.ECharts) => {
  const { series: _, ...rest } = chart.getOption() as Record<string, unknown>;
  return JSON.parse(JSON.stringify(rest)) as unknown;
};

describe("a layout switched there and back is the layout drawn fresh", () => {
  // The oracle is a chart mounted at the final width: every layout key is set
  // for both layouts, so none of the other's survives a round trip.
  it.each([
    ["a colour-scale grid", GRID, CELLS],
    ["a category grid", CATEGORY_GRID, CATEGORY_CELLS],
    ["a scatter with a legend", SCATTER, POINTS],
  ] as const)("%s: wide -> compact -> wide, and compact -> wide -> compact", (_, doc, a) => {
    const trip = mount(doc, a).chart;
    resize(600);
    resize(300);
    resize(600);
    const wide = mount(doc, a).chart;
    resize(600);
    expect(layoutState(trip)).toEqual(layoutState(wide));
    resize(300);
    const compact = mount(doc, a).chart;
    resize(300);
    expect(layoutState(trip)).toEqual(layoutState(compact));
  });
});

describe("a write the marking already holds", () => {
  it("still turns ECharts' own brush visual off: the marking lights the chart", async () => {
    // the spec's highlight seeds items i3 and i4 as this view's write; a
    // brush over exactly those rows writes the same again, which the store
    // takes as no change -- only whether the brush went to the marking changes
    const seeded = answer({
      ...POINTS.layers[0]!,
      highlight: btoa(String.fromCharCode(0b1100)),
      lit: 2,
    });
    const store = new MarkingStore();
    const { chart } = mount(SCATTER, seeded, "m", store);
    resize(600);
    const outOfBrush = () => (chart.getOption() as { brush: { outOfBrush?: object }[] }).brush[0]!.outOfBrush;
    expect(store.get("m")?.marking.item).toEqual(new Set(["i3", "i4"]));
    expect(outOfBrush()).toEqual({ color: "#ddd" });
    const px = pixels(chart);
    const [p, q] = [px(2.5, 0), px(4.5, 5)];
    const range = [
      [Math.min(p[0]!, q[0]!), Math.max(p[0]!, q[0]!)],
      [Math.min(p[1]!, q[1]!), Math.max(p[1]!, q[1]!)],
    ];
    act(() => chart.dispatchAction({ type: "brush", areas: [{ brushType: "rect", range }] }));
    await settle();
    expect(store.get("m")?.marking.item).toEqual(new Set(["i3", "i4"]));
    expect(outOfBrush()).toEqual({ colorAlpha: 1 });
  });
});

// sixteen levels: compact, their legend takes more than a short chart has
const MANY = answer(
  layer("grid", 16, {
    x: f64(Array.from({ length: 16 }, (_, i) => i % 4)),
    y: f64(Array.from({ length: 16 }, (_, i) => Math.floor(i / 4))),
    g: cat(Array.from({ length: 16 }, (_, i) => `level ${String(i).padStart(2, "0")}`)),
  }),
);

describe("a category legend with no room (row 3)", () => {
  it("is not drawn in a short compact chart, the notes line says so, and it is drawn again wide", () => {
    const { chart } = mount(CATEGORY_GRID, MANY);
    resize(300, 300);
    const shown = () => (chart.getOption() as { visualMap: { show: boolean }[] }).visualMap[0]!.show;
    expect({ shown: shown(), note: screen.queryByText("colour key hidden (too short)") !== null }).toEqual({ shown: false, note: true });
    resize(600, 300);
    expect({ shown: shown(), note: screen.queryByText("colour key hidden (too short)") !== null }).toEqual({ shown: true, note: false });
    const wide = mount(CATEGORY_GRID, MANY).chart;
    resize(600, 300);
    expect(layoutState(chart)).toEqual(layoutState(wide));
  });
});

describe("a pane reporting 0 px", () => {
  it("keeps the layout it had: 0 px is no width", () => {
    const { chart } = mount(GRID, CELLS);
    resize(139);
    const compact = layoutState(chart);
    resize(0, 0);
    expect(layoutState(chart)).toEqual(compact);
    expect((chart.getOption() as { grid: { left: unknown }[] }).grid[0]!.left).toBe(8); // compact's
  });
});

describe("a pane's height (P37, conformance N2)", () => {
  it("is no new layout for a wide chart: a pane growing taller does not re-render it", () => {
    mount(SCATTER, POINTS);
    resize(600, 400);
    const before = renders.n;
    resize(600, 401);
    resize(600, 437);
    expect(renders.n).toBe(before);
  });

  it("(control) is a new layout for a compact one, whose plot keeps half of it", () => {
    mount(SCATTER, POINTS);
    resize(300, 400);
    const before = renders.n;
    resize(300, 437);
    expect(renders.n).toBeGreaterThan(before);
  });
});
