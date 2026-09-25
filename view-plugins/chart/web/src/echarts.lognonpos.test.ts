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
    const { svg } = svgOf({ type: "scatter" }, "x");
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
