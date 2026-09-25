/**
 * The parts of the option that run later, inside ECharts: tooltip text,
 * renderItem callbacks, labels and sizes — called here with a fake `api`.
 */
import { describe, expect, it } from "vitest";

import { toOption } from "./option";
import { answer, base, cat, f64, layer, q8, time } from "./testAnswer";
import type { WireColumn } from "./wire";

type Fn = (...args: never[]) => unknown;
const fmt = (o: Record<string, unknown>) => (o.tooltip as { formatter: (p: unknown) => string }).formatter;

describe("tooltip text", () => {
  const spec = (tooltip: unknown) => ({
    ...base,
    mark: "scatter",
    encoding: { x: { field: "t", type: "temporal" }, y: { field: "v", type: "quantitative" }, tooltip },
  });

  it("escapes data, because ECharts renders tooltip text as HTML", () => {
    const a = answer(
      layer("scatter", 1, { t: time(["2024-01-01"]), v: f64([1]), who: cat(["<img src=x onerror=alert(1)>"]) }),
    );
    const text = fmt(toOption(spec({ field: "who", type: "nominal" }), a).option)({ seriesIndex: 0, dataIndex: 0 });
    expect(text).not.toContain("<img");
    expect(text).toContain("&#60;img");
  });

  it("writes a zone-less time as written, trims float noise and marks a missing value", () => {
    const a = answer(
      layer("scatter", 2, { t: time(["2024-01-01T00:00:00Z", "2024-01-02T00:00:00Z"]), v: f64([0.1 + 0.2, null]) }),
    );
    const f = fmt(toOption(spec(undefined), a).option);
    // #847/#848 P14: as written (the sandbox reads it as UTC), no zone named --
    // was the ISO instant "2024-01-01T00:00:00.000Z"
    expect(f({ seriesIndex: 0, dataIndex: 0 })).toContain("<b>2024-01-01</b>");
    expect(f({ seriesIndex: 0, dataIndex: 0 })).toContain("<b>0.3</b>");
    expect(f({ seriesIndex: 0, dataIndex: 1 })).toContain("<b>—</b>");
  });

  it("says nothing for a point it does not know", () => {
    const f = fmt(toOption(spec(undefined), answer(layer("scatter", 1, { t: time(["2024-01-01"]), v: f64([1]) }))).option);
    expect(f({ seriesIndex: 5, dataIndex: 0 })).toBe("");
    expect(f({ seriesIndex: 0, dataIndex: 9 })).toBe("");
  });

  it("counts the points behind a bin", () => {
    const s = {
      ...base,
      mark: "scatter",
      encoding: { x: { field: "a", type: "quantitative" }, y: { field: "b", type: "quantitative" } },
    };
    const a = answer(
      layer("scatter", 1, { a: f64([1]), b: f64([2]), $count: f64([7]) }, { binned: { points: 20000, bins: 1 } }),
    );
    expect(fmt(toOption(s, a).option)({ seriesIndex: 0, dataIndex: 0 })).toContain("points: <b>7</b>");
  });
});

describe("drawing callbacks", () => {
  const coord = (p: number[]) => [p[0] * 10, 100 - p[1] * 10];

  it("fits the grid image to the cell area, and draws nothing without an image host", () => {
    const spec = {
      ...base,
      mark: "grid",
      encoding: {
        x: { field: "x", type: "ordinal" },
        y: { field: "y", type: "ordinal" },
        color: { field: "v", type: "quantitative" },
      },
    };
    const a = answer(layer("grid", 2, { x: f64([0, 1]), y: f64([0, 0]), v: q8([0, 254], 0, 1) }));
    const image = { fake: "canvas" };
    const drawn = (toOption(spec, a, { gridImage: () => image }).option.series as { renderItem: Fn }[])[0];
    // Cells span x -0.5..1.5 and y -0.5..0.5 → pixels (-5, 95) to (15, 105).
    expect((drawn.renderItem as (p: unknown, api: unknown) => unknown)({}, { coord })).toEqual({
      type: "image",
      style: { image, x: -5, y: 95, width: 20, height: 10 },
    });
    const bare = toOption(spec, a).option;
    expect(((bare.series as { renderItem: Fn }[])[0].renderItem as (p: unknown, api: unknown) => unknown)({}, { coord })).toBeNull();
    const label = (bare.xAxis as { axisLabel: { formatter: (i: number) => string } }[])[0].axisLabel.formatter;
    expect([label(0), label(1), label(0.5), label(2)]).toEqual(["0", "1", "", ""]);
  });

  it("draws an errorbar as a stem with two caps", () => {
    const spec = {
      ...base,
      mark: "errorbar",
      encoding: { x: { field: "g", type: "nominal" }, y: { field: "v", type: "quantitative" } },
    };
    const a = answer(layer("errorbar", 1, { g: cat(["a"]), $lo: f64([1]), $mid: f64([2]), $hi: f64([3]) }));
    const s = (toOption(spec, a).option.series as { renderItem: Fn }[])[0];
    const values = [0, 1, 3];
    const drawn = (s.renderItem as (p: unknown, api: unknown) => { children: { shape: unknown }[] })(
      {},
      { coord, value: (d: number) => values[d], style: () => ({}) },
    );
    expect(drawn.children.map((c) => c.shape)).toEqual([
      { x1: 0, y1: 90, x2: 0, y2: 70 },
      { x1: -4, y1: 90, x2: 4, y2: 90 },
      { x1: -4, y1: 70, x2: 4, y2: 70 },
    ]);
  });

  it("labels a text mark with its text column", () => {
    const spec = {
      ...base,
      mark: "text",
      encoding: {
        x: { field: "a", type: "quantitative" },
        y: { field: "b", type: "quantitative" },
        text: { field: "t", type: "nominal" },
      },
    };
    const a = answer(layer("text", 2, { a: f64([1, 2]), b: f64([1, 2]), t: cat(["p", "q"]) }));
    const s = (toOption(spec, a).option.series as { label: { formatter: (p: unknown) => string } }[])[0];
    expect(s.label.formatter({ dataIndex: 1 })).toBe("q");
  });

  it("sizes a point by its size field, and evenly when every size is the same", () => {
    const spec = {
      ...base,
      mark: "scatter",
      encoding: {
        x: { field: "a", type: "quantitative" },
        y: { field: "b", type: "quantitative" },
        size: { field: "s", type: "quantitative" },
      },
    };
    const sized = (sizes: number[]) =>
      (
        toOption(spec, answer(layer("scatter", 2, { a: f64([1, 2]), b: f64([1, 2]), s: f64(sizes) }))).option.series as {
          symbolSize: (v: number[]) => number;
        }[]
      )[0].symbolSize;
    expect(sized([0, 10])([1, 1, 0])).toBe(4);
    expect(sized([0, 10])([1, 1, 10])).toBe(24);
    expect(sized([5, 5])([1, 1, 5])).toBe(10);
  });
});

describe("rules", () => {
  const line = {
    mark: "line",
    encoding: { x: { field: "a", type: "quantitative" }, y: { field: "b", type: "quantitative" } },
  };
  const markLine = (rule: unknown, cols: Record<string, WireColumn>, rows = 1) => {
    const spec = { ...base, layer: [line, rule] };
    const a = answer(layer("line", 1, { a: f64([1]), b: f64([2]) }), layer("rule", rows, cols));
    return (toOption(spec, a).option.series as { markLine: { data: unknown } }[])[1].markLine.data;
  };

  it("draws an x datum as a vertical line", () => {
    expect(markLine({ mark: "rule", encoding: { x: { datum: 4 } } }, {}, 0)).toEqual([{ xAxis: 4, name: undefined }]);
  });

  it("draws a y field as one horizontal line per row, and an x field as vertical ones", () => {
    const ys = markLine({ mark: "rule", encoding: { y: { field: "lim", type: "quantitative" } } }, { lim: f64([1, 2]) }, 2);
    expect(ys).toEqual([{ yAxis: 1 }, { yAxis: 2 }]);
    const xs = markLine({ mark: "rule", encoding: { x: { field: "at", type: "quantitative" } } }, { at: f64([3]) });
    expect(xs).toEqual([{ xAxis: 3 }]);
  });

  it("draws x..x2 at y as a segment", () => {
    const rule = {
      mark: "rule",
      encoding: {
        x: { field: "a", type: "quantitative" },
        x2: { field: "a2", type: "quantitative" },
        y: { field: "b", type: "quantitative" },
      },
    };
    expect(markLine(rule, { a: f64([1]), a2: f64([5]), b: f64([2]) })).toEqual([[{ coord: [1, 2] }, { coord: [5, 2] }]]);
  });
});

describe("options that pass through", () => {
  const enc = { x: { field: "a", type: "quantitative" }, y: { field: "b", type: "quantitative" } };
  const one = answer(layer("line", 1, { a: f64([1]), b: f64([2]) }));

  it("sets an axis domain", () => {
    const spec = {
      ...base,
      mark: "scatter",
      encoding: { ...enc, x: { ...enc.x, scale: { domain: [0, 5, 10] } } },
    };
    expect(toOption(spec, one).option.xAxis).toMatchObject([{ min: 0, max: 10 }]);
  });

  it("shows line points and smooths", () => {
    const l = toOption({ ...base, title: "T", mark: { type: "line", point: true, smooth: true }, encoding: enc }, one);
    expect(l.option.series).toMatchObject([{ type: "line", showSymbol: true, smooth: true }]);
  });

  // #847/#848 PR 5 P13 — the view header right above the chart already shows
  // the spec's `title:`. Drawn again inside the canvas it cost a row of every
  // pane's height and, in a narrow pane, sat under the brush toolbox.
  it("draws no title of its own, so the toolbox has the top row to itself", () => {
    const l = toOption({ ...base, title: "T", mark: "scatter", encoding: enc }, one);
    expect(l.option.title).toBeUndefined();
    expect(l.option.toolbox).toMatchObject({ top: 4, right: 8 });
    // the plot starts below the toolbox's row
    expect((l.option.grid as { top: number }).top).toBeGreaterThanOrEqual(32);
  });

  it("starts the plot below the legend's row when there is a legend", () => {
    const two = answer(layer("scatter", 2, { a: f64([1, 2]), b: f64([2, 3]), g: cat(["p", "q"]) }));
    const color = { ...enc, color: { field: "g", type: "nominal" } };
    const o = toOption({ ...base, mark: "scatter", encoding: color }, two).option;
    const legendTop = (o.legend as { top: number }).top;
    expect((o.grid as { top: number }).top).toBeGreaterThanOrEqual(legendTop + 24);
  });

  it("stacks bars and fades them by opacity, in the mark's colour", () => {
    const b = toOption({ ...base, mark: { type: "bar", stack: true, opacity: 0.4, color: "teal" }, encoding: enc }, one);
    expect(b.option.series).toMatchObject([{ type: "bar", stack: "stack", itemStyle: { color: "teal", opacity: 0.4 } }]);
  });

  it("switches a big scatter to large mode", () => {
    const n = 2001;
    const col = f64(Array.from({ length: n }, (_, i) => i));
    const spec = { ...base, mark: "scatter", encoding: enc };
    expect(toOption(spec, answer(layer("scatter", n, { a: col, b: col }))).option.series).toMatchObject([{ large: true }]);
  });

  it("draws a pie without a colour as numbered slices", () => {
    const spec = { ...base, mark: "pie", encoding: { theta: { field: "n", type: "quantitative" } } };
    const { option } = toOption(spec, answer(layer("pie", 2, { n: f64([3, 1]) })));
    expect(option.series).toMatchObject([{ data: [{ name: "0" }, { name: "1" }] }]);
  });

  it("puts a row with no category nowhere on the axis", () => {
    const bar = { mark: "bar", encoding: { x: { field: "k", type: "nominal" }, y: { field: "v", type: "quantitative" } } };
    const a = answer(layer("bar", 1, { k: cat(["a"]), v: f64([1]) }), layer("bar", 1, { k: cat([null]), v: f64([2]) }));
    expect((toOption({ ...base, layer: [bar, bar] }, a).option.series as { data: unknown[] }[])[1].data).toEqual([[null, 2]]);
  });
});
