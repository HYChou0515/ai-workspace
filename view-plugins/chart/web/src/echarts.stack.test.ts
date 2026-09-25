/**
 * `stack: true` against REAL ECharts (SSR), on every kind of x (#847/#848 PR 5
 * P29). ECharts matches a stack's points by x VALUE only on a category axis;
 * on a value or time axis it stacks by data INDEX, and on a value x it stacks
 * the first number dimension it finds -- x itself. Two groups of 1s on a
 * quantitative x drew a y range of 0-1, nothing stacked. The oracle here is
 * ECharts' own stacking on a category x, which does match by value.
 */
import * as echarts from "echarts/core";
import { SVGRenderer } from "echarts/renderers";
import { describe, expect, it } from "vitest";

import "./echarts"; // registers the chart's series + components
import { DIM_OPACITY } from "./highlight";
import { type Answer, toOption } from "./option";
import { type BrushSelected, selectionFromBrush, selectionFromLegend } from "./selection";
import { answer, base, cat, f64, layer, time } from "./testAnswer";
import type { WireColumn } from "./wire";

echarts.use([SVGRenderer]);

type Model = {
  getComponent(main: string, i: number): { axis: { scale: { getExtent(): [number, number] } } };
  getSeriesByIndex(i: number): {
    getData(): {
      count(): number;
      get(dim: string, i: number): number;
      getCalculationInfo(key: string): string;
      mapDimension(coordDim: string): string;
    };
  };
  getSeriesCount(): number;
};

function draw(doc: object, a: Answer) {
  const built = toOption(doc, a);
  const chart = echarts.init(null, null, { renderer: "svg", ssr: true, width: 600, height: 400 });
  chart.setOption(built.option, true);
  const model = (chart as unknown as { getModel(): Model }).getModel();
  return { chart, built, model };
}

/** Per series, each point's x as the axis holds it and its stacked top, over
 * the points that draw a layer row: "x → top". */
function tops(model: Model, built: ReturnType<typeof toOption>): Map<string, number>[] {
  return built.series.map((s, i) => {
    const data = model.getSeriesByIndex(i).getData();
    const top = data.getCalculationInfo("stackResultDimension");
    const out = new Map<string, number>();
    for (let j = 0; j < data.count(); j++) {
      if (s.rows[j] === null) continue;
      out.set(String(s.rows[j]), data.get(top, j));
    }
    return out;
  });
}

const area = (x: object, mark: object = { type: "area", stack: true }) => ({
  ...base,
  mark,
  encoding: { x, y: { field: "y", type: "quantitative" }, color: { field: "g", type: "nominal" } },
});

describe("stack: true on a quantitative, temporal and nominal x", () => {
  it("stacks two groups of 1s on a quantitative x to 2 (red before: y ran 0-1)", () => {
    const { chart, built, model } = draw(
      area({ field: "x", type: "quantitative" }),
      answer(layer("area", 4, { x: f64([1, 2, 1, 2]), y: f64([1, 1, 1, 1]), g: cat(["a", "a", "b", "b"]) })),
    );
    expect(model.getComponent("yAxis", 0).axis.scale.getExtent()).toEqual([0, 2]);
    // the x axis is the data's own 1..2, not x stacked on itself (2..4)
    expect(model.getComponent("xAxis", 0).axis.scale.getExtent()[1]).toBeLessThan(3);
    expect([...tops(model, built)[1].values()]).toEqual([2, 2]);
    chart.dispose();
  });

  it("stacks bars on a quantitative x the same way", () => {
    const { chart, model } = draw(
      area({ field: "x", type: "quantitative" }, { type: "bar", stack: true }),
      answer(layer("bar", 4, { x: f64([1, 2, 1, 2]), y: f64([1, 1, 1, 1]), g: cat(["a", "a", "b", "b"]) })),
    );
    expect(model.getComponent("yAxis", 0).axis.scale.getExtent()).toEqual([0, 2]);
    chart.dispose();
  });

  // Groups with different x sets, rows in no order: a at 2, 3, 1; b at 3 and
  // 2 only. Matched by index, b's first point (x=3) sat on a's x=2.
  const ys = [2, 4, 1, 10, 20];
  const gs = ["a", "a", "a", "b", "b"];
  const xs = [2, 3, 1, 3, 2];
  const stacked = (x: WireColumn, type: string) => {
    const { chart, built, model } = draw(
      area({ field: "x", type }),
      answer(layer("area", 5, { x, y: f64(ys), g: cat(gs) })),
    );
    const out = tops(model, built);
    chart.dispose();
    return out;
  };
  // row → stacked top, by ECharts' own matching on a category x
  const oracle = stacked(cat(xs), "nominal");

  // P36 row 11 [supersedes P29's "leaves a category x's stack to ECharts: its
  // series keep their rows"]: ECharts still matches by category, but a series
  // with no row at a category gets its 0 there, as off a category axis, and
  // its points run in the axis's order -- or an area ran straight across the
  // category it has no row at.
  it("lines a category x's stack up too: a filler where a series has no row, points in the axis's order", () => {
    expect(oracle[1]).toEqual(new Map([["3", 14], ["4", 22]]));
    const { chart, built } = draw(area({ field: "x", type: "nominal" }), answer(layer("area", 5, { x: cat(xs), y: f64(ys), g: cat(gs) })));
    // x 1, 2, 3: a's rows 2, 0, 1; b has no row at x=1
    expect(built.series.map((s) => s.rows)).toEqual([[2, 0, 1], [null, 4, 3]]);
    chart.dispose();
  });

  it("matches the category x's stacking, by value, on a quantitative x", () => {
    expect(stacked(f64(xs), "quantitative")).toEqual(oracle);
  });

  it("matches it on a temporal x too (red before: stacked by index)", () => {
    const day = (d: number) => `2026-03-0${d}T00:00:00Z`;
    expect(stacked(time(xs.map(day)), "temporal")).toEqual(oracle);
  });

  it("gives the points it adds to line the groups up no row, so no gesture or tooltip reads one", () => {
    const doc = area({ field: "x", type: "quantitative" });
    const a = answer(layer("area", 5, { x: f64(xs), y: f64(ys), g: cat(gs) }));
    const { chart, built } = draw(doc, a);
    const b = built.series[1];
    // b has no row at x=1: that slot is a filler
    expect(b.rows).toEqual([null, 4, 3]);
    // drawn clear, never highlighted by a hover, no tooltip of its own; y first
    expect((built.option.series as { data: unknown[] }[])[1].data[0]).toEqual({
      value: [0, 1],
      itemStyle: { opacity: 0 },
      emphasis: { disabled: true },
      tooltip: { show: false },
    });
    const brushed: BrushSelected = {
      batch: [{ areas: [{ brushType: "rect", coordRange: [[0, 9], [0, 99]] }], selected: [{ seriesIndex: 1, dataIndex: [0, 1, 2] }] }],
    };
    expect(selectionFromBrush(brushed, built)).toEqual([{ source: "brush", layer: 0, rows: [3, 4] }]);
    expect(selectionFromLegend({ a: false, b: true }, built)).toEqual([{ source: "legend", layer: 0, rows: [3, 4] }]);
    const tip = (built.option.tooltip as { formatter: (p: object) => string }).formatter;
    expect(tip({ seriesIndex: 1, dataIndex: 0 })).toBe("");
    expect(tip({ seriesIndex: 1, dataIndex: 1 })).toContain("<b>2</b>");
    chart.dispose();
  });

  it("keeps an unlit row dimmed where its point moves", () => {
    const doc = area({ field: "x", type: "quantitative" });
    const a = answer(layer("area", 4, { x: f64([2, 1, 1, 2]), y: f64([1, 1, 1, 1]), g: cat(["a", "a", "b", "b"]) }));
    const built = toOption(doc, a, { lit: [[false, true, true, true]] });
    // a's slots are x=1 (row 1, lit), x=2 (row 0, unlit): y first
    expect((built.option.series as { data: unknown[] }[])[0].data).toEqual([
      [1, 1],
      { value: [1, 2], itemStyle: { opacity: DIM_OPACITY } },
    ]);
  });

  // P35 row 7, as P37 row 12 draws it: two rows of one group at one x add
  // up. Before P35 a's second row at x=1 was a second slot of a, drawn from 0
  // over its first, and b sat on a's first alone (11); P35 made it a piece of
  // its own, stacked on a's first; P37 draws a's rows at x=1 as ONE point,
  // their sum, which stands for both.
  it("stacks two rows of one group at one x as their sum, and puts a row with no x last", () => {
    const { chart, built, model } = draw(
      area({ field: "x", type: "quantitative" }),
      answer(layer("area", 5, { x: f64([1, null, 1, 1, 2]), y: f64([1, 5, 2, 10, 3]), g: cat(["a", "a", "a", "b", "a"]) })),
    );
    // slots: x=1, x=2, then the row with no x; a's rows at x=1 are one point
    expect(built.series.map((s) => s.rows)).toEqual([
      [[0, 2], 4, 1],
      [3, null, null],
    ]);
    // a at x=1 ends at 1 + 2, b on it at 1 + 2 + 10
    const t = tops(model, built);
    expect([t[0].get("0,2"), t[1].get("3")]).toEqual([3, 13]);
    chart.dispose();
  });
});

describe("stacks P29 must not break (round 16 defect lens)", () => {
  it("stacks a horizontal bar by its category y, as before P29 (D1: red — filled by length)", () => {
    const doc = {
      ...base,
      mark: { type: "bar", stack: true },
      encoding: {
        x: { field: "v", type: "quantitative" },
        y: { field: "c", type: "nominal" },
        color: { field: "g", type: "nominal" },
      },
    };
    const { chart, built, model } = draw(
      doc,
      answer(layer("bar", 4, { v: f64([1, 5, 2, 7]), c: cat(["p", "q", "p", "q"]), g: cat(["a", "a", "b", "b"]) })),
    );
    // b's p bar ends at 1 + 2, its q bar at 5 + 7; no point is added to line it up
    expect(tops(model, built)[1]).toEqual(new Map([["2", 3], ["3", 12]]));
    expect(built.series.map((s) => s.rows)).toEqual([[0, 1], [2, 3]]);
    chart.dispose();
  });

  it("labels each stacked point with its own row's text, and a filler with none (D2: red — by old index)", () => {
    const doc = {
      ...base,
      mark: { type: "bar", stack: true },
      encoding: {
        x: { field: "x", type: "quantitative" },
        y: { field: "y", type: "quantitative" },
        color: { field: "g", type: "nominal" },
        text: { field: "t", type: "nominal" },
      },
    };
    const built = toOption(
      doc,
      answer(layer("bar", 3, { x: f64([3, 1, 2]), y: f64([1, 1, 1]), g: cat(["a", "a", "b"]), t: cat(["A3", "A1", "B2"]) })),
    );
    const series = built.option.series as { label: { formatter: (p: object) => string } }[];
    const labels = series.map((s, i) =>
      built.series[i].rows.map((_, j) => s.label.formatter({ seriesIndex: i, dataIndex: j })),
    );
    // slots x = 1, 2, 3: a has rows at 1 and 3, b at 2
    expect(labels).toEqual([
      ["A1", "", "A3"],
      ["", "B2", ""],
    ]);
  });

  it("keeps a log y's range on the data when groups sit at different x (D3: red — a filler 0 on a log axis)", () => {
    const doc = {
      ...base,
      mark: { type: "area", stack: true },
      encoding: {
        x: { field: "x", type: "quantitative" },
        y: { field: "y", type: "quantitative", scale: { type: "log" } },
        color: { field: "g", type: "nominal" },
      },
    };
    const { chart, built, model } = draw(
      doc,
      answer(layer("area", 4, { x: f64([1, 2, 3, 4]), y: f64([10, 100, 10, 100]), g: cat(["a", "a", "b", "b"]) })),
    );
    // b sits where a has no row: its tops are its own values
    expect(tops(model, built)[1]).toEqual(new Map([["2", 10], ["3", 100]]));
    const [lo, hi] = model.getComponent("yAxis", 0).axis.scale.getExtent();
    expect(Number.isFinite(lo) && Number.isFinite(hi)).toBe(true);
    expect(hi).toBeGreaterThanOrEqual(2); // log10(100): the axis reaches the data
    expect(chart.renderToSVGString()).not.toContain("Infinity");
    chart.dispose();
  });

  // #847/#848 PR 5 P34 row 4: the filler follows the axis the chart BUILT, not
  // the spec's shape -- a `layer:` spec has no top-level encoding, and read
  // from there its log y was filled with 0.
  it("keeps a log y's range on the data when the stack is one layer of a `layer:` spec (P34)", () => {
    const doc = {
      ...base,
      layer: [
        {
          mark: { type: "area", stack: true },
          encoding: {
            x: { field: "x", type: "quantitative" },
            y: { field: "y", type: "quantitative", scale: { type: "log" } },
            color: { field: "g", type: "nominal" },
          },
        },
      ],
    };
    const { chart, built, model } = draw(
      doc,
      answer(layer("area", 4, { x: f64([1, 2, 3, 4]), y: f64([10, 100, 10, 100]), g: cat(["a", "a", "b", "b"]) })),
    );
    expect(tops(model, built)[1]).toEqual(new Map([["2", 10], ["3", 100]]));
    const [lo, hi] = model.getComponent("yAxis", 0).axis.scale.getExtent();
    expect(Number.isFinite(lo) && Number.isFinite(hi)).toBe(true);
    expect(hi).toBeGreaterThanOrEqual(2);
    expect(chart.renderToSVGString()).not.toContain("Infinity");
    chart.dispose();
  });
});
