/**
 * #847/#848 P14 against REAL ECharts (SSR, SVG): a time axis's labels are the
 * column's clock, identical for a viewer in any zone. Before, ECharts
 * formatted in the viewer's zone: Taipei midnight read 16:00 for a viewer in
 * UTC and 00:00 for one in Taipei.
 */
import * as echarts from "echarts/core";
import { SVGRenderer } from "echarts/renderers";
import { afterAll, describe, expect, it } from "vitest";

import "./echarts"; // registers the chart's series + components
import { toOption } from "./option";
import { answer, base, f64, layer } from "./testAnswer";
import type { WireColumn } from "./wire";

echarts.use([SVGRenderer]);

const ORIGINAL_TZ = process.env.TZ;
afterAll(() => {
  process.env.TZ = ORIGINAL_TZ;
});

const hours = (n: number, from: string) => Array.from({ length: n }, (_, i) => Date.parse(from) + i * 3_600_000);
const timeCol = (ms: number[], zone?: string): WireColumn => ({
  ...(f64(ms) as { data: string }),
  kind: "time",
  ...(zone ? { zone } : {}),
});

/** The x axis's tick labels as ECharts draws them, for a viewer in `tz`. */
function labels(tz: string, x: WireColumn, n: number): string[] {
  process.env.TZ = tz;
  const doc = {
    ...base,
    mark: "line",
    encoding: { x: { field: "at", type: "temporal" }, y: { field: "v", type: "quantitative" } },
  };
  const built = toOption(doc, answer(layer("line", n, { at: x, v: f64(Array.from({ length: n }, (_, i) => i)) })));
  const chart = echarts.init(null, null, { renderer: "svg", ssr: true, width: 900, height: 300 });
  chart.setOption(built.option, true);
  const svg = chart.renderToSVGString();
  chart.dispose();
  // the x axis's labels sit on its line, below the plot: every text on that row
  // the x axis's labels share one row (the y axis's each have their own):
  // the most common row, in left-to-right order
  const texts = [...svg.matchAll(/<text[^>]*transform="translate\(([\d.]+) ([\d.]+)\)"[^>]*>([^<]*)<\/text>/g)];
  const rows = new Map<string, number>();
  for (const t of texts) rows.set(t[2], (rows.get(t[2]) ?? 0) + 1);
  const row = [...rows].sort((a, b) => b[1] - a[1])[0][0];
  return texts
    .filter((t) => t[2] === row)
    .sort((a, b) => Number(a[1]) - Number(b[1]))
    .map((t) => t[3]);
}

describe("a time axis against real ECharts", () => {
  it("(control) a viewer's zone moved the labels before useUTC", () => {
    // the same axis without the chart's frame: ECharts' default, the viewer's zone
    const draw = (tz: string) => {
      process.env.TZ = tz;
      const chart = echarts.init(null, null, { renderer: "svg", ssr: true, width: 900, height: 300 });
      chart.setOption({
        xAxis: { type: "time" },
        yAxis: { type: "value" },
        series: [{ type: "line", data: hours(48, "2026-02-28T16:00:00Z").map((t, i) => [t, i]) }],
      });
      const svg = chart.renderToSVGString();
      chart.dispose();
      return [...svg.matchAll(/>(\d\d:\d\d)</g)].map((m) => m[1]);
    };
    expect(draw("UTC")).not.toEqual(draw("Asia/Tokyo"));
  });

  it("labels a zoned column on its own clock, whatever the viewer's zone", () => {
    // two Taipei days, hourly, from Taipei midnight
    const x = timeCol(hours(48, "2026-02-28T16:00:00Z"), "Asia/Taipei");
    const seen = ["UTC", "America/Los_Angeles", "Asia/Tokyo"].map((tz) => labels(tz, x, 48));
    expect(seen[1]).toEqual(seen[0]);
    expect(seen[2]).toEqual(seen[0]);
    // the axis starts at Taipei's midnight on 1 March and the day ticks are
    // Taipei's midnights ("Mar" is the month's first day, "2" the 2nd)
    expect(seen[0]).toEqual(["Mar", "06:00", "12:00", "18:00", "2", "06:00", "12:00", "18:00"]);
  });

  it("labels a zone-less column as written, whatever the viewer's zone", () => {
    const x = timeCol(hours(48, "2026-03-01T00:00:00Z"));
    const seen = ["UTC", "America/Los_Angeles"].map((tz) => labels(tz, x, 48));
    expect(seen[1]).toEqual(seen[0]);
  });
});
