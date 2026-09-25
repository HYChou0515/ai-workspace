/**
 * Review round 3 findings on the renderer:
 * - on a grid chart every other layer's field positions were null, so points
 *   laid over the lattice (marking cells) never drew;
 * - a rule between two cells (`datum: 11.5`) had no position either.
 */
import { describe, expect, it } from "vitest";

import { toOption } from "./option";
import { answer, base, f64, layer, q8 } from "./testAnswer";

const grid = {
  mark: "grid",
  encoding: {
    x: { field: "x", type: "ordinal" },
    y: { field: "y", type: "ordinal" },
    color: { field: "v", type: "quantitative" },
  },
};
// A 3 × 2 lattice: x 10, 11, 12; y 0, 1.
const cells = layer("grid", 6, {
  x: f64([10, 11, 12, 10, 11, 12]),
  y: f64([0, 0, 0, 1, 1, 1]),
  v: q8([0, 1, 2, 3, 4, 5], 0, 1),
});

type Series = { data?: unknown[]; markLine?: { data: { xAxis: number }[] } };

describe("a layer over a grid", () => {
  it("puts each point on the cell its values name", () => {
    const spec = {
      ...base,
      layer: [grid, { mark: "scatter", encoding: { x: { field: "x", type: "ordinal" }, y: { field: "y", type: "ordinal" } } }],
    };
    const a = answer(cells, layer("scatter", 2, { x: f64([12, 10]), y: f64([1, 0]) }));
    const series = toOption(spec, a).option.series as Series[];
    expect(series[1].data).toEqual([
      [2, 1],
      [0, 0],
    ]);
  });

  it("puts a value between two cells between their centres", () => {
    const spec = { ...base, layer: [grid, { mark: "rule", encoding: { x: { datum: 11.5 } } }] };
    const series = toOption(spec, answer(cells, layer("rule", 0, {}))).option.series as Series[];
    expect(series[1].markLine?.data[0].xAxis).toBe(1.5);
  });

  it("places nothing for a value off the lattice", () => {
    const spec = {
      ...base,
      layer: [grid, { mark: "scatter", encoding: { x: { field: "x", type: "ordinal" }, y: { field: "y", type: "ordinal" } } }],
    };
    const a = answer(cells, layer("scatter", 1, { x: f64([13]), y: f64([0]) }));
    const series = toOption(spec, a).option.series as Series[];
    expect(series[1].data).toEqual([[null, 0]]);
  });
});

describe("a line's opacity", () => {
  it("fades the line, not only its points", () => {
    // SKILL.md lists opacity for line; it set only itemStyle, the symbols —
    // hidden by default — so the line drew at full strength.
    const spec = {
      ...base,
      mark: { type: "line", opacity: 0.3 },
      encoding: { x: { field: "t", type: "quantitative" }, y: { field: "v", type: "quantitative" } },
    };
    const a = answer(layer("line", 2, { t: f64([1, 2]), v: f64([1, 2]) }));
    const [s] = toOption(spec, a).option.series as { lineStyle?: { opacity: number } }[];
    expect(s.lineStyle?.opacity).toBe(0.3);
  });
});

describe("a rule drawn from a field", () => {
  it("leaves out the rows with no position on its axis", () => {
    // Review round 6: a missing value went to ECharts as {yAxis: null}, and a
    // 0 on a log axis as {yAxis: 0}, which the axis cannot hold.
    const spec = {
      ...base,
      layer: [
        { mark: "scatter", encoding: { x: { field: "a", type: "quantitative" }, y: { field: "b", type: "quantitative", scale: { type: "log" } } } },
        { mark: "rule", encoding: { y: { field: "b", type: "quantitative" } } },
      ],
    };
    const a = answer(layer("scatter", 1, { a: f64([1]), b: f64([1]) }), layer("rule", 3, { b: f64([1, null, 0]) }));
    const built = toOption(spec, a);
    const series = built.option.series as Series[];
    expect(series[1].markLine?.data).toEqual([{ yAxis: 1 }]);
    // Round 6 regression lens: left out in silence, nobody knew the rule was short.
    expect(built.notes).toContain("2 rule values off the y axis — not drawn");
  });
});
