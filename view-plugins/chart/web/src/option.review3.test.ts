/**
 * Review round 3 findings on the renderer:
 * - on a grid chart every other layer's field positions were null, so points
 *   laid over the lattice (marking cells) never drew;
 * - a rule between two cells (`datum: 11.5`) had no position either.
 */
import { describe, expect, it } from "vitest";

import { toOption } from "./option";
import { answer, base, cat, f64, layer, q8 } from "./testAnswer";

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

describe("a layer's points on a number axis", () => {
  it("are sent as the layer holds them, for ECharts to read", () => {
    // Review round 7: P24 sent every mark's points through the rule's `pos`,
    // so a layer typing the same field as text (it arrives as `cat`) lost
    // all its points, with no note. Only a rule's own rows are held to `pos`.
    const spec = {
      ...base,
      layer: [
        { mark: "scatter", encoding: { x: { field: "a", type: "quantitative" }, y: { field: "v", type: "quantitative" } } },
        { mark: "scatter", encoding: { x: { field: "a", type: "quantitative" }, y: { field: "v", type: "ordinal" } } },
      ],
    };
    const a = answer(
      layer("scatter", 2, { a: f64([1, 2]), v: f64([1.5, 2.5]) }),
      layer("scatter", 2, { a: f64([1, 2]), v: cat(["1.5", "2.5"]) }),
    );
    const series = toOption(spec, a).option.series as { data: unknown[] }[];
    expect(series[1].data).toEqual([
      [1, "1.5"],
      [2, "2.5"],
    ]);
  });
});

describe("a rule drawn from a field", () => {
  it("leaves out the rows with no position on its axis", () => {
    // Review round 6: a 0 on a log axis went to ECharts as {yAxis: 0}, which
    // the axis cannot hold (a missing value was already left out).
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
    expect(built.notes).toContain("2 rule values with no place on the y axis — not drawn");
  });

  it("places a number sent as text, as ECharts reads it", () => {
    // Review round 8: a rule whose field another layer types as text (it
    // arrives as `cat`) refused "1.5", which ECharts draws at 1.5.
    const spec = {
      ...base,
      layer: [
        { mark: "scatter", encoding: { x: { field: "a", type: "quantitative" }, y: { field: "b", type: "quantitative" } } },
        { mark: "rule", encoding: { y: { field: "b", type: "nominal" } } },
      ],
    };
    const a = answer(layer("scatter", 1, { a: f64([1]), b: f64([1]) }), layer("rule", 2, { b: cat(["1.5", "2"]) }));
    const built = toOption(spec, a);
    const series = built.option.series as Series[];
    expect(series[1].markLine?.data).toEqual([{ yAxis: 1.5 }, { yAxis: 2 }]);
    expect(built.notes).toEqual([]);
  });

  it("places an x field's values the same way", () => {
    // Round 8 conformance: the x twin of the y path had no test of its own.
    const spec = {
      ...base,
      layer: [
        {
          mark: "scatter",
          encoding: { x: { field: "a", type: "quantitative", scale: { type: "log" } }, y: { field: "b", type: "quantitative" } },
        },
        { mark: "rule", encoding: { x: { field: "a", type: "quantitative" } } },
      ],
    };
    const a = answer(layer("scatter", 1, { a: f64([1]), b: f64([1]) }), layer("rule", 3, { a: f64([2, 0, null]) }));
    const built = toOption(spec, a);
    const series = built.option.series as Series[];
    expect(series[1].markLine?.data).toEqual([{ xAxis: 2 }]);
    expect(built.notes).toContain("2 rule values with no place on the x axis — not drawn");
  });

  it("holds a segment's ends to the same placing as a rule's values", () => {
    // Review round 8: ends went through the raw `at`, so a 0 on a log axis
    // or text on a number axis was sent, and the note did not count it.
    const spec = {
      ...base,
      layer: [
        {
          mark: "scatter",
          encoding: { x: { field: "a", type: "quantitative", scale: { type: "log" } }, y: { field: "b", type: "quantitative" } },
        },
        {
          mark: "rule",
          encoding: {
            x: { field: "a", type: "quantitative" },
            y: { field: "b", type: "quantitative" },
            x2: { field: "c", type: "quantitative" },
          },
        },
      ],
    };
    const a = answer(
      layer("scatter", 1, { a: f64([1]), b: f64([1]) }),
      layer("rule", 3, { a: f64([1, 1, 1]), b: f64([1, 1, 1]), c: f64([2, 0, -1]) }),
    );
    const built = toOption(spec, a);
    const series = built.option.series as { markLine?: { data: unknown[] } }[];
    expect(series[1].markLine?.data).toEqual([[{ coord: [1, 1] }, { coord: [2, 1] }]]);
    expect(built.notes).toContain("2 rule segments with no place on the axes — not drawn");
  });

  it("leaves out a segment with an end it cannot place, and says so", () => {
    // Review round 7: such a segment went as {coord: [null, y]} with no note,
    // and an x2 with no place fell back to x — a segment of no length.
    const spec = {
      ...base,
      layer: [
        { mark: "scatter", encoding: { x: { field: "a", type: "quantitative" }, y: { field: "b", type: "quantitative" } } },
        {
          mark: "rule",
          encoding: {
            x: { field: "a", type: "quantitative" },
            y: { field: "b", type: "quantitative" },
            x2: { field: "c", type: "quantitative" },
          },
        },
      ],
    };
    const a = answer(
      layer("scatter", 1, { a: f64([1]), b: f64([1]) }),
      layer("rule", 3, { a: f64([1, null, 1]), b: f64([1, 1, 1]), c: f64([2, 2, null]) }),
    );
    const built = toOption(spec, a);
    const series = built.option.series as { markLine?: { data: unknown[] } }[];
    expect(series[1].markLine?.data).toEqual([[{ coord: [1, 1] }, { coord: [2, 1] }]]);
    expect(built.notes).toContain("2 rule segments with no place on the axes — not drawn");
  });
});
