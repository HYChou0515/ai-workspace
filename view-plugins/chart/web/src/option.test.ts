/**
 * spec + query answer → ECharts option. Pure: no chart instance, no DOM.
 *
 * Answers are built with `testAnswer.ts`, which encodes columns the way the
 * sandbox does (the wire format is pinned separately by wire-corpus/).
 */
import { describe, expect, it } from "vitest";

import { toOption } from "./option";
import { answer, base, cat, f64, layer, q8, time } from "./testAnswer";

describe("axes", () => {
  it("maps quantitative / temporal / nominal to value / time / category", () => {
    const spec = {
      ...base,
      mark: "line",
      encoding: { x: { field: "day", type: "temporal" }, y: { field: "v", type: "quantitative" } },
    };
    const { option } = toOption(spec, answer(layer("line", 2, { day: time(["2024-01-01", "2024-01-02"]), v: f64([1, 2]) })));
    expect(option.xAxis).toMatchObject([{ type: "time" }]);
    expect(option.yAxis).toMatchObject([{ type: "value" }]);

    const bars = {
      ...base,
      mark: "bar",
      encoding: { x: { field: "lot", type: "nominal" }, y: { field: "v", type: "quantitative" } },
    };
    const o2 = toOption(bars, answer(layer("bar", 2, { lot: cat(["b", "a"]), v: f64([1, 2]) }))).option;
    expect(o2.xAxis).toMatchObject([{ type: "category", data: ["a", "b"] }]);
  });

  it("honours a log scale, zero: false and a title", () => {
    const spec = {
      ...base,
      mark: "scatter",
      encoding: {
        x: { field: "a", type: "quantitative", scale: { zero: false }, title: "Thickness" },
        y: { field: "b", type: "quantitative", scale: { type: "log" } },
      },
    };
    const { option } = toOption(spec, answer(layer("scatter", 1, { a: f64([1]), b: f64([10]) })));
    expect(option.xAxis).toMatchObject([{ type: "value", scale: true, name: "Thickness" }]);
    expect(option.yAxis).toMatchObject([{ type: "log" }]);
  });

  it("orders a category axis by sort", () => {
    const enc = (sort: unknown) => ({
      ...base,
      mark: "bar",
      encoding: { x: { field: "k", type: "nominal", sort }, y: { field: "v", type: "quantitative" } },
    });
    const a = answer(layer("bar", 3, { k: cat(["a", "b", "c"]), v: f64([1, 2, 3]) }));
    expect(toOption(enc("descending"), a).option.xAxis).toMatchObject([{ data: ["c", "b", "a"] }]);
    expect(toOption(enc(["b"]), a).option.xAxis).toMatchObject([{ data: ["b", "a", "c"] }]);
  });
});

describe("series", () => {
  const scatterSpec = {
    ...base,
    mark: "scatter",
    encoding: {
      x: { field: "a", type: "quantitative" },
      y: { field: "b", type: "quantitative" },
      color: { field: "lot", type: "nominal" },
    },
  };
  const scatterAnswer = answer(
    layer("scatter", 4, { a: f64([1, 2, 3, 4]), b: f64([5, 6, 7, 8]), lot: cat(["A", "B", "A", null]) }),
  );

  it("splits a nominal colour into one series per level, with a legend", () => {
    const { option, series } = toOption(scatterSpec, scatterAnswer);
    const s = option.series as { type: string; name: string; data: unknown[] }[];
    expect(s.map((x) => [x.type, x.name])).toEqual([
      ["scatter", "A"],
      ["scatter", "B"],
      ["scatter", "(none)"],
    ]);
    expect(option.legend).toMatchObject({ data: ["A", "B", "(none)"] });
    // Which layer row each data point is — how a selection or a highlight maps back.
    expect(series.map((x) => x.rows)).toEqual([[0, 2], [1], [3]]);
    expect(s[0].data).toEqual([[1, 5], [3, 7]]);
  });

  it("lets every series blur the others when one is emphasized", () => {
    const { option } = toOption(scatterSpec, scatterAnswer);
    for (const s of option.series as { emphasis: { focus: string } }[]) expect(s.emphasis.focus).toBe("self");
  });

  it("maps a quantitative colour to a visualMap instead of series", () => {
    const spec = { ...scatterSpec, encoding: { ...scatterSpec.encoding, color: { field: "v", type: "quantitative" } } };
    const { option } = toOption(spec, answer(layer("scatter", 2, { a: f64([1, 2]), b: f64([3, 4]), v: f64([0.1, 0.9]) })));
    expect((option.series as unknown[]).length).toBe(1);
    expect(option.visualMap).toMatchObject([{ type: "continuous", min: 0.1, max: 0.9, dimension: 2 }]);
  });

  it("draws an area as a filled line, stacked when asked", () => {
    const spec = {
      ...base,
      mark: { type: "area", stack: true, opacity: 0.5 },
      encoding: { x: { field: "a", type: "quantitative" }, y: { field: "b", type: "quantitative" } },
    };
    const { option } = toOption(spec, answer(layer("area", 1, { a: f64([1]), b: f64([2]) })));
    expect(option.series).toMatchObject([{ type: "line", areaStyle: { opacity: 0.5 }, stack: "stack" }]);
  });

  it("draws a heatmap on category indices with a visualMap", () => {
    const spec = {
      ...base,
      mark: "heatmap",
      encoding: {
        x: { field: "x", type: "nominal" },
        y: { field: "y", type: "nominal" },
        color: { field: "v", type: "quantitative", scale: { scheme: "diverging" } },
      },
    };
    const a = answer(layer("heatmap", 2, { x: cat(["p", "q"]), y: cat(["r", "r"]), v: f64([-1, 2]) }));
    const { option } = toOption(spec, a);
    expect(option.series).toMatchObject([{ type: "heatmap", data: [[0, 0, -1], [1, 0, 2]] }]);
    expect(option.visualMap).toMatchObject([{ min: -2, max: 2 }]);
  });

  it("draws a pie from theta and colour", () => {
    const spec = {
      ...base,
      mark: "pie",
      encoding: { theta: { field: "n", type: "quantitative" }, color: { field: "k", type: "nominal" } },
    };
    const { option, series } = toOption(spec, answer(layer("pie", 2, { n: f64([3, 1]), k: cat(["a", "b"]) })));
    expect(option.series).toMatchObject([{ type: "pie", data: [{ name: "a", value: 3 }, { name: "b", value: 1 }] }]);
    expect(option.xAxis).toBeUndefined();
    expect(series[0].rows).toEqual([0, 1]);
  });

  it("draws a boxplot from the five numbers, and its outliers", () => {
    const spec = {
      ...base,
      mark: "boxplot",
      encoding: { x: { field: "g", type: "nominal" }, y: { field: "v", type: "quantitative" } },
    };
    const a = answer(
      layer(
        "boxplot",
        1,
        { g: cat(["a"]), $lo: f64([1]), $q1: f64([3]), $mid: f64([5]), $q3: f64([7]), $hi: f64([8]) },
        { outliers: { rows: 1, columns: { g: cat(["a"]), v: f64([100]) } } },
      ),
    );
    const { option } = toOption(spec, a);
    expect(option.series).toMatchObject([
      { type: "boxplot", data: [[0, 1, 3, 5, 7, 8]] },
      { type: "scatter", data: [[0, 100]] },
    ]);
  });

  it("draws an errorbar from $lo..$hi, or from y..y2", () => {
    const spec = {
      ...base,
      mark: "errorbar",
      encoding: { x: { field: "g", type: "nominal" }, y: { field: "v", type: "quantitative" } },
    };
    const a = answer(layer("errorbar", 1, { g: cat(["a"]), $lo: f64([1]), $mid: f64([2]), $hi: f64([3]) }));
    expect(toOption(spec, a).option.series).toMatchObject([{ type: "custom", data: [[0, 1, 3]] }]);

    const given = {
      ...base,
      mark: "errorbar",
      encoding: {
        x: { field: "g", type: "nominal" },
        y: { field: "lo", type: "quantitative" },
        y2: { field: "hi", type: "quantitative" },
      },
    };
    const b = answer(layer("errorbar", 1, { g: cat(["a"]), lo: f64([1]), hi: f64([4]) }));
    expect(toOption(given, b).option.series).toMatchObject([{ type: "custom", data: [[0, 1, 4]] }]);
  });

  it("draws a datum rule as a mark line and a text mark as labels", () => {
    const spec = {
      ...base,
      layer: [
        {
          mark: "line",
          encoding: { x: { field: "a", type: "quantitative" }, y: { field: "b", type: "quantitative" } },
        },
        { mark: { type: "rule", color: "red" }, encoding: { y: { datum: 3, title: "upper spec" } } },
        {
          mark: "text",
          encoding: {
            x: { field: "a", type: "quantitative" },
            y: { field: "b", type: "quantitative" },
            text: { field: "note", type: "nominal" },
          },
        },
      ],
    };
    const a = answer(
      layer("line", 1, { a: f64([1]), b: f64([2]) }),
      layer("rule", 0, {}),
      layer("text", 1, { a: f64([1]), b: f64([2]), note: cat(["hi"]) }),
    );
    const s = toOption(spec, a).option.series as Record<string, unknown>[];
    expect(s[1]).toMatchObject({ type: "line", data: [], markLine: { data: [{ yAxis: 3, name: "upper spec" }] } });
    expect(s[1].markLine).toMatchObject({ lineStyle: { color: "red" } });
    expect(s[2]).toMatchObject({ type: "scatter", symbolSize: 0, label: { show: true } });
  });

  it("draws a grid as one raster image on index axes", () => {
    const spec = {
      ...base,
      mark: "grid",
      encoding: {
        x: { field: "x", type: "ordinal" },
        y: { field: "y", type: "ordinal" },
        color: { field: "v", type: "quantitative" },
      },
    };
    const a = answer(layer("grid", 3, { x: f64([0, 1, 2]), y: f64([0, 0, 0]), v: q8([0, 127, 254], 0, 1) }));
    const image = { fake: "canvas" };
    const { option, grids } = toOption(spec, a, { gridImage: () => image });
    expect(grids).toHaveLength(1);
    expect(grids[0].cells.width).toBe(3);
    expect(option.xAxis).toMatchObject([{ type: "value", min: -0.5, max: 2.5 }]);
    expect(option.series).toMatchObject([{ type: "custom" }]);
    expect(option.visualMap).toMatchObject([{ min: 0, max: 1 }]);
  });
});

describe("interactions", () => {
  it("offers box brush and lasso, and a tooltip", () => {
    const spec = {
      ...base,
      mark: "scatter",
      encoding: { x: { field: "a", type: "quantitative" }, y: { field: "b", type: "quantitative" } },
    };
    const { option } = toOption(spec, answer(layer("scatter", 1, { a: f64([1]), b: f64([2]) })));
    expect(option.brush).toMatchObject({ toolbox: ["rect", "polygon", "clear"] });
    expect(option.toolbox).toMatchObject({ feature: { brush: { type: ["rect", "polygon", "clear"] } } });
    expect(option.tooltip).toMatchObject({ trigger: "item" });
  });

  it("names the tooltip fields for a point", () => {
    const spec = {
      ...base,
      mark: "scatter",
      encoding: {
        x: { field: "a", type: "quantitative" },
        y: { field: "b", type: "quantitative" },
        tooltip: [{ field: "wafer", type: "nominal" }],
      },
    };
    const { option } = toOption(spec, answer(layer("scatter", 1, { a: f64([1]), b: f64([2]), wafer: cat(["W7"]) })));
    const formatter = (option.tooltip as { formatter: (p: unknown) => string }).formatter;
    const text = formatter({ seriesIndex: 0, dataIndex: 0 });
    expect(text).toContain("wafer");
    expect(text).toContain("W7");
    expect(text).toContain("a");
  });

  it("says when a scatter was binned", () => {
    const spec = {
      ...base,
      mark: "scatter",
      encoding: { x: { field: "a", type: "quantitative" }, y: { field: "b", type: "quantitative" } },
    };
    const a = answer(
      layer("scatter", 1, { a: f64([1]), b: f64([2]), $count: f64([7]) }, { binned: { points: 20000, bins: 1 } }),
    );
    const { option, notes } = toOption(spec, a);
    expect(notes).toEqual(["20,000 points drawn as 1 bins"]);
    // A bin's size follows its count.
    expect((option.series as { symbolSize: unknown }[])[0].symbolSize).toBeTypeOf("function");
  });
});
