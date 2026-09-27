/**
 * #847/#848 PR 5 P30, against REAL ECharts (SSR, SVG): found live -- a pie's
 * legend row sat over the top of the pie, and over the label of the slice at
 * the top. The legend takes the row at 24 px; the pie was centred in the whole
 * chart, its radius 70% of half the height, so in a short pane its top (and a
 * label above it) reached up into that row.
 */
import * as echarts from "echarts/core";
import { SVGRenderer } from "echarts/renderers";
import { describe, expect, it } from "vitest";

import "./echarts"; // registers the chart's series + components
import { toOption } from "./option";
import { answer, base, cat, f64, layer } from "./testAnswer";

echarts.use([SVGRenderer]);

const PIE = {
  ...base,
  mark: "pie",
  encoding: { theta: { field: "n", type: "quantitative" }, color: { field: "k", type: "nominal" } },
};
// the demo's shares: the smallest slice ends at the top, its label above the pie
const SLICES = answer(layer("pie", 4, { n: f64([12, 9, 6, 4]), k: cat(["L1", "L2", "L3", "L4"]) }));

/** Where each text is drawn (its anchor's y), by its content, and the pie's top. */
function drawn(width: number, height: number) {
  const chart = echarts.init(null, null, { renderer: "svg", ssr: true, width, height });
  chart.setOption(toOption(PIE, SLICES).option, true);
  type Layout = { cy: number; r: number };
  type Model = { getSeriesByIndex(s: number): { getData(): { getItemLayout(i: number): Layout } } };
  const l = (chart as unknown as { getModel(): Model }).getModel().getSeriesByIndex(0).getData().getItemLayout(0);
  const svg = chart.renderToSVGString();
  chart.dispose();
  const texts = [...svg.matchAll(/<text[^>]*transform="translate\(([\d.]+) ([\d.]+)\)"[^>]*>(L\d)<\/text>/g)].map((m) => ({
    y: Number(m[2]),
    text: m[3]!,
  }));
  // the legend's four names share one row; each label is on its own
  const rows = new Map<number, number>();
  for (const t of texts) rows.set(t.y, (rows.get(t.y) ?? 0) + 1);
  const legendY = [...rows.entries()].find(([, n]) => n === 4)![0];
  return { legendY, labels: texts.filter((t) => t.y !== legendY), pieTop: l.cy - l.r };
}

describe("a pie under its legend", () => {
  for (const [w, h] of [
    [640, 240],
    [360, 300],
    [600, 400],
  ]) {
    it(`starts below the legend's row, labels and all (${w} x ${h})`, () => {
      const { legendY, labels, pieTop } = drawn(w, h);
      expect(labels).toHaveLength(4);
      // a 12 px text is drawn centred on its y: the row ends ~7 px below it
      const rowBottom = legendY + 7;
      expect(pieTop).toBeGreaterThan(rowBottom);
      for (const l of labels) expect(l.y - 7).toBeGreaterThan(rowBottom);
    });
  }
});
