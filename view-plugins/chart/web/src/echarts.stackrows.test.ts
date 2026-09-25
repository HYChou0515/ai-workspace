/**
 * #847/#848 PR 5 P35 row 7: a stack of raw rows (no aggregate) reaches the
 * per-slot SUM, on any axis. Before, ECharts stacked one series on another
 * but drew a series' own rows at one category over each other from 0: a
 * horizontal bar of rows ended at 31.3 where the sum was 79.38 (P34's demo).
 * P37 row 12 summed a series' rows at a slot here, in the browser; P40 row 18
 * sums them in the sandbox, per slot and colour, and this draws the sums as
 * any aggregated layer's rows: one point per row.
 *
 * Against REAL ECharts (SSR). The answers are the sandbox's own, for the rows
 * `pd.DataFrame({"item": list("ppqpqqqr"), "group": list("aaabbbab"),
 * "value": [3, 4, 7, 5, 1, 2, 6, 8]})` (`stackCorpus.ts`); the oracle is
 * pandas: `df.groupby("item").value.sum()` -> p 12, q 16, r 8.
 */
import * as echarts from "echarts/core";
import { SVGRenderer } from "echarts/renderers";
import { describe, expect, it } from "vitest";

import "./echarts"; // registers the chart's series + components
import { type Answer, toOption } from "./option";
import { type BrushSelected, selectionFromBrush, selectionFromLegend } from "./selection";
import { stackCase } from "./stackCorpus";
import { answer, base, cat, f64, layer } from "./testAnswer";
import { decodeColumn } from "./wire";

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

const SUMS = new Map([["p", 12], ["q", 16], ["r", 8]]); // pandas, above
const upright = stackCase("an upright bar of rows");
const horizontal = stackCase("a horizontal bar of rows");
const drawCase = (c: { spec: object; answer: Answer }) => draw(c.spec, c.answer);
/** A summed row's column value, as the answer holds it. */
const valueOf = (a: Answer, column: string) => {
  const col = decodeColumn(a.layers[0].columns[column]);
  return (row: number) => String(col.value(row));
};

/** Per category, the highest stacked top any of its points reaches: how far
 * the stack is drawn there. Every value is positive, so that is the stack's
 * end. */
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

describe("rows stacked on a category axis reach their category's sum (P35 row 7, P40 row 18)", () => {
  it.each([
    ["a horizontal bar of rows", horizontal],
    ["an upright bar of rows", upright],
    ["an area on a category x", stackCase("an area of rows on a category x")],
    ["a stack with no colour channel", stackCase("a bar of rows with no colour")],
  ])("%s ends at each category's sum", (_, c) => {
    const { chart, built, model } = drawCase(c);
    expect(extents(model, built, valueOf(c.answer, "item"))).toEqual(SUMS);
    chart.dispose();
  });

  it("stretches the value axis to the largest sum", () => {
    for (const [c, axis] of [[horizontal, "xAxis"], [upright, "yAxis"]] as const) {
      const { chart, model } = drawCase(c);
      expect(model.getComponent(axis, 0).axis.scale.getExtent()[1]).toBeGreaterThanOrEqual(16);
      chart.dispose();
    }
  });

  // P40 row 18 [supersedes P37's "draws a series' rows at a category as their
  // sum": the sum is the sandbox's now, one row per slot and colour]
  it("draws each summed row as one point, and the next series on it", () => {
    const { chart, built, model } = drawCase(horizontal);
    const [item, group] = [valueOf(horizontal.answer, "item"), valueOf(horizontal.answer, "group")];
    const top = new Map<string, number>();
    built.series.forEach((s, i) => {
      const data = model.getSeriesByIndex(i).getData();
      const dim = data.getCalculationInfo("stackResultDimension");
      s.rows.forEach((r, j) => r !== null && top.set(`${item(r)}·${group(r)}`, data.get(dim, j)));
    });
    // at p: a's 3 + 4, then b's 5; at q: a's 7 + 6, then b's 1 + 2
    expect(Object.fromEntries(top)).toEqual({ "p·a": 7, "q·a": 13, "p·b": 12, "q·b": 16, "r·b": 8 });
    chart.dispose();
  });

  it("draws each series in its own colour, and names it for the legend", () => {
    const { chart, built, model } = drawCase(horizontal);
    const fill = (i: number) => model.getSeriesByIndex(i).getData().getVisual("style").fill;
    const byName = new Map<string, Set<string>>();
    built.names.forEach((n, i) => byName.set(n as string, (byName.get(n as string) ?? new Set()).add(fill(i))));
    expect([...byName.keys()].sort()).toEqual(["a", "b"]);
    expect([...byName.values()].map((s) => s.size)).toEqual([1, 1]);
    expect(byName.get("a")).not.toEqual(byName.get("b"));
    chart.dispose();
  });

  it("draws a stack with no colour channel as one series, nameless in the legend", () => {
    const { chart, built } = drawCase(stackCase("a bar of rows with no colour"));
    expect(built.series.length).toBe(1);
    expect(built.names.every((n) => n === undefined)).toBe(true);
    chart.dispose();
  });

  it("leaves a layer that is not stacked one series per colour: its rows at one slot are not summed", () => {
    const C = ["p", "p", "q", "p", "q", "q", "q", "r"];
    const G = ["a", "a", "a", "b", "b", "b", "a", "b"];
    const raw = answer(layer("bar", 8, { c: cat(C), g: cat(G), v: f64([3, 4, 7, 5, 1, 2, 6, 8]) }));
    for (const mark of [{ type: "bar" }, { type: "scatter" }, { type: "area" }]) {
      const doc = { ...base, mark, encoding: { x: { field: "c", type: "nominal" }, y: { field: "v", type: "quantitative" }, color: { field: "g", type: "nominal" } } };
      const built = toOption(doc, raw);
      expect(built.series.map((s) => s.rows)).toEqual([[0, 1, 2, 6], [3, 4, 5, 7]]);
    }
  });

  // P40 row 18 [supersedes P37's "maps a gesture on a summed point to its
  // rows": a summed point IS one row of the answer, and what it stands for
  // is written as its slot and colour (echarts.stacksandbox.test.ts)]
  it("maps a gesture on a summed point to its row: a brush, the legend, the tooltip", () => {
    const { chart, built } = drawCase(horizontal);
    const item = valueOf(horizontal.answer, "item");
    // a at p: one row of the answer, the sum 3 + 4
    const j = built.series[0].rows.findIndex((r) => r !== null && item(r) === "p");
    const row = built.series[0].rows[j] as number;
    const brushed: BrushSelected = {
      batch: [{ areas: [{ brushType: "rect", coordRange: [[0, 99], [0, 9]] }], selected: [{ seriesIndex: 0, dataIndex: [j] }] }],
    };
    expect(selectionFromBrush(brushed, built)).toEqual([{ source: "brush", layer: 0, rows: [row] }]);
    // hiding b leaves a's summed rows: p and q
    const [legend] = selectionFromLegend({ a: true, b: false }, built);
    expect(legend.rows.map(item).sort()).toEqual(["p", "q"]);
    const tip = (built.option.tooltip as { formatter: (p: object) => string }).formatter;
    expect(tip({ seriesIndex: 0, dataIndex: j })).toContain("value: <b>7</b>");
    chart.dispose();
  });
});
