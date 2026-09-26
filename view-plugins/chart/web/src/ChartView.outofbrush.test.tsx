// @vitest-environment happy-dom
/**
 * #847/#848 PR 5 P45 row 41: a marking dims by opacity; a brush that writes
 * nothing marks its outside by colour -- desaturated, its lightness kept.
 * P44 row 37 made out-of-brush the 0.15 opacity the marking dims to, so on a
 * chart another view's marking lit, a brush over rows 0 and 3 drew brushed
 * row 3 as the out-of-brush rows (4 states became 2).
 *
 * Real ECharts (SSR), the host's real MarkingStore; what is read is what is
 * DRAWN: the fill, stroke and opacity of each item's shape.
 */
import { act, cleanup, render } from "@testing-library/react";
import * as echarts from "echarts/core";
import { SVGRenderer } from "echarts/renderers";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { MarkingProvider } from "../../../../web/src/hooks/useMarking";
import { MarkingStore } from "../../../../web/src/lib/markings";
import { DIM_OPACITY } from "./highlight";
import { type Answer } from "./option";
import { stackCase } from "./stackCorpus";
import { answer, cat, f64, layer } from "./testAnswer";
import { desaturated, type Drawn, drawn, lightness } from "./testDrawn";

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

function mount(store: MarkingStore, doc: object, a: Answer, marking: string | null = "m") {
  sdk.useSandboxRun.mockReturnValue({ data: { stdout: JSON.stringify(a), stderr: "", exit_code: 0 }, error: null, isLoading: false, refetch: vi.fn() });
  render(
    <MarkingProvider store={store}>
      <ChartView spec={{ __doc: doc } as never} marking={marking} path="/v/a.ai.yaml" type={null} entities={[]} onCreate={() => {}} onPatch={() => {}} />
    </MarkingProvider>,
  );
  return made.charts.at(-1)!;
}

/** A person's box from x0 to x1 (x values, category indices allowed as
 * fractions) over the whole height: dispatched in PIXELS, as a drag is. */
async function box(chart: echarts.ECharts, [x0, x1]: [number, number]) {
  const at = (x: number) => {
    const [o, e] = [0, 1].map((k) => chart.convertToPixel({ gridIndex: 0 }, [k, 0]) as number[]);
    return o![0]! + x * (e![0]! - o![0]!);
  };
  act(() => chart.dispatchAction({ type: "brush", areas: [{ brushType: "rect", range: [[at(x0), at(x1)], [0, 400]] }] }));
  await settle();
}

beforeEach(() => {
  made.charts.length = 0;
});
afterEach(() => {
  cleanup();
  for (const c of made.charts) c.dispose();
});

// Rows 0..4 at x 1..5; the chart names no `keys:`, so its brush writes nothing.
const POINTS = answer(layer("scatter", 5, { a: f64([1, 2, 3, 4, 5]), b: f64([1, 1, 1, 1, 1]), group: cat(["G1", "G1", "G2", "G2", "G3"]) }));
const SCATTER = {
  view: "chart",
  source: "data/a.csv",
  mark: "scatter",
  encoding: { x: { field: "a", type: "quantitative" }, y: { field: "b", type: "quantitative" } },
};

describe("the four states of a row: lit or dimmed by the marking, in or out of the brush", () => {
  it("draws each apart: the marking by opacity, the brush's outside by colour", async () => {
    const store = new MarkingStore();
    // another view lit G1: rows 0 and 1
    act(() => store.set("m", { group: new Set(["G1"]) }, "/v/other.ai.yaml"));
    const chart = mount(store, SCATTER, POINTS);
    await settle();
    const own = drawn(chart, 0, 0);
    expect(drawn(chart, 0, 3).opacity).toBe(DIM_OPACITY);
    // a box over rows 0 and 3 (x 0.5..1.5 and 3.5..4.5)
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
    expect(store.get("m")?.source).toBe("/v/other.ai.yaml");
    const d = [0, 1, 2, 3, 4].map((i) => drawn(chart, 0, i));
    // lit, in: as drawn
    expect(d[0]).toEqual(own);
    // lit, out: its colour desaturated, at the lit opacity
    expect(desaturated(d[1]!.fill!, own.fill!)).toBe(true);
    expect(d[1]!.opacity).toBe(own.opacity);
    // dimmed, in: its colour, at the dimmed opacity
    expect(d[3]).toEqual({ ...own, opacity: DIM_OPACITY });
    // dimmed, out: desaturated and dimmed
    for (const i of [2, 4]) {
      expect(desaturated(d[i]!.fill!, own.fill!)).toBe(true);
      expect(d[i]!.opacity).toBe(DIM_OPACITY);
    }
    const states = new Set(d.map((s) => JSON.stringify([s.fill, s.opacity])));
    expect(states.size).toBe(4);
  });
});

// Every mark a brush takes rows of. Four rows at x a..d; the box holds a, b.
const AB = { g: cat(["a", "b", "c", "d"]), v: f64([1, 2, 3, 4]) };
const byG = (mark: unknown, extra: object = {}) => ({
  view: "chart",
  source: "data/a.csv",
  mark,
  encoding: { x: { field: "g", type: "nominal" }, y: { field: "v", type: "quantitative" }, ...extra },
});
const MARKS: [string, object, Answer][] = [
  ["bar", byG("bar"), answer(layer("bar", 4, AB))],
  ["scatter", byG("scatter"), answer(layer("scatter", 4, AB))],
  ["line (points drawn)", byG({ type: "line", point: true }), answer(layer("line", 4, AB))],
  ["area (points drawn)", byG({ type: "area", point: true }), answer(layer("area", 4, AB))],
  [
    "scatter coloured by value",
    byG("scatter", { color: { field: "v", type: "quantitative" } }),
    answer(layer("scatter", 4, AB)),
  ],
  [
    "heatmap",
    {
      view: "chart",
      source: "data/a.csv",
      mark: "heatmap",
      encoding: {
        x: { field: "g", type: "nominal" },
        y: { field: "h", type: "nominal" },
        color: { field: "v", type: "quantitative" },
      },
    },
    answer(layer("heatmap", 4, { ...AB, h: cat(["p", "p", "p", "p"]) })),
  ],
  [
    "boxplot",
    byG("boxplot"),
    answer(
      layer("boxplot", 4, {
        g: AB.g,
        $lo: f64([1, 1, 1, 1]),
        $q1: f64([2, 2, 2, 2]),
        $mid: f64([3, 3, 3, 3]),
        $q3: f64([4, 4, 4, 4]),
        $hi: f64([5, 5, 5, 5]),
      }),
    ),
  ],
  [
    "errorbar",
    byG({ type: "errorbar", color: "#5470c6" }),
    answer(layer("errorbar", 4, { g: AB.g, $lo: f64([1, 1, 1, 1]), $mid: f64([2, 2, 2, 2]), $hi: f64([3, 3, 3, 3]) })),
  ],
];

const colours = (d: Drawn) => [d.fill, d.stroke].filter((c): c is string => !!c && lightness(c) < 0.99);

describe.each(MARKS)("out of a brush that writes nothing, a %s", (_, doc, a) => {
  it("keeps its opacity and loses its colour, its lightness kept; in the brush, as drawn", async () => {
    const chart = mount(new MarkingStore(), doc, a, null);
    await settle();
    const before = [0, 1, 2, 3].map((i) => drawn(chart, 0, i));
    for (const d of before) expect(colours(d).length).toBeGreaterThan(0);
    await box(chart, [-0.5, 1.5]);
    const after = [0, 1, 2, 3].map((i) => drawn(chart, 0, i));
    expect(after.slice(0, 2)).toEqual(before.slice(0, 2));
    for (const i of [2, 3]) {
      expect(after[i]!.opacity).toBe(before[i]!.opacity);
      const was = colours(before[i]!);
      const now = colours(after[i]!);
      expect(now.length).toBe(was.length);
      now.forEach((c, k) => expect(desaturated(c, was[k]!), `${c} from ${was[k]}`).toBe(true));
    }
  });
});

it("an errorbar with no colour of its own is drawn #555, as before its colour came from ECharts", async () => {
  const [, doc, a] = MARKS.find(([name]) => name === "errorbar")!;
  const chart = mount(new MarkingStore(), { ...doc, mark: "errorbar" }, a, null);
  await settle();
  expect(drawn(chart, 0, 0).stroke).toBe("#555");
});

// P43's demo scene: a layered stack and points over it; a box over one stack
// segment only. Every point is out of the box and must stay visible over the
// bar it is drawn on.
const c = stackCase("an upright bar of rows");
const points = layer("scatter", 8, { item: cat(c.data.item as string[]), value: f64(c.data.value as number[]) });
const LAYERED = {
  view: "chart",
  source: "data/a.csv",
  layer: [
    { mark: { type: "bar", stack: true }, encoding: c.spec.encoding },
    {
      mark: { type: "scatter", color: "#444444" },
      encoding: { x: { field: "item", type: "nominal" }, y: { field: "value", type: "quantitative" } },
    },
  ],
};

describe("a point over a bar, out of a box over one segment", () => {
  it("stays visible: drawn apart from the bars, at its own opacity", async () => {
    const chart = mount(new MarkingStore(), LAYERED, answer(c.answer.layers[0]!, points), null);
    await settle();
    const opt = chart.getOption() as { series: { type: string }[] };
    const scatter = opt.series.findIndex((s) => s.type === "scatter");
    const bars = opt.series.flatMap((s, i) => (s.type === "bar" ? [i] : []));
    const before = drawn(chart, scatter, 0);
    // a box over the first segment of the first bar series, only
    const seg = chart.convertToPixel({ seriesIndex: bars[0]! }, [0, 0]) as number[];
    act(() => chart.dispatchAction({ type: "brush", areas: [{ brushType: "rect", range: [[seg[0]! - 3, seg[0]! + 3], [seg[1]! - 3, seg[1]!]] }] }));
    await settle();
    const barFills = bars.flatMap((b) => [0, 1, 2, 3].flatMap((i) => {
      try {
        return [drawn(chart, b, i).fill!];
      } catch {
        return [];
      }
    }));
    for (let i = 0; i < 8; i++) {
      const p = drawn(chart, scatter, i);
      expect(p.opacity).toBe(before.opacity);
      expect(desaturated(p.fill!, before.fill!)).toBe(true);
      // a bar out of the box keeps its lightness: the point's grey is not any bar's
      for (const f of barFills) expect(Math.abs(lightness(p.fill!) - lightness(f))).toBeGreaterThan(0.2);
    }
  });
});

// Found in P45's demo (Chromium): a box drawn from ON a mark -- the pointer
// hovering a bar when the drag began -- greyed the other marks while hover
// held them blurred, and zrender restores a state's saved style on leaving it
// (Displayable._innerSaveToNormal keeps the whole style), so as the pointer
// left they took their colours back: the box's outside no longer showed.
// Through zrender's own handler: hover, then drag, then move away.
describe("a box drawn from on a mark", () => {
  it("keeps its outside grey once the pointer leaves", async () => {
    const chart = mount(new MarkingStore(), LAYERED, answer(c.answer.layers[0]!, points), null);
    await settle();
    const opt = chart.getOption() as { series: { type: string }[] };
    const bars = opt.series.flatMap((s, i) => (s.type === "bar" ? [i] : []));
    type Layout = { x: number; y: number; width: number; height: number };
    type Model = { getSeriesByIndex(i: number): { getData(): { getItemLayout(i: number): Layout } } };
    const seg = (chart as unknown as { getModel(): Model }).getModel().getSeriesByIndex(bars[0]!).getData().getItemLayout(0);
    const own = bars.map((b) => [1, 2].map((i) => drawn(chart, b, i).fill!));
    type Handler = Record<"mousemove" | "mousedown" | "mouseup", (e: object) => void>;
    const h = (chart.getZr() as unknown as { handler: Handler }).handler;
    const at = ([x, y]: number[]) => ({ zrX: x, zrY: y, offsetX: x, offsetY: y });
    const from = [seg.x + seg.width / 2, seg.y + seg.height * 0.3];
    const to = [seg.x + seg.width * 0.8, seg.y + seg.height * 0.7];
    // the box tool picked (the toolbox's button does this)
    act(() => chart.dispatchAction({ type: "takeGlobalCursor", key: "brush", brushOption: { brushType: "rect", brushMode: "single" } }));
    act(() => h.mousemove(at(from)));
    await settle();
    act(() => {
      h.mousedown(at(from));
      for (let k = 1; k <= 5; k++) h.mousemove(at([from[0]! + ((to[0]! - from[0]!) * k) / 5, from[1]! + ((to[1]! - from[1]!) * k) / 5]));
      h.mouseup(at(to));
    });
    await settle();
    act(() => h.mousemove(at([590, 390])));
    await settle();
    // the other groups' segments (items 1, 2 of each bar series) are out of the box
    bars.forEach((b, k) =>
      [1, 2].forEach((i, j) => expect(desaturated(drawn(chart, b, i).fill!, own[k]![j]!), `series ${b} item ${i}`).toBe(true)),
    );
  });
});
