/**
 * #847/#848 P19: a grid coloured by CATEGORIES painted one colour -- its
 * category codes went through the continuous ramp built from a min and max
 * a category column does not have (0 and 0), and its colour bar read "0 … 0".
 * Found in a real browser. A category grid now paints each level in the
 * chart's category palette -- the palette a nominal colour gets everywhere
 * else in the chart, one constant -- with a legend naming the levels. The
 * palette gives a level a colour only, never a meaning (Q23).
 */
import * as echarts from "echarts/core";
import { SVGRenderer } from "echarts/renderers";
import { describe, expect, it } from "vitest";

import "./echarts"; // registers the chart's series + components
import { CATEGORY_COLOURS, toOption } from "./option";
import { categoryTable } from "./raster";
import { answer, base, cat, f64, layer } from "./testAnswer";

echarts.use([SVGRenderer]);

const hex = (d: Uint8ClampedArray, i: number) =>
  `#${[d[i * 4], d[i * 4 + 1], d[i * 4 + 2]].map((n) => n.toString(16).padStart(2, "0")).join("")}`;

const doc = {
  ...base,
  mark: "grid",
  encoding: {
    x: { field: "x", type: "ordinal" },
    y: { field: "y", type: "ordinal" },
    color: { field: "kind", type: "nominal" },
  },
};
// one row, three cells: alpha, beta, gamma, then alpha again
const a = answer(layer("grid", 4, { x: f64([0, 1, 2, 3]), y: f64([0, 0, 0, 0]), kind: cat(["alpha", "beta", "gamma", "alpha"]) }));

describe("a grid coloured by categories", () => {
  it("paints each level its own colour from the category palette", () => {
    const { grids } = toOption(doc, a, { gridImage: () => ({}) });
    const img = grids[0].image;
    const colours = [0, 1, 2, 3].map((i) => hex(img.data, i));
    expect(colours).toEqual([CATEGORY_COLOURS[0], CATEGORY_COLOURS[1], CATEGORY_COLOURS[2], CATEGORY_COLOURS[0]]);
  });

  it("uses the palette a nominal colour gets elsewhere in the chart", () => {
    const { option } = toOption(doc, a, { gridImage: () => ({}) });
    expect(option.color).toBe(CATEGORY_COLOURS);
  });

  it("names its levels in a legend, and draws no number scale", () => {
    const built = toOption(doc, a, { gridImage: () => ({}) as unknown as HTMLCanvasElement });
    const chart = echarts.init(null, null, { renderer: "svg", ssr: true, width: 700, height: 400 });
    chart.setOption(built.option, true);
    const svg = chart.renderToSVGString();
    chart.dispose();
    const texts = [...svg.matchAll(/<text[^>]*>([^<]*)<\/text>/g)].map((m) => m[1]);
    expect(texts).toEqual(expect.arrayContaining(["alpha", "beta", "gamma"]));
    // the x axis's first cell and the y axis's one row are each labelled 0;
    // the old number bar drew two more ("0" at both ends)
    expect(texts.filter((t) => t === "0")).toHaveLength(2);
  });
});

describe("categoryTable", () => {
  it("gives code i the palette's colour i, cycling, and leaves a missing cell clear", () => {
    const t = categoryTable(12);
    expect(hex(t, 0)).toBe(CATEGORY_COLOURS[0]);
    expect(hex(t, CATEGORY_COLOURS.length)).toBe(CATEGORY_COLOURS[0]); // cycles past the palette
    expect(hex(t, 11)).toBe(CATEGORY_COLOURS[11 % CATEGORY_COLOURS.length]);
    expect(t[11 * 4 + 3]).toBe(255);
    expect(Array.from(t.slice(255 * 4, 256 * 4))).toEqual([0, 0, 0, 0]);
    // more levels than one byte holds: code 255 is still "no value", clear
    expect(Array.from(categoryTable(300).slice(255 * 4, 256 * 4))).toEqual([0, 0, 0, 0]);
  });
});
