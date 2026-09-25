/**
 * #847/#848 PR 5 P38: on a log axis a value at or below 0 has no place, so a
 * mark's point there is not drawn -- as a rule's datum already was not
 * (`pos`) -- and the chart's note line says how many were left out. Before,
 * `at` handed ECharts the value as the layer held it, and ECharts drew an
 * Infinity vertex (P37's demo, a stacked series' own row of 0).
 *
 * Against REAL ECharts (SSR): the SVG is read for Infinity / NaN.
 */
import * as echarts from "echarts/core";
import { SVGRenderer } from "echarts/renderers";
import { describe, expect, it } from "vitest";

import "./echarts"; // registers the chart's series + components
import { toOption } from "./option";
import { answer, base, cat, f64, layer } from "./testAnswer";

echarts.use([SVGRenderer]);

const X = [1, 2, 3, 4, 5];
const Y = [10, 0, 100, -5, 1000];
const G = ["a", "a", "a", "a", "a"];

function svgOf(mark: object, axis: "x" | "y") {
  const logged = { type: "quantitative", scale: { type: "log" } };
  const plain = { type: "quantitative" };
  const doc = {
    ...base,
    mark,
    encoding: {
      x: { field: axis === "x" ? "y" : "x", ...(axis === "x" ? logged : plain) },
      y: { field: axis === "x" ? "x" : "y", ...(axis === "y" ? logged : plain) },
      color: { field: "g", type: "nominal" },
    },
  };
  const built = toOption(doc, answer(layer("scatter", X.length, { x: f64(X), y: f64(Y), g: cat(G) })));
  const chart = echarts.init(null, null, { renderer: "svg", ssr: true, width: 600, height: 400 });
  chart.setOption(built.option, true);
  const svg = chart.renderToSVGString();
  chart.dispose();
  return { svg, notes: built.notes };
}

describe("a value at or below 0 on a log axis", () => {
  it.each([
    ["line", { type: "line" }],
    ["scatter", { type: "scatter" }],
    ["bar", { type: "bar" }],
    ["stacked area", { type: "area", stack: true }],
  ])("is not drawn on a log y (%s): no Infinity or NaN reaches the SVG", (_name, mark) => {
    const { svg } = svgOf(mark, "y");
    expect(svg).not.toMatch(/Infinity|NaN/);
  });

  it("is not drawn on a log x either", () => {
    // a line: ECharts itself skips a scatter's point there (round 19 conformance N3)
    const { svg } = svgOf({ type: "line" }, "x");
    expect(svg).not.toMatch(/Infinity|NaN/);
  });

  it("is counted in the chart's note line", () => {
    const { notes } = svgOf({ type: "scatter" }, "y");
    expect(notes).toContain("2 values at or below 0 not drawn on the log y axis");
  });

  it("leaves a rule's own values to the rule's note, counting them once", () => {
    const doc = {
      ...base,
      layer: [
        { mark: "scatter", encoding: { x: { field: "a", type: "quantitative" }, y: { field: "b", type: "quantitative", scale: { type: "log" } } } },
        { mark: "rule", encoding: { y: { field: "b", type: "quantitative" } } },
      ],
    };
    const a = answer(layer("scatter", 2, { a: f64([1, 2]), b: f64([1, 0]) }), layer("rule", 2, { b: f64([1, 0]) }));
    const built = toOption(doc, a);
    expect(built.notes).toContain("1 rule value with no place on the y axis — not drawn");
    expect(built.notes).toContain("1 value at or below 0 not drawn on the log y axis");
  });

  // P39: P38's demo (canvas, the app's renderer) showed a stacked series' own
  // 0 dropped: its band vanished over two intervals although the stack's top
  // there (60) fits the axis. In a stack a 0 adds nothing, as a filler does.
  describe("in a stack", () => {
    const T = [1, 2, 3, 4, 5, 1, 2, 3, 4, 5];
    const V = [20, 40, 30, 60, 50, 10, 15, 25, 0, 35];
    const S = ["S1", "S1", "S1", "S1", "S1", "S2", "S2", "S2", "S2", "S2"];
    const doc = {
      ...base,
      mark: { type: "area", stack: true },
      encoding: {
        x: { field: "t", type: "quantitative" },
        y: { field: "v", type: "quantitative", scale: { type: "log" } },
        color: { field: "s", type: "nominal" },
      },
    };
    const built = () => toOption(doc, answer(layer("area", T.length, { t: f64(T), v: f64(V), s: cat(S) })));
    const valueAt = (series: { data: unknown[] }, i: number) => {
      const d = series.data[i] as { value?: unknown[] } | unknown[];
      return (Array.isArray(d) ? d : (d.value as unknown[]))[0];
    };

    it("keeps a series' own 0 over something beneath as 0: it adds nothing, and its band stays joined", () => {
      const series = built().option.series as { data: unknown[] }[];
      expect(valueAt(series[1], 3)).toBe(0);
    });

    it("notes nothing for it: a 0 that adds nothing is drawn exactly", () => {
      expect(built().notes.filter((n) => n.includes("log"))).toEqual([]);
    });

    it("draws no Infinity or NaN", () => {
      const chart = echarts.init(null, null, { renderer: "svg", ssr: true, width: 600, height: 400 });
      chart.setOption(built().option, true);
      expect(chart.renderToSVGString()).not.toMatch(/Infinity|NaN/);
      chart.dispose();
    });

    it("still leaves out a 0 with nothing beneath it, and a negative, and counts them", () => {
      // the first series of a stack: nothing beneath its 0; a negative has no place
      const lone = toOption(
        { ...doc, encoding: { ...doc.encoding, color: undefined } },
        answer(layer("area", 5, { t: f64([1, 2, 3, 4, 5]), v: f64([10, 0, 100, -5, 1000]), s: cat(["a", "a", "a", "a", "a"]) })),
      );
      const series = lone.option.series as { data: unknown[] }[];
      expect(valueAt(series[0], 1)).toBeNull();
      expect(valueAt(series[0], 3)).toBeNull();
      expect(lone.notes).toContain("2 values at or below 0 not drawn on the log y axis");
    });
  });

  // P39: a line draws no point on its own, so a value between two left-out
  // ones was drawn nowhere -- the note counted 4 of the 6 values one could not see.
  it("shows a point whose neighbours are both left out, so every value not noted can be seen", () => {
    const doc = {
      ...base,
      mark: { type: "line" },
      encoding: {
        x: { field: "x", type: "quantitative" },
        y: { field: "y", type: "quantitative", scale: { type: "log" } },
      },
    };
    const built = toOption(doc, answer(layer("line", 5, { x: f64(X), y: f64(Y) })));
    const data = (built.option.series as { data: unknown[] }[])[0].data;
    const opacity = (i: number) => {
      const d = data[i] as { itemStyle?: { opacity?: number } } | unknown[];
      return Array.isArray(d) ? undefined : d.itemStyle?.opacity;
    };
    // 10 (t=1: nothing before, 0 after) and 100 (t=3: 0 before, -5 after) stand alone
    expect(opacity(0)).toBe(1);
    expect(opacity(2)).toBe(1);
    // 1000 at t=5 also stands alone (-5 before, nothing after)
    expect(opacity(4)).toBe(1);
  });

  it("leaves a dimmed lone point dimmed: only a line that hides its points needs one shown", () => {
    const doc = {
      ...base,
      mark: { type: "line" },
      encoding: {
        x: { field: "x", type: "quantitative" },
        y: { field: "y", type: "quantitative", scale: { type: "log" } },
      },
    };
    // lit: only the last row -- so the lone 10 at t=1 is dimmed, and stays so
    const lit = [false, false, false, false, true];
    const built = toOption(doc, answer(layer("line", 5, { x: f64(X), y: f64(Y) })), { lit: [lit] });
    const first = (built.option.series as { data: unknown[] }[])[0].data[0] as { itemStyle?: { opacity?: number } };
    expect(first.itemStyle?.opacity).toBeLessThan(1);
  });

  it("(control) a line's joined points stay as they were", () => {
    const doc = {
      ...base,
      mark: { type: "line" },
      encoding: {
        x: { field: "x", type: "quantitative" },
        y: { field: "y", type: "quantitative", scale: { type: "log" } },
      },
    };
    const built = toOption(doc, answer(layer("line", 3, { x: f64([1, 2, 3]), y: f64([1, 10, 100]) })));
    const data = (built.option.series as { data: unknown[] }[])[0].data;
    for (const d of data) expect(Array.isArray(d) ? undefined : (d as { itemStyle?: { opacity?: number } }).itemStyle?.opacity).not.toBe(1);
  });

  it("(control) a positive value on a log axis is drawn and noted nowhere", () => {
    const doc = {
      ...base,
      mark: { type: "scatter" },
      encoding: {
        x: { field: "x", type: "quantitative" },
        y: { field: "y", type: "quantitative", scale: { type: "log" } },
      },
    };
    const built = toOption(doc, answer(layer("scatter", 3, { x: f64([1, 2, 3]), y: f64([1, 10, 100]) })));
    expect(built.notes.filter((n) => n.includes("log"))).toEqual([]);
  });
});
