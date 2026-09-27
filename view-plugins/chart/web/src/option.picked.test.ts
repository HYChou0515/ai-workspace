/**
 * #861 D3: an aggregated bar a marking picks part of is drawn whole, dimmed,
 * with a lit bar in front whose value is the same aggregate over the picked
 * rows (`Options.picked`, folded by `partials.ts`). The lit bar is narrower
 * than the bar (`LIT_SHARE`, a third) at its left edge [user, review #862],
 * so a picked mean taller than the bar hides nothing.
 * Drawn with REAL ECharts (SSR): the oracle for where the lit bar goes is
 * where ECharts laid the bar out, and the pixel ECharts gives the value.
 */
import * as echarts from "echarts/core";
import { SVGRenderer } from "echarts/renderers";
import { describe, expect, it } from "vitest";

import { DIM_OPACITY } from "./highlight";
import "./echarts"; // registers the chart's series + components
import { barAtOf } from "./barAt";
import { type Answer, toOption } from "./option";
import { answer, base, cat, f64, layer } from "./testAnswer";
import { drawn } from "./testDrawn";

echarts.use([SVGRenderer]);

type Rect = { x: number; y: number; width: number; height: number };
type El = { shape?: Rect; isGroup?: boolean; childAt?: (i: number) => El | undefined; silent?: boolean };
type Data = {
  count(): number;
  get(dim: string, i: number): number;
  getCalculationInfo(key: string): string;
  getItemLayout(i: number): Rect | undefined;
  getItemGraphicEl(i: number): El | undefined;
};
type Model = { getSeriesByIndex(i: number): { getData(): Data } };

/** A rect as its edges, whatever the sign of its width and height. */
const edges = (r: Rect) => ({
  left: Math.min(r.x, r.x + r.width),
  right: Math.max(r.x, r.x + r.width),
  top: Math.min(r.y, r.y + r.height),
  bottom: Math.max(r.y, r.y + r.height),
});

/** Draw with real ECharts; the chart's own `barAt` reads its bar layout. */
function draw(doc: object, a: Answer, picked?: ((number | null)[] | null)[], size = { width: 600, height: 400 }) {
  let chart: echarts.ECharts | undefined;
  const model = () => (chart as unknown as { getModel(): Model }).getModel();
  // the chart's own reader (what ChartView hands `toOption`)
  const barAt = barAtOf(() => chart ?? null);
  const built = toOption(doc, a, picked ? { picked, barAt } : { barAt });
  chart = echarts.init(null, null, { renderer: "svg", ssr: true, ...size });
  chart.setOption(built.option, true);
  const all = built.option.series as { type: string; silent?: boolean }[];
  const bars = all.flatMap((s, i) => (s.type === "bar" ? [i] : []));
  const lits = all.flatMap((s, i) => (s.type === "custom" ? [i] : []));
  const tops = bars.map((i) => {
    const data = model().getSeriesByIndex(i).getData();
    const dim = data.getCalculationInfo("stackResultDimension") ?? "y";
    return Array.from({ length: data.count() }, (_, j) => data.get(dim, j));
  });
  /** Where ECharts laid out bar j of bar series `s`. */
  const bar = (s: number, j: number) => edges(model().getSeriesByIndex(s).getData().getItemLayout(j)!);
  /** The lit rect drawn for item j of lit series `s`, or null for none. */
  const lit = (s: number, j: number) => {
    let el = model().getSeriesByIndex(s).getData().getItemGraphicEl(j);
    while (el?.isGroup) el = el.childAt?.(0);
    return el?.shape ? edges(el.shape) : null;
  };
  const px = (x: number | string, y: number | string) => chart!.convertToPixel({ gridIndex: 0 }, [x, y] as never) as unknown as number[];
  const style = (s: number, j: number) => drawn(chart!, s, j);
  return { built, bars, lits, tops, bar, lit, px, style, chart: chart!, all };
}

const COUNTS = answer(layer("bar", 2, { group: cat(["g1", "g2"]), n: f64([10, 4]) }, { measured: ["n"] }));
const BAR = {
  ...base,
  keys: ["group", "item"],
  mark: "bar",
  encoding: { x: { field: "group", type: "nominal" }, y: { field: "n", type: "quantitative", aggregate: "count" } },
};

describe("a bar the marking picks part of (#861 D3)", () => {
  it("draws the bar whole, dimmed, and in front a lit bar a third its width at its left edge, from the axis to the picked count", () => {
    // g1 counts 10, 2 of them picked; g2 counts 4, none picked
    const { bars, lits, tops, bar, lit, px, style, chart } = draw(BAR, COUNTS, [[2, null]]);
    expect(bars).toHaveLength(1);
    expect(lits).toHaveLength(1);
    // the bar keeps its whole value, dimmed
    expect(tops).toEqual([[10, 4]]);
    expect(style(bars[0]!, 0).opacity).toBe(DIM_OPACITY);
    const whole = bar(bars[0]!, 0);
    const part = lit(lits[0]!, 0)!;
    expect(part.left).toBeCloseTo(whole.left, 5);
    expect(part.right - part.left).toBeCloseTo((whole.right - whole.left) / 3, 5);
    // from the axis (the bar's own base) to the pixel of the value 2
    expect(part.bottom).toBeCloseTo(whole.bottom, 5);
    expect(part.top).toBeCloseTo(px("g1", 2)[1]!, 5);
    // lit in the bar's own colour, at full strength
    const drawnLit = style(lits[0]!, 0);
    expect(drawnLit.opacity).toBe(1);
    expect(drawnLit.fill).toBe(style(bars[0]!, 0).fill);
    // nothing picked in g2: no lit bar there
    expect(lit(lits[0]!, 1)).toBeNull();
    chart.dispose();
  });

  it("draws a picked value taller than the bar at its own height, the bar still whole behind it", () => {
    // a mean: the picked rows' mean 12 over the bar's 10
    const { bars, lits, tops, bar, lit, px, chart } = draw(BAR, COUNTS, [[12, 1]]);
    expect(tops).toEqual([[10, 4]]);
    const part = lit(lits[0]!, 0)!;
    expect(part.top).toBeCloseTo(px("g1", 12)[1]!, 5);
    // above the bar's own top: both are seen
    expect(part.top).toBeLessThan(bar(bars[0]!, 0).top);
    chart.dispose();
  });

  it("draws a picked value on the other side of 0 there, and the bar whole", () => {
    const { bars, lits, tops, lit, px, chart } = draw(BAR, COUNTS, [[-3, null]]);
    expect(tops[0]![0]).toBe(10);
    const part = lit(lits[0]!, 0)!;
    expect(part.top).toBeCloseTo(px("g1", 0)[1]!, 5);
    expect(part.bottom).toBeCloseTo(px("g1", -3)[1]!, 5);
    expect(bars).toHaveLength(1);
    chart.dispose();
  });

  it("puts each colour's lit bar at its own bar's left edge", () => {
    const a = answer(
      layer("bar", 4, { group: cat(["g1", "g1", "g2", "g2"]), region: cat(["n", "s", "n", "s"]), n: f64([6, 8, 2, 4]) }, { measured: ["n"] }),
    );
    const doc = { ...BAR, encoding: { ...BAR.encoding, color: { field: "region", type: "nominal" } } };
    const unsplit = draw(doc, a);
    const { built, bars, lits, bar, lit, style, chart } = draw(doc, a, [[1, null, 2, 3]]);
    // n: bar, s: bar; a lit series for each
    expect(bars).toHaveLength(2);
    expect(lits).toHaveLength(2);
    // the bars are where the unsplit chart draws them
    for (const [k, s] of bars.entries()) {
      for (const j of [0, 1]) expect(bar(s, j)).toEqual(unsplit.bar(unsplit.bars[k]!, j));
    }
    // each lit bar at its own bar's left edge, in its colour
    for (const [k, l] of lits.entries()) {
      for (const j of [0, 1]) {
        const part = lit(l, j);
        if (part) expect(part.left).toBeCloseTo(bar(bars[k]!, j).left, 5);
      }
      const j = lit(l, 0) ? 0 : 1;
      expect(style(l, j).fill).toBe(style(bars[k]!, j).fill);
    }
    // n picked in g1 only (row 0); s in g2 (row 3) and g1 (row 1: none)
    expect(lits.map((l) => [lit(l, 0) !== null, lit(l, 1) !== null])).toEqual([
      [true, true],
      [false, true],
    ]);
    // a lit series is its colour's legend entry
    expect(built.names).toEqual(["n", "s", "n", "s"]);
    unsplit.chart.dispose();
    chart.dispose();
  });

  it("on a number x, as on a category one", () => {
    const a = answer(layer("bar", 2, { pos: f64([1, 3]), n: f64([10, 4]) }, { measured: ["n"] }));
    const doc = { ...BAR, encoding: { x: { field: "pos", type: "quantitative" }, y: BAR.encoding.y } };
    const { bars, lits, bar, lit, px, chart } = draw(doc, a, [[2, null]]);
    const whole = bar(bars[0]!, 0);
    const part = lit(lits[0]!, 0)!;
    expect(part.left).toBeCloseTo(whole.left, 5);
    expect(part.right - part.left).toBeCloseTo((whole.right - whole.left) / 3, 5);
    expect(part.top).toBeCloseTo(px(1, 2)[1]!, 5);
    chart.dispose();
  });

  it("lights each row's own bar where its category is drawn, in a sorted order too", () => {
    // descending: g2 is drawn left of g1. The picked values are per layer
    // row (the sandbox's drawn row), whatever order the axis shows them in.
    const doc = { ...BAR, encoding: { ...BAR.encoding, x: { field: "group", type: "nominal", sort: "descending" } } };
    const { built, bars, lits, bar, lit, px, chart } = draw(doc, COUNTS, [[2, 3]]);
    const at = (row: number) => built.series[bars[0]!]!.rows.indexOf(row);
    for (const [row, value, name] of [
      [0, 2, "g1"],
      [1, 3, "g2"],
    ] as const) {
      const part = lit(lits[0]!, at(row))!;
      expect(part.left).toBeCloseTo(bar(bars[0]!, at(row)).left, 5);
      expect(part.top).toBeCloseTo(px(name, value)[1]!, 5);
    }
    expect(bar(bars[0]!, at(1)).left).toBeLessThan(bar(bars[0]!, at(0)).left);
    chart.dispose();
  });

  it("asks where the bar is on every draw: a resized chart keeps the lit bar at the bar's edge", () => {
    const { bars, lits, bar, lit, chart } = draw(BAR, COUNTS, [[2, null]]);
    const before = bar(bars[0]!, 0).left;
    chart.resize({ width: 900, height: 400 });
    const whole = bar(bars[0]!, 0);
    expect(whole.left).not.toBeCloseTo(before, 0);
    expect(lit(lits[0]!, 0)!.left).toBeCloseTo(whole.left, 5);
    chart.dispose();
  });

  it("maps the bar to the layer's rows; the lit bar takes no hover, click or brush of its own", () => {
    const { built, bars, lits, all, chart } = draw(BAR, COUNTS, [[2, null]]);
    expect(built.series[bars[0]!]!.rows).toEqual([0, 1]);
    expect(built.series[lits[0]!]!.rows).toEqual([]);
    expect(all[lits[0]!]!.silent).toBe(true);
    chart.dispose();
  });

  it("keeps a hovered bar's lit bar lit: hovering a split bar fades nothing", () => {
    // The lit bar is silent, so a hover lands on the bar behind it; a hover
    // that faded every other element faded the very lit bar it was over.
    const { bars, lits, chart } = draw(BAR, COUNTS, [[2, 3]]);
    // ECharts marks a faded element's hover state 1 (blur), a hovered one 2
    const state = (s: number, j: number) => (itemEl(chart, s, j) as { hoverState?: number } | undefined)?.hoverState ?? 0;
    chart.dispatchAction({ type: "highlight", seriesIndex: bars[0], dataIndex: 0 });
    expect(state(bars[0]!, 0)).toBe(2);
    expect([state(lits[0]!, 0), state(lits[0]!, 1), state(bars[0]!, 1)]).not.toContain(1);
    chart.dispose();
  });

  it("says the picked value in the bar's tooltip", () => {
    const { built, bars, chart } = draw(BAR, COUNTS, [[2, null]]);
    const tip = (built.option.tooltip as { formatter: (p: object) => string }).formatter;
    expect(tip({ seriesIndex: bars[0], dataIndex: 0 })).toContain("picked: <b>2</b>");
    expect(tip({ seriesIndex: bars[0], dataIndex: 1 })).not.toContain("picked");
    chart.dispose();
  });

  it("(control) with nothing picked, the bar is drawn as before: one series", () => {
    const { built, chart } = draw(BAR, COUNTS, [null]);
    expect(built.series).toHaveLength(1);
    chart.dispose();
  });

  it("on a log axis is lit whole: there is no 0 to start a part at", () => {
    const doc = { ...BAR, encoding: { ...BAR.encoding, y: { ...BAR.encoding.y, scale: { type: "log" } } } };
    const { built, chart } = draw(doc, COUNTS, [[2, null]]);
    expect(built.series).toHaveLength(1);
    chart.dispose();
  });

  it("on a horizontal bar, at the bar's start edge", () => {
    const doc = { ...BAR, encoding: { y: { field: "group", type: "nominal" }, x: { field: "n", type: "quantitative", aggregate: "count" } } };
    const { bars, lits, bar, lit, px, chart } = draw(doc, COUNTS, [[2, null]]);
    const whole = bar(bars[0]!, 0);
    const part = lit(lits[0]!, 0)!;
    // the layout's own origin edge across the bar, a third of its thickness
    const origin = model_y(chart, bars[0]!, 0);
    expect(Math.abs(origin - part.top) < 1e-6 || Math.abs(origin - part.bottom) < 1e-6).toBe(true);
    expect(part.bottom - part.top).toBeCloseTo((whole.bottom - whole.top) / 3, 5);
    expect(part.left).toBeCloseTo(whole.left, 5);
    expect(part.right).toBeCloseTo(px(2, "g1")[0]!, 5);
    chart.dispose();
  });
});

/** The element drawn for item j of series `s` (a group's first child). */
function itemEl(chart: echarts.ECharts, s: number, j: number): El | undefined {
  const model = (chart as unknown as { getModel(): Model }).getModel();
  let el = model.getSeriesByIndex(s).getData().getItemGraphicEl(j);
  while (el?.isGroup) el = el.childAt?.(0);
  return el;
}

/** The y ECharts gives bar j's layout (its origin edge across a horizontal bar). */
function model_y(chart: echarts.ECharts, s: number, j: number): number {
  const model = (chart as unknown as { getModel(): Model }).getModel();
  return model.getSeriesByIndex(s).getData().getItemLayout(j)!.y;
}

describe("a stacked bar's segments (#861 D3)", () => {
  // segments (g1, n) 6, (g1, s) 8, (g2, n) 2, (g2, s) 4
  const STACK = answer(
    layer("bar", 4, { group: cat(["g1", "g1", "g2", "g2"]), region: cat(["n", "s", "n", "s"]), v: f64([6, 8, 2, 4]) }, { measured: ["v"] }),
  );
  const doc = {
    ...base,
    keys: ["item"],
    mark: { type: "bar", stack: true },
    encoding: {
      x: { field: "group", type: "nominal" },
      y: { field: "v", type: "quantitative" },
      color: { field: "region", type: "nominal" },
    },
  };

  it("light each segment's picked part from its own base, the stack whole", () => {
    const { built, bars, lits, tops, bar, lit, px, chart } = draw(doc, STACK, [[1, 3, null, 4]]);
    // the stack as unpicked: n to 6 | 2, s on top to 14 | 6
    expect(tops).toEqual([
      [6, 2],
      [14, 6],
    ]);
    // n in g1: 0 to 1; s in g1: from its base 6 to 6 + 3; s in g2: 2 to 2 + 4
    const n = lit(lits[0]!, 0)!;
    expect(n.bottom).toBeCloseTo(bar(bars[0]!, 0).bottom, 5);
    expect(n.top).toBeCloseTo(px("g1", 1)[1]!, 5);
    expect(n.left).toBeCloseTo(bar(bars[0]!, 0).left, 5);
    expect(lit(lits[0]!, 1)).toBeNull();
    const s1 = lit(lits[1]!, 0)!;
    expect(s1.bottom).toBeCloseTo(bar(bars[1]!, 0).bottom, 5);
    expect(s1.top).toBeCloseTo(px("g1", 9)[1]!, 5);
    const s2 = lit(lits[1]!, 1)!;
    expect(s2.top).toBeCloseTo(px("g2", 6)[1]!, 5);
    expect(built.notes).not.toContain("a stack is lit whole: a picked part does not fit inside its segment");
    chart.dispose();
  });

  it("is lit whole when a picked part does not fit inside its segment, and says so", () => {
    const { built, lits, chart } = draw(doc, STACK, [[7, null, null, null]]);
    expect(lits).toHaveLength(0);
    expect(built.series).toHaveLength(2);
    expect(built.notes).toContain("a stack is lit whole: a picked part does not fit inside its segment");
    chart.dispose();
  });
});
