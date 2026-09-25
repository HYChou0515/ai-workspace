/**
 * #847/#848 P18, against REAL ECharts (SSR, SVG): found live -- a rule's label
 * ran past the plot and was cut ("102" drew as "10"), and a grid's axis line
 * sat through the middle of the first cell rather than on the lattice's edge.
 */
import * as echarts from "echarts/core";
import { SVGRenderer } from "echarts/renderers";
import { describe, expect, it } from "vitest";

import "./echarts"; // registers the chart's series + components
import { toOption } from "./option";
import { answer, base, f64, layer, q8 } from "./testAnswer";

echarts.use([SVGRenderer]);

const W = 600;
const H = 400;
function svgOf(doc: object, a: ReturnType<typeof answer>): string {
  const built = toOption(doc, a, { gridImage: () => ({}) as unknown as HTMLCanvasElement });
  const chart = echarts.init(null, null, { renderer: "svg", ssr: true, width: W, height: H });
  chart.setOption(built.option, true);
  const svg = chart.renderToSVGString();
  chart.dispose();
  return svg;
}

describe("a rule's label", () => {
  it("is drawn inside the plot, ending before its right edge", () => {
    const doc = {
      ...base,
      layer: [
        { mark: "scatter", encoding: { x: { field: "a", type: "quantitative" }, y: { field: "b", type: "quantitative" } } },
        { mark: "rule", encoding: { y: { datum: 102.5 } } },
      ],
    };
    const svg = svgOf(doc, answer(layer("scatter", 3, { a: f64([1, 2, 3]), b: f64([99, 101, 104]) }), layer("rule", 0, {})));
    const label = /<text([^>]*)>102.5<\/text>/.exec(svg);
    expect(label).not.toBeNull();
    const attrs = label![1];
    const x = Number(/translate\(([\d.]+) /.exec(attrs)![1]);
    // anchored at its END, at or left of the plot's right edge (the grid's right margin is 16)
    expect(attrs).toContain('text-anchor="end"');
    expect(x).toBeLessThanOrEqual(W - 16);
  });
});

describe("a grid's axis lines", () => {
  it("sit on the lattice's edges, not through the first cell", () => {
    const doc = {
      ...base,
      mark: "grid",
      encoding: {
        x: { field: "x", type: "ordinal" },
        y: { field: "y", type: "ordinal" },
        color: { field: "v", type: "quantitative" },
      },
    };
    const built = toOption(doc, answer(layer("grid", 4, { x: f64([0, 1, 0, 1]), y: f64([0, 0, 1, 1]), v: q8([0, 80, 160, 254], 0, 1) })), {
      gridImage: () => ({}) as unknown as HTMLCanvasElement,
    });
    const chart = echarts.init(null, null, { renderer: "svg", ssr: true, width: W, height: H });
    chart.setOption(built.option, true);
    // the lattice's bottom-left corner in pixels: x of the left edge, y of the bottom edge
    const [left, bottom] = chart.convertToPixel({ gridIndex: 0 }, [-0.5, -0.5]) as number[];
    const svg = chart.renderToSVGString();
    chart.dispose();
    // the two axis lines: straight paths spanning the plot, one horizontal, one vertical
    const lines = [...svg.matchAll(/<path d="M([\d.]+) ([\d.]+)L([\d.]+) ([\d.]+)"[^>]*stroke="#6E7079"/g)].map((m) => m.slice(1, 5).map(Number));
    const horizontal = lines.find(([, y1, , y2]) => Math.abs(y1 - y2) < 0.01)!;
    const vertical = lines.find(([x1, , x2]) => Math.abs(x1 - x2) < 0.01)!;
    // within a pixel: ECharts shifts a 1 px line by half a pixel to draw it crisp
    expect(Math.abs(horizontal[1] - bottom)).toBeLessThanOrEqual(1);
    expect(Math.abs(vertical[0] - left)).toBeLessThanOrEqual(1);
  });
});
