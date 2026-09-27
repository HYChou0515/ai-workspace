/**
 * #847/#848 PR 5 P40 row 18: a stack of raw rows is summed IN THE SANDBOX, per
 * slot and colour -- exactly an `aggregate: sum` on its value channel -- and
 * drawn here like any aggregated layer: one row per slot and colour, one
 * point per row. P37 summed a series' rows at a slot here, in the browser,
 * a second aggregation beside the sandbox's, and every channel (tooltip,
 * text, a colour by value, the marking) needed its own rule for it.
 *
 * Each case is `wire-corpus/stack-sums.json`: the query's real answer and
 * pandas' own stack tops (`scripts/write_stack_corpus.py`; the sandbox's
 * test_stack_sum.py holds both current). Drawn with REAL ECharts (SSR).
 */
import * as echarts from "echarts/core";
import { SVGRenderer } from "echarts/renderers";
import { describe, expect, it } from "vitest";

import { isLit } from "../../../../web/src/lib/markings";
import "./echarts"; // registers the chart's series + components
import { markingLit, selectionMarking } from "./marking";
import { measuredFields, toOption } from "./option";
import { type BrushSelected, selectionFromBrush, selectionFromLegend } from "./selection";
import { type StackCase, stackCase as named, stackCases as cases } from "./stackCorpus";

echarts.use([SVGRenderer]);

type Data = { count(): number; get(dim: string, i: number): number; getCalculationInfo(key: string): string };
type Model = { getSeriesByIndex(i: number): { getData(): Data } };

function draw(c: StackCase) {
  const built = toOption(c.spec, c.answer);
  const chart = echarts.init(null, null, { renderer: "svg", ssr: true, width: 600, height: 400 });
  chart.setOption(built.option, true);
  const model = (chart as unknown as { getModel(): Model }).getModel();
  const svg = chart.renderToSVGString();
  const tops = built.series.map((_, i) => {
    const data = model.getSeriesByIndex(i).getData();
    const dim = data.getCalculationInfo("stackResultDimension");
    return Array.from({ length: data.count() }, (_, j) => data.get(dim, j));
  });
  chart.dispose();
  return { built, svg, tops };
}

const tip = (built: ReturnType<typeof toOption>) => (built.option.tooltip as { formatter: (p: object) => string }).formatter;

describe("a stack summed in the sandbox, drawn as any aggregated layer (P40 row 18)", () => {
  it.each(cases.map((c) => [c.name, c] as const))("%s: each stack ends where pandas sums it", (_, c) => {
    const { built, svg, tops } = draw(c);
    if (c.tops) expect(tops).toEqual(c.tops);
    expect(svg).not.toMatch(/Infinity|NaN/);
    // one layer row per point: none stands for a list of rows
    expect(built.series.every((s) => s.rows.every((r) => r === null || typeof r === "number"))).toBe(true);
  });

  it("draws 1,000 rows in one item as one point per colour there", () => {
    const { built } = draw(named("a crowded item"));
    expect(built.series.map((s) => s.rows.filter((r) => r !== null).length)).toEqual([2, 2]);
  });

  it("its tooltip is the summed row's: the sum, and a field its rows do not share as empty", () => {
    const c = named("a bar with a tooltip its rows share only in part");
    const { built } = draw(c);
    // series a, slot q: rows 2 (7, s) and 6 (6, n)
    const q = tip(built)({ seriesIndex: 0, dataIndex: 1 });
    expect(q).toContain("value: <b>13</b>");
    expect(q).toContain("region: <b>—</b>");
    expect(q).not.toContain("sum of");
    // slot p: rows 0 (3, n) and 1 (4, n) share their region
    expect(tip(built)({ seriesIndex: 0, dataIndex: 0 })).toContain("region: <b>n</b>");
  });

  it("a brush over a summed bar writes its slot and colour, which light exactly its rows everywhere", () => {
    const c = named("a bar with a tooltip its rows share only in part");
    const { built } = draw(c);
    const measured = measuredFields(c.answer);
    // series a at p: rows 0 and 1, whose region (n) they share -- still no key
    const brushed: BrushSelected = {
      batch: [{ areas: [{ brushType: "rect", coordRange: [[0, 1], [0, 99]] }], selected: [{ seriesIndex: 0, dataIndex: [0] }] }],
    };
    const sel = selectionFromBrush(brushed, built);
    const wrote = selectionMarking(sel, c.answer, c.spec.keys, measured)!;
    expect(Object.fromEntries(Object.entries(wrote).map(([k, v]) => [k, [...v]]))).toEqual({ item: ["p"], group: ["a"] });
    // the chart lights the one summed bar
    const lit = markingLit(c.answer, wrote, isLit, measured)[0]!;
    expect(lit.filter(Boolean)).toHaveLength(1);
    // a table of the raw rows lights exactly those the bar summed: 0 and 1
    const rows = c.data.item.map((_, r) => ({ item: String(c.data.item[r]), group: String(c.data.group[r]), region: String(c.data.region[r]) }));
    expect(rows.flatMap((row, r) => (isLit(row, wrote) ? [r] : []))).toEqual([0, 1]);
  });

  it("the legend hides a colour's summed rows, and so writes the other colour", () => {
    const c = named("an upright bar of rows");
    const { built } = draw(c);
    const sel = selectionFromLegend({ a: true, b: false }, built);
    const wrote = selectionMarking(sel, c.answer, c.spec.keys, measuredFields(c.answer))!;
    expect([...wrote.group]).toEqual(["a"]);
  });
});
