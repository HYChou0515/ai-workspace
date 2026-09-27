/**
 * #861 D3: an aggregated bar a marking picks part of is drawn as the full bar
 * dimmed with, in front, a lit bar whose value is the same aggregate over the
 * picked rows (`Options.picked`, folded by `partials.ts`). Drawn with REAL
 * ECharts (SSR): where each part ends, its colour and opacity, its slot.
 */
import * as echarts from "echarts/core";
import { SVGRenderer } from "echarts/renderers";
import { describe, expect, it } from "vitest";

import { DIM_OPACITY } from "./highlight";
import "./echarts"; // registers the chart's series + components
import { type Answer, toOption } from "./option";
import { answer, base, cat, f64, layer } from "./testAnswer";
import { drawn } from "./testDrawn";

echarts.use([SVGRenderer]);

type Data = {
  count(): number;
  get(dim: string, i: number): number;
  getCalculationInfo(key: string): string;
  getItemLayout(i: number): { x: number; y: number; width: number; height: number } | undefined;
};
type Model = { getSeriesByIndex(i: number): { getData(): Data } };

/** Per series, where each bar ends (its stacked top), and its slot's left edge. */
function draw(doc: object, a: Answer, picked?: ((number | null)[] | null)[]) {
  const built = toOption(doc, a, picked ? { picked } : {});
  const chart = echarts.init(null, null, { renderer: "svg", ssr: true, width: 600, height: 400 });
  chart.setOption(built.option, true);
  const model = (chart as unknown as { getModel(): Model }).getModel();
  const tops = built.series.map((_, i) => {
    const data = model.getSeriesByIndex(i).getData();
    const dim = data.getCalculationInfo("stackResultDimension") ?? "y";
    return Array.from({ length: data.count() }, (_, j) => data.get(dim, j));
  });
  const lefts = built.series.map((_, i) => {
    const data = model.getSeriesByIndex(i).getData();
    return Array.from({ length: data.count() }, (_, j) => Math.round(data.getItemLayout(j)?.x ?? NaN));
  });
  const style = (s: number, j: number) => drawn(chart, s, j);
  return { built, tops, lefts, style, chart };
}

const COUNTS = answer(layer("bar", 2, { group: cat(["g1", "g2"]), n: f64([10, 4]) }, { measured: ["n"] }));
const BAR = {
  ...base,
  keys: ["group", "item"],
  mark: "bar",
  encoding: { x: { field: "group", type: "nominal" }, y: { field: "n", type: "quantitative", aggregate: "count" } },
};

describe("a bar the marking picks part of (#861 D3)", () => {
  it("draws the picked count lit from the axis, and the rest of the bar dimmed on it", () => {
    // g1 counts 10, 2 of them picked; g2 counts 4, none picked
    const { built, tops, style, chart } = draw(BAR, COUNTS, [[2, null]]);
    expect(built.series).toHaveLength(2);
    expect(tops).toEqual([
      [2, 0],
      [10, 4],
    ]);
    const [lit, rest] = [style(0, 0), style(1, 0)];
    expect(lit.opacity).toBe(1);
    expect(rest.opacity).toBe(DIM_OPACITY);
    // one bar in two parts: one colour
    expect(lit.fill).toBe(rest.fill);
    chart.dispose();
  });

  it("draws a picked value longer than the bar at its own length", () => {
    // a mean: the picked rows' mean 12 over the bar's 10 -- the rest of the
    // bar is 0, behind it; g2's picked 1 of 4 as before
    const { tops, chart } = draw(BAR, COUNTS, [[12, 1]]);
    expect(tops).toEqual([
      [12, 1],
      [12, 4],
    ]);
    chart.dispose();
  });

  it("draws a picked value on the other side of 0 there, and the bar whole", () => {
    const { tops, chart } = draw(BAR, COUNTS, [[-3, null]]);
    expect(tops[0]![0]).toBe(-3);
    expect(tops[1]![0]).toBe(10);
    chart.dispose();
  });

  it("keeps each colour's bar in its own slot, its two parts in one", () => {
    const a = answer(
      layer("bar", 4, { group: cat(["g1", "g1", "g2", "g2"]), region: cat(["n", "s", "n", "s"]), n: f64([6, 8, 2, 4]) }, { measured: ["n"] }),
    );
    const doc = { ...BAR, encoding: { ...BAR.encoding, color: { field: "region", type: "nominal" } } };
    const unsplit = draw(doc, a);
    const { built, lefts, tops, chart } = draw(doc, a, [[1, null, 2, 3]]);
    // n: lit, rest; s: lit, rest
    expect(built.series).toHaveLength(4);
    expect(lefts[0]).toEqual(lefts[1]);
    expect(lefts[2]).toEqual(lefts[3]);
    expect(lefts[0]).not.toEqual(lefts[2]);
    // the slots are where the unsplit chart draws its bars
    expect([lefts[0], lefts[2]]).toEqual(unsplit.lefts);
    expect(tops).toEqual([
      [1, 2],
      [6, 2],
      [0, 3],
      [8, 4],
    ]);
    // both parts of a colour are its legend entry
    expect(built.names).toEqual(["n", "n", "s", "s"]);
    unsplit.chart.dispose();
    chart.dispose();
  });

  it("on a number x, as on a category one", () => {
    const a = answer(layer("bar", 2, { pos: f64([1, 3]), n: f64([10, 4]) }, { measured: ["n"] }));
    const doc = { ...BAR, encoding: { x: { field: "pos", type: "quantitative" }, y: BAR.encoding.y } };
    const { tops, lefts, chart } = draw(doc, a, [[2, null]]);
    expect(tops).toEqual([
      [2, 0],
      [10, 4],
    ]);
    expect(lefts[0]).toEqual(lefts[1]);
    chart.dispose();
  });

  it("maps both parts to the layer's rows, so a brush over either selects the bar", () => {
    const { built, chart } = draw(BAR, COUNTS, [[2, null]]);
    expect(built.series.map((s) => s.rows)).toEqual([
      [0, 1],
      [0, 1],
    ]);
    chart.dispose();
  });

  it("says the picked value in the tooltip", () => {
    const { built, chart } = draw(BAR, COUNTS, [[2, null]]);
    const tip = (built.option.tooltip as { formatter: (p: object) => string }).formatter;
    expect(tip({ seriesIndex: 0, dataIndex: 0 })).toContain("picked: <b>2</b>");
    expect(tip({ seriesIndex: 1, dataIndex: 0 })).toContain("picked: <b>2</b>");
    expect(tip({ seriesIndex: 1, dataIndex: 1 })).not.toContain("picked");
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
});

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

  it("light each segment's picked part at its own base, the stack's height kept", () => {
    const { tops, built, chart } = draw(doc, STACK, [[1, 3, null, 4]]);
    // n: lit 1 | 0, rest to 6 | 2; then s on top: lit to 6+3 | 2+4, rest to 14 | 6
    expect(tops).toEqual([
      [1, 0],
      [6, 2],
      [9, 6],
      [14, 6],
    ]);
    expect(built.notes).not.toContain("a stack is lit whole: a picked part does not fit inside its segment");
    chart.dispose();
  });

  it("is lit whole when a picked part does not fit inside its segment, and says so", () => {
    const { built, chart } = draw(doc, STACK, [[7, null, null, null]]);
    expect(built.series).toHaveLength(2);
    expect(built.notes).toContain("a stack is lit whole: a picked part does not fit inside its segment");
    chart.dispose();
  });
});
