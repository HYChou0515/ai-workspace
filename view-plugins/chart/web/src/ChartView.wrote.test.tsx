// @vitest-environment happy-dom
/**
 * #847/#848 PR 5 P37 (review round 18): whether a chart's selection went to
 * the marking is read from what it WROTE -- the marking it wrote to must be
 * the one the chart is on now; a selection made detached wrote nothing (row
 * 13) -- and the write keeps the source it was made as, so a view file
 * renamed under a mounted chart does not read its own write as another
 * view's (row 15). Row 16 pins the pie's third click on one slice.
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

// The chart debounces brush events (throttleDelay 250 ms in the option).
const settle = () => act(() => new Promise((r) => setTimeout(r, 400)));
const SELF = "/v/a.ai.yaml";
const OTHER = "/v/other.ai.yaml";

// Rows 0..3: (G1,1) (G1,2) (G2,1) (G2,2), at x 1..4; row 4 (G3,3) at x 5.
const POINTS = answer(
  layer("scatter", 5, {
    a: f64([1, 2, 3, 4, 5]),
    b: f64([1, 1, 1, 1, 1]),
    group: cat(["G1", "G1", "G2", "G2", "G3"]),
    item: cat(["1", "2", "1", "2", "3"]),
  }),
);
const SCATTER = {
  view: "chart",
  source: "data/a.csv",
  keys: ["group"],
  mark: "scatter",
  encoding: { x: { field: "a", type: "quantitative" }, y: { field: "b", type: "quantitative" } },
};
const PIE = {
  view: "chart",
  source: "data/a.csv",
  keys: ["group"],
  mark: "pie",
  encoding: { theta: { field: "n", type: "quantitative" }, color: { field: "group", type: "nominal" } },
};
const SLICES = answer(layer("pie", 3, { n: f64([5, 3, 2]), group: cat(["G1", "G2", "G3"]) }));

type Mounted = { chart: echarts.ECharts; again: (marking: string | null, path?: string) => void };
function mount(store: MarkingStore, doc: object, a: Answer, marking: string | null = "m"): Mounted {
  sdk.useSandboxRun.mockReturnValue({ data: { stdout: JSON.stringify(a), stderr: "", exit_code: 0 }, error: null, isLoading: false, refetch: vi.fn() });
  const el = (m: string | null, path: string) => (
    <MarkingProvider store={store}>
      <ChartView spec={{ __doc: doc } as never} marking={m} path={path} type={null} entities={[]} onCreate={() => {}} onPatch={() => {}} />
    </MarkingProvider>
  );
  const view = render(el(marking, SELF));
  return { chart: made.charts.at(-1)!, again: (m, path = SELF) => view.rerender(el(m, path)) };
}

const selectedText = () => screen.queryByText(/selected/)?.textContent ?? null;
const marked = (store: MarkingStore, name = "m") =>
  Object.fromEntries(Object.entries(store.get(name)?.marking ?? {}).map(([k, v]) => [k, [...v].sort()]));
const brushAreas = (chart: echarts.ECharts) =>
  (chart as unknown as { getModel(): { getComponent(m: string): { areas: unknown[] } } }).getModel().getComponent("brush").areas.length;

/** Per row (the one series' points), how the chart draws it: "brush-grey" is
 * ECharts' own brush visual (a point outside the box). */
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

/** A person's box over x 0.5..1.5 and 3.5..4.5: rows 0 (G1,1) and 3 (G2,2). */
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
});
afterEach(() => {
  cleanup();
  for (const c of made.charts) c.dispose();
});

describe("a selection went to the marking only if what it wrote is the marking the chart is on (P37 row 13)", () => {
  it("(control) a brush on m says which columns m marks by, and m lights the chart", async () => {
    const store = new MarkingStore();
    const { chart } = mount(store, SCATTER, POINTS);
    await brush(chart);
    expect(marked(store)).toEqual({ group: ["G1", "G2"] });
    expect({ text: selectedText(), drawn: drawn(chart) }).toEqual({
      text: "2 selected · by group",
      drawn: ["lit", "lit", "lit", "lit", "dim"],
    });
  });

  it("a brush made detached wrote nothing: attached again to a marking another view rewrote, it is counted without m's columns and keeps its own brush visual (red before)", async () => {
    const store = new MarkingStore();
    const { chart, again } = mount(store, SCATTER, POINTS);
    await brush(chart); // written to m
    again(null);
    await settle();
    await brush(chart); // detached: written nowhere
    act(() => store.set("m", { group: new Set(["G1"]) }, OTHER));
    again("m");
    await settle();
    expect(marked(store)).toEqual({ group: ["G1"] });
    // as a scatter on a marking it cannot write: the brush's own visual
    // (outside the box grey), and the marking lighting the rest
    expect({ text: selectedText(), boxes: brushAreas(chart), drawn: drawn(chart) }).toEqual({
      text: "2 selected",
      boxes: 2,
      drawn: ["lit", "brush-grey", "brush-grey", "dim", "brush-grey"],
    });
  });

  it("a brush on m, the chart moved to n: counted without n's columns, with its own brush visual (red before: '· by item')", async () => {
    const store = new MarkingStore();
    act(() => store.set("n", { item: new Set(["2"]) }, OTHER));
    const { chart, again } = mount(store, SCATTER, POINTS);
    await brush(chart);
    again("n");
    await settle();
    expect(marked(store, "m")).toEqual({ group: ["G1", "G2"] });
    expect({ text: selectedText(), drawn: drawn(chart) }).toEqual({
      text: "2 selected",
      drawn: ["dim", "brush-grey", "brush-grey", "lit", "brush-grey"],
    });
  });

  it("(control) detached and attached again with no gesture between, what it wrote is still m's: '· by' again", async () => {
    const store = new MarkingStore();
    const { chart, again } = mount(store, SCATTER, POINTS);
    await brush(chart);
    again(null);
    await settle();
    again("m");
    await settle();
    expect({ text: selectedText(), drawn: drawn(chart) }).toEqual({
      text: "2 selected · by group",
      drawn: ["lit", "lit", "lit", "lit", "dim"],
    });
  });
});

describe("the chart's write keeps the source it was made as (P37 row 15)", () => {
  it("renamed under the chart, its own write is still its own: count, box and marking stay (red before: dropped)", async () => {
    const store = new MarkingStore();
    const { chart, again } = mount(store, SCATTER, POINTS);
    await brush(chart);
    again("m", "/v/renamed.ai.yaml");
    await settle();
    expect({ text: selectedText(), boxes: brushAreas(chart), marked: marked(store), source: store.get("m")!.source }).toEqual({
      text: "2 selected · by group",
      boxes: 2,
      marked: { group: ["G1", "G2"] },
      source: SELF,
    });
  });

  it("renamed, then detached and attached again: its write is still its own", async () => {
    // attaching again is when the chart asks afresh whether the marking still
    // holds its write -- as the source it wrote as, not the new name
    const store = new MarkingStore();
    const { chart, again } = mount(store, SCATTER, POINTS);
    await brush(chart);
    again("m", "/v/renamed.ai.yaml");
    again(null, "/v/renamed.ai.yaml");
    await settle();
    again("m", "/v/renamed.ai.yaml");
    await settle();
    expect({ text: selectedText(), boxes: brushAreas(chart) }).toEqual({ text: "2 selected · by group", boxes: 2 });
  });

  it("(control) renamed, another view's write still drops it", async () => {
    const store = new MarkingStore();
    const { chart, again } = mount(store, SCATTER, POINTS);
    await brush(chart);
    again("m", "/v/renamed.ai.yaml");
    await settle();
    act(() => store.set("m", { group: new Set(["G3"]) }, OTHER));
    await settle();
    expect({ text: selectedText(), boxes: brushAreas(chart) }).toEqual({ text: null, boxes: 0 });
  });

  it("renamed, its next gesture writes as the new name", async () => {
    const store = new MarkingStore();
    const { chart, again } = mount(store, SCATTER, POINTS);
    again("m", "/v/renamed.ai.yaml");
    await brush(chart);
    expect(store.get("m")!.source).toBe("/v/renamed.ai.yaml");
    expect(selectedText()).toBe("2 selected · by group");
  });
});

describe("a pie's third click on one slice picks it again (P37 row 16)", () => {
  it("click, click, click: the slice is selected, as after the first", () => {
    const store = new MarkingStore();
    const { chart } = mount(store, PIE, SLICES);
    const click = () => act(() => clickAt(chart, sliceAt(chart, 1)));
    click();
    expect(marked(store)).toEqual({ group: ["G2"] });
    click();
    expect(store.get("m")).toBeUndefined();
    click();
    expect(marked(store)).toEqual({ group: ["G2"] });
    expect(selectedText()).toBe("1 selected · by group");
  });
});
