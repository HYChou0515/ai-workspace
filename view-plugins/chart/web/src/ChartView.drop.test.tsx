// @vitest-environment happy-dom
/**
 * #847/#848 PR 5 P35 row 9: the marking is what a linked chart shows. Once
 * another view rewrites the marking, a chart whose selection went to it drops
 * its own selection -- the "N selected" count, the brush box, the pie's pick,
 * the legend's hidden entries -- and shows the marking as every other view
 * does. A chart whose selection writes nothing (no marking, no `keys:`) keeps
 * its own: nothing else holds it. Before, the count, box or pick stayed
 * beside a marking that no longer held what they said (P34's demo).
 *
 * The chart is REAL ECharts (SSR); the marking is the host's real store.
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
import { clickAt, sliceAt } from "./testGesture";

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

echarts.use([SVGRenderer]);

const settle = () => act(() => new Promise((r) => setTimeout(r, 400)));
const SELF = "/v/a.ai.yaml";
const OTHER = "/v/other.ai.yaml";

function mount(store: MarkingStore, doc: object, a: Answer, marking: string | null = "m") {
  const stdout = JSON.stringify(a);
  sdk.useSandboxRun.mockReturnValue({ data: { stdout, stderr: "", exit_code: 0 }, error: null, isLoading: false, refetch: vi.fn() });
  render(
    <MarkingProvider store={store}>
      <ChartView spec={{ __doc: doc } as never} marking={marking} path={SELF} type={null} entities={[]} onCreate={() => {}} onPatch={() => {}} />
    </MarkingProvider>,
  );
  return made.charts.at(-1)!;
}

const marked = (store: MarkingStore) =>
  Object.fromEntries(Object.entries(store.get("m")?.marking ?? {}).map(([k, v]) => [k, [...v].sort()]));
const selectedText = () => screen.queryByText(/selected/)?.textContent ?? null;
const brushAreas = (chart: echarts.ECharts) =>
  (chart as unknown as { getModel(): { getComponent(m: string): { areas: unknown[] } } }).getModel().getComponent("brush").areas.length;
function opacities(chart: echarts.ECharts, s = 0): number[] {
  type Data = { count(): number; getItemVisual(i: number, k: "style"): { opacity?: number } };
  type Model = { getSeriesByIndex(i: number): { getData(): Data } };
  const data = (chart as unknown as { getModel(): Model }).getModel().getSeriesByIndex(s).getData();
  return Array.from({ length: data.count() }, (_, i) => data.getItemVisual(i, "style").opacity ?? 1);
}
const click = (chart: echarts.ECharts, at: number[]) => act(() => clickAt(chart, at));
const other = (store: MarkingStore, lots: string[] | null, source = OTHER) =>
  act(() => store.set("m", lots ? { lot: new Set(lots) } : null, source));

const PIE = {
  view: "chart",
  source: "data/a.csv",
  keys: ["lot"],
  mark: "pie",
  encoding: { theta: { field: "n", type: "quantitative" }, color: { field: "lot", type: "nominal" } },
};
const SLICES = answer(layer("pie", 3, { n: f64([5, 3, 2]), lot: cat(["L1", "L2", "L3"]) }));
const KEYLESS = { ...PIE, keys: undefined };

const SCATTER = {
  view: "chart",
  source: "data/a.csv",
  keys: ["lot"],
  mark: "scatter",
  encoding: { x: { field: "x", type: "quantitative" }, y: { field: "y", type: "quantitative" }, color: { field: "lot", type: "nominal" } },
};
const POINTS = answer(layer("scatter", 3, { x: f64([1, 2, 3]), y: f64([1, 2, 3]), lot: cat(["L1", "L2", "L3"]) }));

/** A person's box over data x 1.5..3.5, y 1.5..3.5 (points L2, L3), in pixels. */
async function brushTwo(chart: echarts.ECharts) {
  const at = (p: number[]) => chart.convertToPixel({ gridIndex: 0 }, p) as number[];
  const [p, q] = [at([1.5, 1.5]), at([3.5, 3.5])];
  const range = [
    [Math.min(p[0]!, q[0]!), Math.max(p[0]!, q[0]!)],
    [Math.min(p[1]!, q[1]!), Math.max(p[1]!, q[1]!)],
  ];
  act(() => chart.dispatchAction({ type: "brush", areas: [{ brushType: "rect", range }] }));
  await settle();
}

beforeEach(() => {
  made.charts.length = 0;
});
afterEach(() => {
  cleanup();
  for (const c of made.charts) c.dispose();
});

describe("another view's write drops a chart's own selection that went to the marking (P35 row 9)", () => {
  it("a pie's pick: its count goes and the marking lights the pie (red before: '1 selected' stayed)", () => {
    const store = new MarkingStore();
    const chart = mount(store, PIE, SLICES);
    click(chart, sliceAt(chart, 1));
    expect(selectedText()).toMatch(/^1 selected/);
    other(store, ["L1", "L3"]);
    expect(selectedText()).toBeNull();
    expect(opacities(chart)).toEqual([1, DIM_OPACITY, 1]);
  });

  it("a brush: its count and its box go (red before: both stayed)", async () => {
    const store = new MarkingStore();
    const chart = mount(store, SCATTER, POINTS);
    await brushTwo(chart);
    expect({ text: selectedText(), areas: brushAreas(chart), marked: marked(store) }).toEqual({
      text: expect.stringMatching(/^2 selected/),
      areas: 1,
      marked: { lot: ["L2", "L3"] },
    });
    other(store, ["L1"]);
    await settle();
    expect({ text: selectedText(), areas: brushAreas(chart), marked: marked(store) }).toEqual({ text: null, areas: 0, marked: { lot: ["L1"] } });
  });

  it("another view clearing the marking drops it too (a chart with no legend)", async () => {
    const store = new MarkingStore();
    const chart = mount(store, { ...SCATTER, encoding: { x: SCATTER.encoding.x, y: SCATTER.encoding.y } }, POINTS);
    await brushTwo(chart);
    other(store, null);
    await settle();
    expect({ text: selectedText(), areas: brushAreas(chart) }).toEqual({ text: null, areas: 0 });
  });

  it("another view writing the same values is another view's write: dropped", () => {
    const store = new MarkingStore();
    const chart = mount(store, PIE, SLICES);
    click(chart, sliceAt(chart, 1));
    other(store, ["L2"]);
    expect(selectedText()).toBeNull();
  });

  it("the legend's hidden entries come back: what the legend hid is not what the marking holds", () => {
    const store = new MarkingStore();
    const chart = mount(store, SCATTER, POINTS);
    act(() => chart.dispatchAction({ type: "legendToggleSelect", name: "L1" }));
    expect(marked(store)).toEqual({ lot: ["L2", "L3"] });
    expect(selectedText()).toMatch(/^2 selected/);
    other(store, ["L1"]);
    const legend = (chart.getOption() as { legend: { selected?: Record<string, boolean> }[] }).legend[0]!.selected ?? {};
    expect(Object.values(legend).every((shown) => shown !== false)).toBe(true);
    expect(selectedText()).toBeNull();
    expect(marked(store)).toEqual({ lot: ["L1"] }); // bringing them back writes nothing
  });

  it("after the drop, the chart's next gesture selects and writes as ever", async () => {
    const store = new MarkingStore();
    const chart = mount(store, SCATTER, POINTS);
    await brushTwo(chart);
    other(store, ["L1"]);
    await settle();
    await brushTwo(chart);
    expect({ text: selectedText(), areas: brushAreas(chart), marked: marked(store) }).toEqual({
      text: expect.stringMatching(/^2 selected/),
      areas: 1,
      marked: { lot: ["L2", "L3"] },
    });
  });
});

describe("what is not another view's write keeps the chart's own selection (P35 row 9)", () => {
  it("the chart's own write: the brush's count and box stay", async () => {
    const store = new MarkingStore();
    const chart = mount(store, SCATTER, POINTS);
    await brushTwo(chart);
    await settle();
    expect({ text: selectedText(), areas: brushAreas(chart), source: store.get("m")!.source }).toEqual({
      text: expect.stringMatching(/^2 selected/),
      areas: 1,
      source: SELF,
    });
  });

  it("the chart's own second pick replaces its first, and stays", () => {
    const store = new MarkingStore();
    const chart = mount(store, PIE, SLICES);
    click(chart, sliceAt(chart, 1));
    click(chart, sliceAt(chart, 2));
    expect(selectedText()).toMatch(/^1 selected/);
    expect(opacities(chart)).toEqual([DIM_OPACITY, DIM_OPACITY, 1]);
  });

  it("a keyless pie's pick writes nothing, so another view's write leaves it: its own count and lit slice stay", () => {
    const store = new MarkingStore();
    const chart = mount(store, KEYLESS, SLICES);
    click(chart, sliceAt(chart, 1));
    other(store, ["L1"]);
    expect(selectedText()).toMatch(/^1 selected/);
    expect(opacities(chart)).toEqual([DIM_OPACITY, 1, DIM_OPACITY]);
  });

  it("a scatter on a marking it cannot write keeps its brush when another view writes", async () => {
    const store = new MarkingStore();
    const chart = mount(store, { ...SCATTER, keys: undefined }, POINTS);
    await brushTwo(chart);
    other(store, ["L1"]);
    await settle();
    expect({ areas: brushAreas(chart), text: selectedText() }).toEqual({ areas: 1, text: expect.stringMatching(/^2 selected/) });
  });

  it("a brush made while detached is its own: attached again to a marking another view rewrote, it stays", async () => {
    const store = new MarkingStore();
    const doc = SCATTER;
    const view = (marking: string | null) => (
      <MarkingProvider store={store}>
        <ChartView spec={{ __doc: doc } as never} marking={marking} path={SELF} type={null} entities={[]} onCreate={() => {}} onPatch={() => {}} />
      </MarkingProvider>
    );
    sdk.useSandboxRun.mockReturnValue({ data: { stdout: JSON.stringify(POINTS), stderr: "", exit_code: 0 }, error: null, isLoading: false, refetch: vi.fn() });
    const { rerender } = render(view("m"));
    const chart = made.charts.at(-1)!;
    await brushTwo(chart); // written to m
    rerender(view(null));
    await brushTwo(chart); // a second box, detached: written nowhere
    other(store, ["L1"]);
    rerender(view("m"));
    await settle();
    expect({ areas: brushAreas(chart) > 0, text: selectedText() }).toEqual({ areas: true, text: expect.stringMatching(/^2 selected/) });
  });

  it("a pick another view's write dropped is not cleared by empty space later: nothing of it is left (P34 row 1)", () => {
    const store = new MarkingStore();
    const chart = mount(store, PIE, SLICES);
    click(chart, sliceAt(chart, 1));
    other(store, ["L1"]);
    click(chart, [5, 395]);
    expect(marked(store)).toEqual({ lot: ["L1"] });
  });
});
