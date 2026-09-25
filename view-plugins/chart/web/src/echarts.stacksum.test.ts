/**
 * #847/#848 PR 5 P37 row 12: a stacked series draws, per slot, the SUM of its
 * rows as ONE point, which stands for all of them. P35 row 7 split a series
 * into one "piece" per row-rank so rows at one slot stacked on each other;
 * a slot with 10,000 rows made 10,000 series, and each piece got a point per
 * slot (a filler where it had no row): 500 rows in one of 200 categories
 * drew 100,000 points. Now there is one series per colour key and one point
 * per slot; the stack still ends at the slot's sum.
 *
 * Against REAL ECharts (SSR). The oracle is pandas:
 *
 *   df = pd.DataFrame({"c": list("ppqpqqqr"), "g": list("aaabbbab"),
 *                      "v": [3, 4, 7, 5, 1, 2, 6, 8]})
 *   df.pivot_table(index="g", columns="c", values="v", aggfunc="sum",
 *                  fill_value=0).cumsum()
 *
 *        p   q  r
 *   a    7  13  0
 *   b   12  16  8
 */
import * as echarts from "echarts/core";
import { SVGRenderer } from "echarts/renderers";
import { describe, expect, it } from "vitest";

import "./echarts"; // registers the chart's series + components
import { DIM_OPACITY } from "./highlight";
import { type Answer, toOption } from "./option";
import { type BrushSelected, selectionFromBrush, selectionFromLegend } from "./selection";
import { answer, base, cat, f64, layer } from "./testAnswer";

echarts.use([SVGRenderer]);

type Data = {
  count(): number;
  get(dim: string, i: number): number;
  getCalculationInfo(key: string): string;
  getItemVisual(i: number, k: string): { fill: string };
};
type Model = { getSeriesByIndex(i: number): { getData(): Data } };

function draw(doc: object, a: Answer) {
  const built = toOption(doc, a);
  const chart = echarts.init(null, null, { renderer: "svg", ssr: true, width: 600, height: 400 });
  chart.setOption(built.option, true);
  const model = (chart as unknown as { getModel(): Model }).getModel();
  return { chart, built, model, svg: chart.renderToSVGString() };
}

const C = ["p", "p", "q", "p", "q", "q", "q", "r"];
const G = ["a", "a", "a", "b", "b", "b", "a", "b"];
const V = [3, 4, 7, 5, 1, 2, 6, 8];
// pandas, above: per colour key, the stack's top at p, q, r
const TOPS = [
  [7, 13, 0],
  [12, 16, 8],
];
const rowsOf = (mark = "bar") => answer(layer(mark, 8, { c: cat(C), g: cat(G), v: f64(V) }));
const doc = (mark: object, x: object, y: object, extra: Record<string, object> = {}) => ({
  ...base,
  mark,
  encoding: { x, y, color: { field: "g", type: "nominal" }, ...extra },
});
const byCategory = { field: "c", type: "nominal" };
const value = { field: "v", type: "quantitative" };
const upright = doc({ type: "bar", stack: true }, byCategory, value);
const horizontal = doc({ type: "bar", stack: true }, value, byCategory);

const tops = (model: Model, series: number) => {
  const data = model.getSeriesByIndex(series).getData();
  const dim = data.getCalculationInfo("stackResultDimension");
  return Array.from({ length: data.count() }, (_, j) => data.get(dim, j));
};
const valueOf = (d: unknown): unknown[] => (Array.isArray(d) ? d : (d as { value: unknown[] }).value);
const dataOf = (built: ReturnType<typeof toOption>, i: number) => (built.option.series as { data: unknown[] }[])[i].data;

describe("a stacked series draws one point per slot: the sum of its rows there (P37 row 12)", () => {
  it("one series per colour key and one point per slot, however many rows share a slot (red before: a series per row-rank)", () => {
    const n = 10_000;
    const cs = [...Array(n).fill("p"), "q", "r"];
    const gs = cs.map((_, i) => (i % 2 ? "a" : "b"));
    const a = answer(layer("bar", cs.length, { c: cat(cs), g: cat(gs), v: f64(cs.map((_, i) => 1 + (i % 7))) }));
    const built = toOption(upright, a);
    expect(built.series).toHaveLength(2);
    expect(built.series.map((_, i) => dataOf(built, i).length)).toEqual([3, 3]);
    // the point at p stands for every one of its series' rows there
    const atP = built.series.map((s) => s.rows[0]);
    expect(atP.map((r) => (Array.isArray(r) ? r.length : 1))).toEqual([n / 2, n / 2]);
  });

  it("on a time x the same: 3,000 rows at one x and 1,000 alone are one series of 1,001 points", () => {
    const T0 = Date.parse("2024-01-01");
    const xs = [...Array(3000).fill(T0), ...Array.from({ length: 1000 }, (_, i) => T0 + (i + 1) * 86_400_000)];
    const time = f64(xs) as { data: string };
    const a = answer(layer("bar", xs.length, { t: { kind: "time", data: time.data }, v: f64(xs.map((_, i) => 1 + (i % 7))) }));
    const built = toOption({ ...base, mark: { type: "bar", stack: true }, encoding: { x: { field: "t", type: "temporal" }, y: value } }, a);
    expect(built.series).toHaveLength(1);
    expect(dataOf(built, 0)).toHaveLength(1001);
    // y first off a category axis (P29): the sum at the crowded x
    expect(valueOf(dataOf(built, 0)[0])[0]).toBe(xs.slice(0, 3000).reduce((s, _, i) => s + 1 + (i % 7), 0));
  });

  it.each([
    ["an upright bar", upright],
    ["a horizontal bar", horizontal],
    ["an area on a category x", doc({ type: "area", stack: true }, byCategory, value)],
  ])("%s ends each stack where pandas sums it", (_, d) => {
    const { chart, model, svg } = draw(d, rowsOf());
    expect([tops(model, 0), tops(model, 1)]).toEqual(TOPS);
    expect(svg).not.toContain("Infinity");
    expect(svg).not.toContain("NaN");
    chart.dispose();
  });

  it("a brush over a summed point selects every row it stands for", () => {
    const { chart, built } = draw(horizontal, rowsOf());
    // a at q: rows 2 (7) and 6 (6)
    const j = built.series[0].rows.findIndex((r) => Array.isArray(r) && r.includes(6));
    expect(built.series[0].rows[j]).toEqual([2, 6]);
    const brushed: BrushSelected = {
      batch: [{ areas: [{ brushType: "rect", coordRange: [[0, 99], [0, 9]] }], selected: [{ seriesIndex: 0, dataIndex: [j] }] }],
    };
    expect(selectionFromBrush(brushed, built)).toEqual([{ source: "brush", layer: 0, rows: [2, 6] }]);
    // hiding b leaves every row of a, those its sums stand for included
    expect(selectionFromLegend({ a: true, b: false }, built)).toEqual([{ source: "legend", layer: 0, rows: [0, 1, 2, 6] }]);
    chart.dispose();
  });

  it("a summed point is lit if any of its rows is lit", () => {
    // lit: row 1 only (a at p, with row 0) and row 7 (b at r)
    const lit = [[false, true, false, false, false, false, false, true]];
    const built = toOption(upright, rowsOf(), { lit });
    const dimmed = (i: number) => dataOf(built, i).map((d) => (d as { itemStyle?: { opacity?: number } }).itemStyle?.opacity === DIM_OPACITY);
    // a: p (rows 0, 1: one lit) lit; q (rows 2, 6: none) dim; r a filler
    // (clear, not dimmed); b: p, q dim, r lit
    expect(dimmed(0)).toEqual([false, true, false]);
    expect(dimmed(1)).toEqual([true, true, false]);
  });

  it("its tooltip says it is the sum of N rows, not one row's fields", () => {
    const built = toOption(doc({ type: "bar", stack: true }, byCategory, value, { tooltip: { field: "v2", type: "quantitative" } }), answer(
      layer("bar", 8, { c: cat(C), g: cat(G), v: f64(V), v2: f64([10, 20, 30, 40, 50, 60, 70, 80]) }),
    ));
    const tip = (built.option.tooltip as { formatter: (p: object) => string }).formatter;
    // a at p: rows 0 (3) and 1 (4)
    const summed = tip({ seriesIndex: 0, dataIndex: 0 });
    expect(summed).toContain("<b>7</b>");
    expect(summed).toContain("sum of 2 rows");
    // what the two rows share is said; what differs between them is not
    expect(summed).toContain("c: <b>p</b>");
    expect(summed).toContain("g: <b>a</b>");
    for (const one of ["<b>3</b>", "<b>4</b>", "<b>10</b>", "<b>20</b>"]) expect(summed).not.toContain(one);
    // a single row's point is that row, as ever: b at r, row 7
    const single = tip({ seriesIndex: 1, dataIndex: 2 });
    expect(single).toContain("v: <b>8</b>");
    expect(single).toContain("v2: <b>80</b>");
    expect(single).not.toContain("sum of");
  });

  it("on a number x its tooltip says the sum it draws; rows with no value add nothing; a sum of none is none", () => {
    const area = { ...base, mark: { type: "area", stack: true }, encoding: { x: { field: "x", type: "quantitative" }, y: value } };
    // x=1: rows 0 (2), 1 (5), 2 (no value); x=2: rows 3, 4, neither with a value
    const built = toOption(area, answer(layer("area", 5, { x: f64([1, 1, 1, 2, 2]), v: f64([2, 5, null, null, null]) })));
    const tip = (built.option.tooltip as { formatter: (p: object) => string }).formatter;
    expect(tip({ seriesIndex: 0, dataIndex: 0 })).toContain("v: <b>7</b> (sum of 3 rows)");
    expect(tip({ seriesIndex: 0, dataIndex: 1 })).toContain("v: <b>—</b> (sum of 2 rows)");
    // y first off a category axis (P29): the drawn sums
    expect(dataOf(built, 0).map((d) => valueOf(d)[0])).toEqual([7, null]);
  });

  it("a text label on a summed point says nothing of one row", () => {
    const built = toOption(doc({ type: "bar", stack: true }, byCategory, value, { text: { field: "v", type: "quantitative" } }), rowsOf());
    const label = ((built.option.series as { label: { formatter: (p: object) => string } }[])[0]).label.formatter;
    expect(label({ seriesIndex: 0, dataIndex: 0 })).toBe(""); // a at p: two rows
    const b = ((built.option.series as { label: { formatter: (p: object) => string } }[])[1]).label.formatter;
    expect(b({ seriesIndex: 1, dataIndex: 2 })).toBe("8"); // b at r: row 7 alone
  });

  it("a colour by value: a sum's colour value is its rows' when they share one, else none", () => {
    const byValue = {
      ...base,
      mark: { type: "bar", stack: true },
      encoding: { x: byCategory, y: value, color: { field: "w", type: "quantitative" } },
    };
    // p: rows 0, 1 share w 5; q: rows 2, 3 differ (1, 9); r: row 4 alone (9)
    const a = answer(layer("bar", 5, { c: cat(["p", "p", "q", "q", "r"]), v: f64([1, 2, 3, 4, 5]), w: f64([5, 5, 1, 9, 9]) }));
    const built = toOption(byValue, a);
    expect(built.series).toHaveLength(1);
    expect(dataOf(built, 0).map((d) => valueOf(d)[2])).toEqual([5, null, 9]);
  });
});
