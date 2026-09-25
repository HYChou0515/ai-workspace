// @vitest-environment happy-dom
/**
 * #847/#848 PR 5 P30: every mark a person can select from writes the marking,
 * as every other mark does. The chart is REAL ECharts (SSR), so the gesture
 * selects what ECharts' own selector for that mark finds; the marking is the
 * host's real store, through the SDK double.
 */
import { act, cleanup, render, screen } from "@testing-library/react";
import * as echarts from "echarts/core";
import { SVGRenderer } from "echarts/renderers";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { MarkingProvider } from "../../../../web/src/hooks/useMarking";
import { MarkingStore } from "../../../../web/src/lib/markings";
import { DIM_OPACITY } from "./highlight";
import { type Answer } from "./option";
import { answer, cat, f64, layer } from "./testAnswer";
import { clickAt, sliceAt, toolAt } from "./testGesture";

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

// The chart's own module (its series, components and brush selectors), with
// the one instance it makes kept for the test: SSR, no DOM to draw in.
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

function mount(store: MarkingStore, doc: object, a: Answer, marking: string | null = "m") {
  const stdout = JSON.stringify(a);
  sdk.useSandboxRun.mockReturnValue({ data: { stdout, stderr: "", exit_code: 0 }, error: null, isLoading: false, refetch: vi.fn() });
  render(
    <MarkingProvider store={store}>
      <ChartView spec={{ __doc: doc } as never} marking={marking} path="/v/a.ai.yaml" type={null} entities={[]} onCreate={() => {}} onPatch={() => {}} />
    </MarkingProvider>,
  );
  return made.charts.at(-1)!;
}

/** A person's drag over the data rectangle x0..x1, y0..y1 (category indices
 * allowed as fractions): dispatched in PIXELS, as a drag is. */
function pixels(chart: echarts.ECharts) {
  const at = (p: number[]) => chart.convertToPixel({ gridIndex: 0 }, p) as number[];
  const [o, ex, ey] = [at([0, 0]), at([1, 0]), at([0, 1])];
  return (x: number, y: number) => [o[0]! + x * (ex[0]! - o[0]!), o[1]! + y * (ey[1]! - o[1]!)];
}

async function drag(chart: echarts.ECharts, [[x0, x1], [y0, y1]]: [[number, number], [number, number]]) {
  const px = pixels(chart);
  const [p, q] = [px(x0, y0), px(x1, y1)];
  const range = [
    [Math.min(p[0]!, q[0]!), Math.max(p[0]!, q[0]!)],
    [Math.min(p[1]!, q[1]!), Math.max(p[1]!, q[1]!)],
  ];
  act(() => chart.dispatchAction({ type: "brush", areas: [{ brushType: "rect", range }] }));
  await settle();
}

/** A lasso through the data points `poly`, dispatched in pixels. */
async function lasso(chart: echarts.ECharts, poly: [number, number][]) {
  const px = pixels(chart);
  act(() => chart.dispatchAction({ type: "brush", areas: [{ brushType: "polygon", range: poly.map(([x, y]) => px(x, y)) }] }));
  await settle();
}

// Three groups a, b, c, one lot each.
const groups = { g: cat(["a", "b", "c"]), lot: cat(["L1", "L2", "L3"]) };
const byGroup = (mark: string) => ({
  view: "chart",
  source: "data/a.csv",
  keys: ["lot"],
  mark,
  encoding: { x: { field: "g", type: "nominal" }, y: { field: "v", type: "quantitative" } },
});

const marked = (store: MarkingStore) =>
  Object.fromEntries(Object.entries(store.get("m")?.marking ?? {}).map(([k, v]) => [k, [...v].sort()]));

beforeEach(() => {
  made.charts.length = 0;
});
afterEach(() => {
  cleanup();
  for (const c of made.charts) c.dispose();
});

describe("a selection on every mark writes the marking", () => {
  it("a brush over a heatmap writes the lots of the cells whose centre it holds", async () => {
    const doc = {
      view: "chart",
      source: "data/a.csv",
      keys: ["lot"],
      mark: "heatmap",
      encoding: {
        x: { field: "x", type: "nominal" },
        y: { field: "y", type: "nominal" },
        color: { field: "v", type: "quantitative" },
      },
    };
    const a = answer(
      layer("heatmap", 4, {
        x: cat(["a", "b", "a", "b"]),
        y: cat(["p", "p", "q", "q"]),
        v: f64([1, 2, 3, 4]),
        lot: cat(["L1", "L2", "L3", "L4"]),
      }),
    );
    const store = new MarkingStore();
    const chart = mount(store, doc, a);
    // x 0.6..1.4 holds b's centre only; y -0.4..1.4 both rows: cells (b, p) and (b, q)
    await drag(chart, [[0.6, 1.4], [-0.4, 1.4]]);
    expect(marked(store)).toEqual({ lot: ["L2", "L4"] });
  });

  it("a brush over a boxplot writes the lots of the groups whose box it meets", async () => {
    // boxes q1..q3 = 3..7, 13..17, 23..27
    const a = answer(
      layer("boxplot", 3, {
        ...groups,
        $lo: f64([2, 12, 22]),
        $q1: f64([3, 13, 23]),
        $mid: f64([5, 15, 25]),
        $q3: f64([7, 17, 27]),
        $hi: f64([8, 18, 28]),
      }),
    );
    const store = new MarkingStore();
    const chart = mount(store, byGroup("boxplot"), a);
    // y 6..14 meets a's box (to 7) and b's (from 13), not c's
    await drag(chart, [[-0.5, 2.5], [6, 14]]);
    expect(marked(store)).toEqual({ lot: ["L1", "L2"] });
  });

  it("a lasso over an errorbar writes the lots of the groups whose line it crosses", async () => {
    // lines 1..3, 11..13, 21..23
    const a = answer(layer("errorbar", 3, { ...groups, $lo: f64([1, 11, 21]), $mid: f64([2, 12, 22]), $hi: f64([3, 13, 23]) }));
    const store = new MarkingStore();
    const chart = mount(store, byGroup("errorbar"), a);
    // a band around c's line at y 22, clear of a and b
    await lasso(chart, [[1.7, 21.5], [2.3, 21.5], [2.3, 22.5], [1.7, 22.5]]);
    expect(marked(store)).toEqual({ lot: ["L3"] });
  });
});

const PIE = {
  view: "chart",
  source: "data/a.csv",
  keys: ["lot"],
  mark: "pie",
  encoding: { theta: { field: "n", type: "quantitative" }, color: { field: "lot", type: "nominal" } },
};
const SLICES = answer(layer("pie", 3, { n: f64([5, 3, 2]), lot: cat(["L1", "L2", "L3"]) }));
const click = (chart: echarts.ECharts, at: number[]) => act(() => clickAt(chart, at));

describe("a click on a pie's slice writes the marking", () => {
  it("writes that slice's lot", () => {
    const store = new MarkingStore();
    const chart = mount(store, PIE, SLICES);
    click(chart, sliceAt(chart, 1));
    expect(marked(store)).toEqual({ lot: ["L2"] });
    expect(store.get("m")!.source).toBe("/v/a.ai.yaml");
  });

  it("a click on another slice takes its place", () => {
    const store = new MarkingStore();
    const chart = mount(store, PIE, SLICES);
    click(chart, sliceAt(chart, 1));
    click(chart, sliceAt(chart, 2));
    expect(marked(store)).toEqual({ lot: ["L3"] });
  });

  it("a second click on the same slice clears it", () => {
    const store = new MarkingStore();
    const chart = mount(store, PIE, SLICES);
    click(chart, sliceAt(chart, 1));
    click(chart, sliceAt(chart, 1));
    expect(store.get("m")).toBeUndefined();
  });

  it("a click on empty space clears it", () => {
    const store = new MarkingStore();
    const chart = mount(store, PIE, SLICES);
    click(chart, sliceAt(chart, 1));
    click(chart, [5, 395]); // the bottom-left corner: no slice, no legend
    expect(store.get("m")).toBeUndefined();
  });

  it("a click on empty space clears nothing it did not select", () => {
    // another view's selection on the marking is not this pie's to clear
    const store = new MarkingStore();
    const chart = mount(store, PIE, SLICES);
    act(() => store.set("m", { lot: new Set(["L1"]) }, "/v/other.ai.yaml"));
    click(chart, [5, 395]);
    expect(marked(store)).toEqual({ lot: ["L1"] });
  });

  it("a click on empty space after a legend click clears nothing: the legend's choice is the legend's", () => {
    const store = new MarkingStore();
    const chart = mount(store, PIE, SLICES);
    click(chart, sliceAt(chart, 1));
    act(() => chart.dispatchAction({ type: "legendToggleSelect", name: "L1" }));
    click(chart, [5, 395]);
    expect(marked(store)).toEqual({ lot: ["L2", "L3"] });
  });

  it("a click on another mark's point leaves the marking as it is: those are brushed", () => {
    const store = new MarkingStore();
    const doc = { ...byGroup("scatter"), encoding: { x: { field: "x", type: "quantitative" }, y: { field: "y", type: "quantitative" } } };
    const chart = mount(store, doc, answer(layer("scatter", 2, { x: f64([1, 2]), y: f64([1, 2]), lot: cat(["L1", "L2"]) })));
    act(() => store.set("m", { lot: new Set(["L1"]) }, "/v/other.ai.yaml"));
    click(chart, chart.convertToPixel({ gridIndex: 0 }, [2, 2]) as number[]);
    expect(marked(store)).toEqual({ lot: ["L1"] });
  });

  // #847/#848 PR 5 P31 [mine, open to override]: a pie on its own has nothing
  // to brush (P30), but a marking its `highlight:` seeded -- or a slice picked
  // before -- must be clearable from the pie itself, as from every other chart
  // (P17). So it draws the ✕ alone.
  it("a pie on its own offers only the ✕, in the chart as drawn", () => {
    const chart = mount(new MarkingStore(), PIE, SLICES);
    expect(toolAt(chart, "clear")).not.toBeNull();
    expect(toolAt(chart, "rect")).toBeNull();
    expect(toolAt(chart, "polygon")).toBeNull();
    // (control: a chart with axes draws all three, where toolAt finds them)
    const doc = { ...byGroup("scatter"), encoding: { x: { field: "x", type: "quantitative" }, y: { field: "y", type: "quantitative" } } };
    const scatter = mount(new MarkingStore(), doc, answer(layer("scatter", 2, { x: f64([1, 2]), y: f64([1, 2]), lot: cat(["L1", "L2"]) })));
    expect(["rect", "polygon", "clear"].map((t) => toolAt(scatter, t) !== null)).toEqual([true, true, true]);
  });

  it("the pie's ✕ clears a marking its highlight seeded", () => {
    const seeded = answer(
      layer("pie", 3, { n: f64([5, 3, 2]), lot: cat(["L1", "L2", "L3"]) }, { highlight: btoa(String.fromCharCode(0b010)), lit: 1 }),
    );
    const store = new MarkingStore();
    const chart = mount(store, PIE, seeded);
    expect(marked(store)).toEqual({ lot: ["L2"] }); // seeded on open
    click(chart, toolAt(chart, "clear")!);
    expect(store.get("m")).toBeUndefined();
  });

  it("the pie's ✕ clears a slice it picked, and a click on that slice picks it again", () => {
    const store = new MarkingStore();
    const chart = mount(store, PIE, SLICES);
    click(chart, sliceAt(chart, 1));
    click(chart, toolAt(chart, "clear")!);
    expect(store.get("m")).toBeUndefined();
    click(chart, sliceAt(chart, 1));
    expect(marked(store)).toEqual({ lot: ["L2"] });
  });

  // #847/#848 PR 5 P32: a pie counting items holds COUNTS under `item`, and
  // `$key.item` repeats them: its item column is not a key.
  const COUNTING = {
    view: "chart",
    source: "data/a.csv",
    keys: ["group", "item"],
    mark: "pie",
    encoding: { theta: { field: "item", type: "quantitative", aggregate: "count" }, color: { field: "group", type: "nominal" } },
  };
  const COUNTS = answer(layer("pie", 4, { group: cat(["A", "B", "C", "D"]), item: f64([6, 4, 3, 5]), "$key.item": cat([6, 4, 3, 5]) }));
  const opacities = (chart: echarts.ECharts) => {
    type Data = { count(): number; getItemVisual(i: number, k: "style"): { opacity?: number } };
    type Model = { getSeriesByIndex(i: number): { getData(): Data } };
    const data = (chart as unknown as { getModel(): Model }).getModel().getSeriesByIndex(0).getData();
    return Array.from({ length: data.count() }, (_, i) => data.getItemVisual(i, "style").opacity ?? 1);
  };

  it("a pie counting items lights the groups a marking holds, not the slices whose count is a marked item (P32)", () => {
    const store = new MarkingStore();
    const chart = mount(store, COUNTING, COUNTS);
    // rows (A, 4), (A, 5) and (B, 3) picked in another view
    act(() => store.set("m", { group: new Set(["A", "B"]), item: new Set(["3", "4", "5"]) }, "/v/other.ai.yaml"));
    expect(opacities(chart)).toEqual([1, 1, DIM_OPACITY, DIM_OPACITY]);
  });

  it("a click on a counting pie's slice writes its group, not its count (P32)", () => {
    const store = new MarkingStore();
    const chart = mount(store, COUNTING, COUNTS);
    click(chart, sliceAt(chart, 0));
    expect(marked(store)).toEqual({ group: ["A"] });
  });

  it("on no marking, the slice it picked is lit and the rest dimmed", () => {
    const chart = mount(new MarkingStore(), PIE, SLICES, null);
    click(chart, sliceAt(chart, 1));
    type Data = { count(): number; getItemVisual(i: number, k: "style"): { opacity?: number } };
    type Model = { getSeriesByIndex(i: number): { getData(): Data } };
    const data = (chart as unknown as { getModel(): Model }).getModel().getSeriesByIndex(0).getData();
    const opacity = Array.from({ length: data.count() }, (_, i) => data.getItemVisual(i, "style").opacity ?? 1);
    expect(opacity).toEqual([DIM_OPACITY, 1, DIM_OPACITY]);
    expect(screen.getByText("1 selected")).toBeTruthy();
  });

  // #847/#848 PR 5 P34 row 1: a click toggles only what it wrote. Once another
  // view rewrote the marking, the pie's pick is forgotten: empty space clears
  // nothing, and its slice is picked afresh rather than taken for a second click.
  it("a click on empty space after another view rewrote the marking clears nothing (P34)", () => {
    const store = new MarkingStore();
    const chart = mount(store, PIE, SLICES);
    click(chart, sliceAt(chart, 1));
    act(() => store.set("m", { lot: new Set(["L1"]) }, "/v/other.ai.yaml"));
    click(chart, [5, 395]);
    expect(marked(store)).toEqual({ lot: ["L1"] });
  });

  it("a click on the same slice after another view rewrote the marking picks it again (P34)", () => {
    const store = new MarkingStore();
    const chart = mount(store, PIE, SLICES);
    click(chart, sliceAt(chart, 1));
    act(() => store.set("m", { lot: new Set(["L1"]) }, "/v/other.ai.yaml"));
    click(chart, sliceAt(chart, 1));
    expect(marked(store)).toEqual({ lot: ["L2"] });
  });

  it("another view writing the same values is still another view's write (P34)", () => {
    const store = new MarkingStore();
    const chart = mount(store, PIE, SLICES);
    click(chart, sliceAt(chart, 1));
    act(() => store.set("m", { lot: new Set(["L2"]) }, "/v/other.ai.yaml"));
    click(chart, [5, 395]);
    expect(marked(store)).toEqual({ lot: ["L2"] });
    expect(store.get("m")!.source).toBe("/v/other.ai.yaml");
  });

  // P35 row 9 [supersedes P34's "what the marking holds when the click comes"]:
  // another view's write drops the pick there and then, so a later write of
  // the same values (the same file open elsewhere) brings no pick back.
  it("a pick another view's write dropped stays dropped when the marking holds its values again (P35)", () => {
    const store = new MarkingStore();
    const chart = mount(store, PIE, SLICES);
    click(chart, sliceAt(chart, 1));
    act(() => store.set("m", { lot: new Set(["L1"]) }, "/v/other.ai.yaml"));
    act(() => store.set("m", { lot: new Set(["L2"]) }, "/v/a.ai.yaml"));
    click(chart, [5, 395]);
    expect(marked(store)).toEqual({ lot: ["L2"] });
    expect(screen.queryByText(/selected/)).toBeNull();
  });

  // #847/#848 PR 5 P34 row 5: on a marking this pie cannot write (no `keys:`),
  // its pick writes nothing, so the pie lights it itself, as with no marking.
  const KEYLESS = { ...PIE, keys: undefined };
  it("on a marking it cannot write, the slice it picked is lit and the rest dimmed (P34)", () => {
    const store = new MarkingStore();
    const chart = mount(store, KEYLESS, SLICES);
    click(chart, sliceAt(chart, 1));
    expect(store.get("m")).toBeUndefined(); // nothing written
    expect(opacities(chart)).toEqual([DIM_OPACITY, 1, DIM_OPACITY]);
    expect(screen.getByText("1 selected")).toBeTruthy();
    // and empty space clears it, as it would with no marking
    click(chart, [5, 395]);
    expect(opacities(chart)).toEqual([1, 1, 1]);
  });

  it("on a marking another view wrote, a pick that wrote nothing is still its own to clear (P34)", () => {
    const store = new MarkingStore();
    const chart = mount(store, KEYLESS, SLICES);
    act(() => store.set("m", { lot: new Set(["L1"]) }, "/v/other.ai.yaml"));
    expect(opacities(chart)).toEqual([1, DIM_OPACITY, DIM_OPACITY]); // the marking's
    click(chart, sliceAt(chart, 1));
    expect(opacities(chart)).toEqual([DIM_OPACITY, 1, DIM_OPACITY]); // its own pick
    click(chart, [5, 395]);
    expect(opacities(chart)).toEqual([1, DIM_OPACITY, DIM_OPACITY]); // the marking's again
    expect(marked(store)).toEqual({ lot: ["L1"] });
  });

  // #847/#848 PR 5 P36 row 10: "by <columns>" names the MARKING's columns, so
  // it is said only of a selection that went to the marking. A pick that wrote
  // nothing is counted, and no more.
  it("a pick that wrote nothing is counted without the marking's columns once another view wrote it (P36)", () => {
    const store = new MarkingStore();
    const chart = mount(store, KEYLESS, SLICES);
    click(chart, sliceAt(chart, 1));
    act(() => store.set("m", { lot: new Set(["L1"]) }, "/v/other.ai.yaml"));
    expect(screen.getByText("1 selected")).toBeTruthy();
    expect(screen.queryByText(/· by/)).toBeNull();
  });

  it("a pick that wrote nothing, made on a marking another view holds, is counted without its columns (P36)", () => {
    const store = new MarkingStore();
    const chart = mount(store, KEYLESS, SLICES);
    act(() => store.set("m", { lot: new Set(["L1"]) }, "/v/other.ai.yaml"));
    click(chart, sliceAt(chart, 1));
    expect(screen.getByText("1 selected")).toBeTruthy();
    expect(screen.queryByText(/· by/)).toBeNull();
  });

  it("(control) a pick that went to the marking says which columns it marks by (P36)", () => {
    const store = new MarkingStore();
    const chart = mount(store, PIE, SLICES);
    click(chart, sliceAt(chart, 1));
    expect(screen.getByText("1 selected · by lot")).toBeTruthy();
  });

  it("(control) on a marking it can write, the slice is lit by the marking (P34)", () => {
    const store = new MarkingStore();
    const chart = mount(store, PIE, SLICES);
    click(chart, sliceAt(chart, 1));
    act(() => store.set("m", { lot: new Set(["L1"]) }, "/v/other.ai.yaml"));
    expect(opacities(chart)).toEqual([1, DIM_OPACITY, DIM_OPACITY]);
  });

  // #847/#848 PR 5 P34 row 6 [decided]: on no marking, a legend toggle is the
  // person's latest gesture, and it is what is lit -- it replaces the spec's
  // own `highlight:` dimming, as a grid's selection already does.
  it("on no marking, a legend toggle replaces the spec's highlight: what the legend left shown is lit (P34)", () => {
    const seeded = answer(
      layer("pie", 3, { n: f64([5, 3, 2]), lot: cat(["L1", "L2", "L3"]) }, { highlight: btoa(String.fromCharCode(0b010)), lit: 1 }),
    );
    const chart = mount(new MarkingStore(), PIE, seeded, null);
    expect(opacities(chart)).toEqual([DIM_OPACITY, 1, DIM_OPACITY]); // the spec's highlight
    act(() => chart.dispatchAction({ type: "legendToggleSelect", name: "L1" }));
    // L1 hidden (ECharts drops it from the drawn data); L2 and L3, the slices
    // left shown, both lit -- by the spec's highlight L3 would read dimmed
    expect(opacities(chart)).toEqual([1, 1]);
  });

  it("the legend still selects the slices left shown", async () => {
    const store = new MarkingStore();
    const chart = mount(store, PIE, SLICES);
    // the toggle a click on a legend entry dispatches (echarts LegendView)
    act(() => chart.dispatchAction({ type: "legendToggleSelect", name: "L1" }));
    expect(marked(store)).toEqual({ lot: ["L2", "L3"] });
  });
});
