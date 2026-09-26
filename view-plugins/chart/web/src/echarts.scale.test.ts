/**
 * #847/#848 P16: a colour scale shows readable values. Found live: a heatmap's
 * colour bar labelled both ends "0" (ECharts' default precision is 0, the
 * values ran 0.056–0.346), and a grid's bar showed no numbers at all. Checked
 * against REAL ECharts (SSR, SVG): the text it draws beside the bar.
 */
import * as echarts from "echarts/core";
import { SVGRenderer } from "echarts/renderers";
import { describe, expect, it } from "vitest";

import "./echarts"; // registers the chart's series + components
import { scalePrecision, toOption } from "./option";
import { answer, base, f64, layer, q8 } from "./testAnswer";

echarts.use([SVGRenderer]);

function drawn(doc: object, a: ReturnType<typeof answer>): string[] {
  const built = toOption(doc, a, { gridImage: () => ({}) as unknown as HTMLCanvasElement });
  const chart = echarts.init(null, null, { renderer: "svg", ssr: true, width: 700, height: 400 });
  chart.setOption(built.option, true);
  const svg = chart.renderToSVGString();
  chart.dispose();
  return [...svg.matchAll(/<text[^>]*>([^<]*)<\/text>/g)].map((m) => m[1]);
}

const heat = {
  ...base,
  mark: "heatmap",
  encoding: {
    x: { field: "x", type: "ordinal" },
    y: { field: "y", type: "ordinal" },
    color: { field: "v", type: "quantitative" },
  },
};
const grid = { ...heat, mark: "grid" };

describe("a colour scale's labels", () => {
  it("tells a heatmap's ends apart", () => {
    const texts = drawn(heat, answer(layer("heatmap", 3, { x: f64([0, 1, 2]), y: f64([0, 0, 0]), v: f64([0.056, 0.2, 0.346]) })));
    expect(texts).toContain("0.06");
    expect(texts).toContain("0.35");
  });

  it("gives a grid's bar its two ends", () => {
    const texts = drawn(grid, answer(layer("grid", 3, { x: f64([0, 1, 2]), y: f64([0, 0, 0]), v: q8([0, 127, 254], 0.056, 0.346) })));
    expect(texts).toContain("0.06");
    expect(texts).toContain("0.35");
  });

  it("tells a scatter's colour bar ends apart", () => {
    const doc = {
      ...base,
      mark: "scatter",
      encoding: {
        x: { field: "x", type: "quantitative" },
        y: { field: "y", type: "quantitative" },
        color: { field: "v", type: "quantitative" },
      },
    };
    const texts = drawn(doc, answer(layer("scatter", 2, { x: f64([0, 1]), y: f64([0, 1]), v: f64([0.056, 0.346]) })));
    expect(texts).toContain("0.06");
    expect(texts).toContain("0.35");
  });

  it("keeps whole numbers whole on a wide range", () => {
    const texts = drawn(heat, answer(layer("heatmap", 2, { x: f64([0, 1]), y: f64([0, 0]), v: f64([12, 480]) })));
    expect(texts).toContain("12");
    expect(texts).toContain("480");
  });
});

describe("scalePrecision", () => {
  it("shows the range's width to two significant digits", () => {
    expect(scalePrecision(0.056, 0.346)).toBe(2); // 0.29 -> 0.06 … 0.35
    expect(scalePrecision(12, 480)).toBe(0);
    expect(scalePrecision(0, 1)).toBe(1);
    expect(scalePrecision(1.0003, 1.0009)).toBe(5); // 0.0006 -> 1.00030 … 1.00090
    expect(scalePrecision(-0.4, 0.4)).toBe(2);
  });

  it("reads a constant by its own size", () => {
    expect(scalePrecision(0.123, 0.123)).toBe(3);
    expect(scalePrecision(250, 250)).toBe(0);
    expect(scalePrecision(0, 0)).toBe(0);
  });
});
