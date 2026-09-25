/**
 * Test helpers: a person's click on a real (SSR) ECharts instance, and where
 * to click to hit a pie's slice.
 */
import type * as echarts from "echarts/core";

/** A person's click at the pixel (x, y): zrender's own mousedown, mouseup,
 * click, which is how a click on a canvas reaches ECharts. */
export function clickAt(chart: echarts.ECharts, [x, y]: number[]): void {
  type Handler = Record<"mousedown" | "mouseup" | "click", (e: object) => void>;
  const handler = (chart.getZr() as unknown as { handler: Handler }).handler;
  const e = { zrX: x, zrY: y, offsetX: x, offsetY: y };
  handler.mousedown(e);
  handler.mouseup(e);
  handler.click(e);
}

/** The pixel inside slice `i` of series `s`: its mid angle, 0.6 of its radius out. */
export function sliceAt(chart: echarts.ECharts, i: number, s = 0): number[] {
  type Layout = { cx: number; cy: number; r: number; startAngle: number; endAngle: number };
  type Model = { getSeriesByIndex(s: number): { getData(): { getItemLayout(i: number): Layout } } };
  const l = (chart as unknown as { getModel(): Model }).getModel().getSeriesByIndex(s).getData().getItemLayout(i);
  const a = (l.startAngle + l.endAngle) / 2;
  return [l.cx + 0.6 * l.r * Math.cos(a), l.cy + 0.6 * l.r * Math.sin(a)];
}
