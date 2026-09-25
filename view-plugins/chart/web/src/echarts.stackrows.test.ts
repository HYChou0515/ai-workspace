/**
 * #847/#848 PR 5 P35 row 7: a stack of raw rows (no aggregate) reaches the
 * per-slot SUM, on any axis. Before, ECharts stacked one series on another
 * but drew a series' own rows at one category over each other from 0: a
 * horizontal bar of rows ended at 31.3 where the sum was 79.38 (P34's demo).
 * P35 made every row a piece of its stack (a series per row-rank); P37 row 12
 * draws the rows of one series at one slot as ONE point, their sum, which
 * stands for every one of them (echarts.stacksum.test.ts).
 *
 * Against REAL ECharts (SSR). The oracle is pandas: the per-category sums
 * below are `pd.DataFrame({"c": list("ppqpqqqr"), "g": list("aaabbbab"),
 * "v": [3, 4, 7, 5, 1, 2, 6, 8]}).groupby("c").v.sum()` -> p 12, q 16, r 8.
 */
import * as echarts from "echarts/core";
import { SVGRenderer } from "echarts/renderers";
import { describe, expect, it } from "vitest";

import "./echarts"; // registers the chart's series + components
import { type Answer, rowsAt, toOption } from "./option";
import { type BrushSelected, selectionFromBrush, selectionFromLegend } from "./selection";
import { answer, base, cat, f64, layer } from "./testAnswer";

echarts.use([SVGRenderer]);

type Data = {
  count(): number;
  get(dim: string, i: number): number;
  getCalculationInfo(key: string): string;
  getVisual(key: string): { fill: string };
};
type Model = {
  getComponent(main: string, i: number): { axis: { scale: { getExtent(): [number, number] } } };
  getSeriesByIndex(i: number): { getData(): Data };
};

function draw(doc: object, a: Answer) {
  const built = toOption(doc, a);
  const chart = echarts.init(null, null, { renderer: "svg", ssr: true, width: 600, height: 400 });
  chart.setOption(built.option, true);
  const model = (chart as unknown as { getModel(): Model }).getModel();
  return { chart, built, model };
}

const C = ["p", "p", "q", "p", "q", "q", "q", "r"];
const G = ["a", "a", "a", "b", "b", "b", "a", "b"];
const V = [3, 4, 7, 5, 1, 2, 6, 8];
const SUMS = new Map([["p", 12], ["q", 16], ["r", 8]]); // pandas, above
const rowsOf = () => answer(layer("bar", 8, { c: cat(C), g: cat(G), v: f64(V) }));

/** Per category, the highest stacked top any of its points reaches: how far
 * the stack is drawn there. Every value is positive, so that is the stack's
 * end. (A point's rows share its category: it is read from the first.) */
function extents(model: Model, built: ReturnType<typeof toOption>, category: (row: number) => string): Map<string, number> {
  const out = new Map<string, number>();
  built.series.forEach((s, i) => {
    const data = model.getSeriesByIndex(i).getData();
    const top = data.getCalculationInfo("stackResultDimension");
    for (let j = 0; j < data.count(); j++) {
      const [row] = rowsAt(s.rows[j]);
      if (row === undefined) continue;
      const c = category(row);
      out.set(c, Math.max(out.get(c) ?? 0, data.get(top, j)));
    }
  });
  return out;
}

const doc = (mark: object, x: object, y: object) => ({
  ...base,
  mark,
  encoding: { x, y, color: { field: "g", type: "nominal" } },
});
const horizontal = doc({ type: "bar", stack: true }, { field: "v", type: "quantitative" }, { field: "c", type: "nominal" });
const upright = doc({ type: "bar", stack: true }, { field: "c", type: "nominal" }, { field: "v", type: "quantitative" });

describe("rows stacked on a category axis reach their category's sum (P35 row 7, P37 row 12)", () => {
  it("a horizontal bar of rows ends at each category's sum (red before: rows of a series overlapped)", () => {
    const { chart, built, model } = draw(horizontal, rowsOf());
    expect(extents(model, built, (r) => C[r])).toEqual(SUMS);
    expect(model.getComponent("xAxis", 0).axis.scale.getExtent()[1]).toBeGreaterThanOrEqual(16);
    chart.dispose();
  });

  it("an upright bar of rows the same", () => {
    const { chart, built, model } = draw(upright, rowsOf());
    expect(extents(model, built, (r) => C[r])).toEqual(SUMS);
    expect(model.getComponent("yAxis", 0).axis.scale.getExtent()[1]).toBeGreaterThanOrEqual(16);
    chart.dispose();
  });

  it("an area on a category x the same", () => {
    const { chart, built, model } = draw(
      doc({ type: "area", stack: true }, { field: "c", type: "nominal" }, { field: "v", type: "quantitative" }),
      rowsOf(),
    );
    expect(extents(model, built, (r) => C[r])).toEqual(SUMS);
    chart.dispose();
  });

  // P37 row 12 [supersedes P35's "stacks the rows in order: a series' rows
  // sit on each other"]: a series' rows at one category are one point, their
  // sum; the next series sits on all of them.
  it("draws a series' rows at a category as their sum, and the next series on all of them", () => {
    const { chart, built, model } = draw(horizontal, rowsOf());
    // a point's rows -> its stacked top
    const top = new Map<string, number>();
    built.series.forEach((s, i) => {
      const data = model.getSeriesByIndex(i).getData();
      const dim = data.getCalculationInfo("stackResultDimension");
      s.rows.forEach((r, j) => r !== null && top.set(String(r), data.get(dim, j)));
    });
    // at p: a's rows 0 (3) and 1 (4), then b's row 3 (5)
    expect([top.get("0,1"), top.get("3")]).toEqual([7, 12]);
    // at q: a's rows 2 (7) and 6 (6), then b's 4 (1) and 5 (2)
    expect([top.get("2,6"), top.get("4,5")]).toEqual([13, 16]);
    chart.dispose();
  });

  it("draws each series in its own colour, and names it for the legend", () => {
    const { chart, built, model } = draw(horizontal, rowsOf());
    const fill = (i: number) => model.getSeriesByIndex(i).getData().getVisual("style").fill;
    const byName = new Map<string, Set<string>>();
    built.names.forEach((n, i) => byName.set(n as string, (byName.get(n as string) ?? new Set()).add(fill(i))));
    expect([...byName.keys()].sort()).toEqual(["a", "b"]);
    expect([...byName.values()].map((s) => s.size)).toEqual([1, 1]);
    expect(byName.get("a")).not.toEqual(byName.get("b"));
    chart.dispose();
  });

  // P37 row 12 [supersedes P35's "draws the pieces of a series with no
  // colour channel in one colour", which drew q's four rows as four series]
  it("draws a stack with no colour channel as one series, nameless in the legend, ending at the sums", () => {
    const one = { ...base, mark: { type: "bar", stack: true }, encoding: { x: { field: "v", type: "quantitative" }, y: { field: "c", type: "nominal" } } };
    const { chart, built, model } = draw(one, rowsOf());
    expect(built.series.length).toBe(1);
    expect(built.names.every((n) => n === undefined)).toBe(true);
    expect(extents(model, built, (r) => C[r])).toEqual(SUMS);
    chart.dispose();
  });

  // P37 row 12 [supersedes P35's "paints every piece by a colour by value"]:
  // a sum of rows whose colour values differ has no one colour value (none,
  // `stacksum`); a point of one row keeps its own, on the ramp.
  it("paints a point of one row by its colour value, from the ramp", () => {
    const byValue = {
      ...base,
      mark: { type: "bar", stack: true },
      encoding: { x: { field: "v", type: "quantitative" }, y: { field: "c", type: "nominal" }, color: { field: "v", type: "quantitative" } },
    };
    const { chart, built, model } = draw(byValue, rowsOf());
    expect(built.series.length).toBe(1);
    const data = model.getSeriesByIndex(0).getData() as Data & { getItemVisual(j: number, k: string): { fill: string } };
    // r's one row (8, the highest value) against the ramp's top colour
    const r = built.series[0].rows.indexOf(7);
    const palette = (built.option.visualMap as { inRange: { color: string[] } }[])[0].inRange.color;
    const top = palette.at(-1)!;
    const rgba = `rgba(${[1, 3, 5].map((k) => parseInt(top.slice(k, k + 2), 16)).join(",")},1)`;
    expect(data.getItemVisual(r, "style").fill).toBe(rgba);
    chart.dispose();
  });

  it("leaves a layer that is not stacked one series per colour: its rows at one slot are not pieces", () => {
    for (const mark of [{ type: "bar" }, { type: "scatter" }, { type: "area" }]) {
      const built = toOption(doc(mark, { field: "c", type: "nominal" }, { field: "v", type: "quantitative" }), rowsOf());
      expect(built.series.map((s) => s.rows)).toEqual([[0, 1, 2, 6], [3, 4, 5, 7]]);
    }
  });

  // P37 row 12 [supersedes P35's "maps a gesture on any piece to its own
  // row"]: a gesture on a sum maps to every row it stands for
  it("maps a gesture on a summed point to its rows: a brush, the legend, the tooltip", () => {
    const { chart, built } = draw(horizontal, rowsOf());
    // the point that draws rows 0 and 1 (a at p)
    const j = built.series[0].rows.findIndex((r) => Array.isArray(r) && r.includes(1));
    expect(built.series[0].rows[j]).toEqual([0, 1]);
    const brushed: BrushSelected = {
      batch: [{ areas: [{ brushType: "rect", coordRange: [[0, 99], [0, 9]] }], selected: [{ seriesIndex: 0, dataIndex: [j] }] }],
    };
    expect(selectionFromBrush(brushed, built)).toEqual([{ source: "brush", layer: 0, rows: [0, 1] }]);
    // hiding b leaves every row of a
    expect(selectionFromLegend({ a: true, b: false }, built)).toEqual([{ source: "legend", layer: 0, rows: [0, 1, 2, 6] }]);
    const tip = (built.option.tooltip as { formatter: (p: object) => string }).formatter;
    expect(tip({ seriesIndex: 0, dataIndex: j })).toContain("<b>7</b> (sum of 2 rows)");
    chart.dispose();
  });
});
