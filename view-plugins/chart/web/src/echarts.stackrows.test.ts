/**
 * #847/#848 PR 5 P35 row 7: every row is one piece of its stack, on any axis.
 * Rows of one series at one slot (a category, an x) stack in order, so a
 * stack of raw rows (no aggregate) reaches the per-slot SUM. Before, ECharts
 * stacked one series on another but drew a series' own rows at one category
 * over each other from 0: a horizontal bar of rows ended at 31.3 where the sum
 * was 79.38 (P34's demo).
 *
 * Against REAL ECharts (SSR). The oracle is pandas: the per-category sums
 * below are `pd.DataFrame({"c": list("ppqpqqqr"), "g": list("aaabbbab"),
 * "v": [3, 4, 7, 5, 1, 2, 6, 8]}).groupby("c").v.sum()` -> p 12, q 16, r 8.
 */
import * as echarts from "echarts/core";
import { SVGRenderer } from "echarts/renderers";
import { describe, expect, it } from "vitest";

import "./echarts"; // registers the chart's series + components
import { type Answer, toOption } from "./option";
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

/** Per category, the highest stacked top any of its rows reaches: how far the
 * stack is drawn there. Every value is positive, so that is the stack's end. */
function extents(model: Model, built: ReturnType<typeof toOption>, category: (row: number) => string): Map<string, number> {
  const out = new Map<string, number>();
  built.series.forEach((s, i) => {
    const data = model.getSeriesByIndex(i).getData();
    const top = data.getCalculationInfo("stackResultDimension");
    for (let j = 0; j < data.count(); j++) {
      const row = s.rows[j];
      if (row === null) continue;
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

describe("rows stacked on a category axis reach their category's sum (P35 row 7)", () => {
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

  it("stacks the rows in order: a series' rows sit on each other, the next series on all of them", () => {
    const { chart, built, model } = draw(horizontal, rowsOf());
    // row -> its stacked top: at p, a's rows 0 (3) and 1 (4), then b's row 3 (5)
    const top = new Map<number, number>();
    built.series.forEach((s, i) => {
      const data = model.getSeriesByIndex(i).getData();
      const dim = data.getCalculationInfo("stackResultDimension");
      s.rows.forEach((r, j) => r !== null && top.set(r, data.get(dim, j)));
    });
    expect([0, 1, 3].map((r) => top.get(r))).toEqual([3, 7, 12]);
    // at q: a's rows 2 (7) and 6 (6), then b's 4 (1) and 5 (2)
    expect([2, 6, 4, 5].map((r) => top.get(r))).toEqual([7, 13, 14, 16]);
    chart.dispose();
  });

  it("draws every piece of a series in that series' colour, and names it for the legend", () => {
    const { chart, built, model } = draw(horizontal, rowsOf());
    const fill = (i: number) => model.getSeriesByIndex(i).getData().getVisual("style").fill;
    const byName = new Map<string, Set<string>>();
    built.names.forEach((n, i) => byName.set(n as string, (byName.get(n as string) ?? new Set()).add(fill(i))));
    expect([...byName.keys()].sort()).toEqual(["a", "b"]);
    expect([...byName.values()].map((s) => s.size)).toEqual([1, 1]);
    expect(byName.get("a")).not.toEqual(byName.get("b"));
    chart.dispose();
  });

  it("draws the pieces of a series with no colour channel in one colour", () => {
    const one = { ...base, mark: { type: "bar", stack: true }, encoding: { x: { field: "v", type: "quantitative" }, y: { field: "c", type: "nominal" } } };
    const { chart, built, model } = draw(one, rowsOf());
    expect(built.series.length).toBe(4); // q has four rows: four pieces
    const fills = new Set(built.series.map((_, i) => model.getSeriesByIndex(i).getData().getVisual("style").fill));
    expect(fills.size).toBe(1);
    expect(built.names.every((n) => n === undefined)).toBe(true);
    expect(extents(model, built, (r) => C[r])).toEqual(SUMS);
    chart.dispose();
  });

  it("paints every piece by a colour by value, not the first piece alone", () => {
    const byValue = {
      ...base,
      mark: { type: "bar", stack: true },
      encoding: { x: { field: "v", type: "quantitative" }, y: { field: "c", type: "nominal" }, color: { field: "v", type: "quantitative" } },
    };
    const { chart, built, model } = draw(byValue, rowsOf());
    expect(built.series.length).toBe(4); // q has four rows: four pieces
    // every item's colour comes from the ramp: one value, one colour, whichever piece draws it
    const colourOf = new Map<number, Set<string>>();
    built.series.forEach((s, i) => {
      const data = model.getSeriesByIndex(i).getData() as Data & { getItemVisual(j: number, k: string): { fill: string } };
      s.rows.forEach((r, j) => r !== null && colourOf.set(V[r], (colourOf.get(V[r]) ?? new Set()).add(data.getItemVisual(j, "style").fill)));
    });
    const lowest = [...colourOf.get(1)!][0];
    const highest = [...colourOf.get(8)!][0];
    expect(lowest).not.toBe(highest);
    // 4 (row 1) is drawn by a's second piece at p: coloured between the ends, not the palette's first colour
    const piece = built.series.findIndex((s) => s.rows.includes(1));
    expect(piece).toBeGreaterThan(0);
    const fills = new Set(built.series.map((_, i) => model.getSeriesByIndex(i).getData().getVisual("style").fill));
    const mid = [...colourOf.get(4)!][0];
    expect([lowest, highest]).not.toContain(mid);
    expect(fills).not.toContain(mid);
    chart.dispose();
  });

  it("leaves a layer that is not stacked one series per colour: its rows at one slot are not pieces", () => {
    for (const mark of [{ type: "bar" }, { type: "scatter" }, { type: "area" }]) {
      const built = toOption(doc(mark, { field: "c", type: "nominal" }, { field: "v", type: "quantitative" }), rowsOf());
      expect(built.series.map((s) => s.rows)).toEqual([[0, 1, 2, 6], [3, 4, 5, 7]]);
    }
  });

  it("maps a gesture on any piece to its own row: a brush, the legend, the tooltip", () => {
    const { chart, built } = draw(horizontal, rowsOf());
    // the piece that draws row 1 (a's second row at p)
    const i = built.series.findIndex((s) => s.rows.includes(1));
    const j = built.series[i].rows.indexOf(1);
    expect(i).toBeGreaterThan(0); // not a's first piece
    const brushed: BrushSelected = {
      batch: [{ areas: [{ brushType: "rect", coordRange: [[0, 99], [0, 9]] }], selected: [{ seriesIndex: i, dataIndex: [j] }] }],
    };
    expect(selectionFromBrush(brushed, built)).toEqual([{ source: "brush", layer: 0, rows: [1] }]);
    // hiding b leaves every row of a, from all of a's pieces
    expect(selectionFromLegend({ a: true, b: false }, built)).toEqual([{ source: "legend", layer: 0, rows: [0, 1, 2, 6] }]);
    const tip = (built.option.tooltip as { formatter: (p: object) => string }).formatter;
    expect(tip({ seriesIndex: i, dataIndex: j })).toContain("<b>4</b>");
    chart.dispose();
  });
});
