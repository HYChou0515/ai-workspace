/**
 * A category colour's palette (#848 P19): a grid's categories take the colours
 * the chart's other marks give a nominal colour -- ECharts' own series palette,
 * read here from REAL ECharts as the oracle, not a copy of it kept by hand.
 */
import * as echarts from "echarts/core";
import { SVGRenderer } from "echarts/renderers";
import { describe, expect, it } from "vitest";

import "./echarts";
import { categoryTable, MISSING } from "./raster";

echarts.use([SVGRenderer]);

function echartsPalette(): string[] {
  const chart = echarts.init(null, null, { renderer: "svg", ssr: true, width: 100, height: 100 });
  chart.setOption({ series: [] });
  const colours = (chart.getOption() as { color: string[] }).color;
  chart.dispose();
  return colours;
}

const rgba = (table: Uint8ClampedArray, code: number) => Array.from(table.slice(code * 4, code * 4 + 4));
const hex = (h: string) => [Number.parseInt(h.slice(1, 3), 16), Number.parseInt(h.slice(3, 5), 16), Number.parseInt(h.slice(5, 7), 16), 255];

describe("categoryTable", () => {
  it("gives category i ECharts' series colour i, cycling as ECharts cycles", () => {
    const palette = echartsPalette();
    expect(palette.length).toBeGreaterThan(5);
    const table = categoryTable(palette.length + 2);
    for (let i = 0; i < palette.length + 2; i++) expect(rgba(table, i), `category ${i}`).toEqual(hex(palette[i % palette.length]));
  });

  it("leaves a missing cell transparent", () => {
    expect(rgba(categoryTable(3), MISSING)).toEqual([0, 0, 0, 0]);
  });
});
