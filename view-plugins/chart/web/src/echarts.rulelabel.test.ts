/**
 * #847/#848 PR 5 P26 against REAL ECharts (SSR, SVG): a rule's label reads
 * as its axis does. Seen in Chromium: a rule at "2026-03-01T12:00" on a
 * Taipei axis sat at 12:00 and was labelled "1772366400000" — the axis
 * position (the wall time as epoch ms), which is what ECharts labels a line
 * with. A rule on a category axis was labelled with its index the same way.
 */
import * as echarts from "echarts/core";
import { SVGRenderer } from "echarts/renderers";
import { describe, expect, it } from "vitest";

import "./echarts"; // registers the chart's series + components
import { toOption } from "./option";
import { answer, base, cat, f64, layer } from "./testAnswer";
import type { WireColumn } from "./wire";

echarts.use([SVGRenderer]);

function texts(doc: object, a: ReturnType<typeof answer>): string[] {
  const built = toOption(doc, a);
  const chart = echarts.init(null, null, { renderer: "svg", ssr: true, width: 900, height: 400 });
  chart.setOption(built.option, true);
  const svg = chart.renderToSVGString();
  chart.dispose();
  return [...svg.matchAll(/<text[^>]*>([^<]*)<\/text>/g)].map((m) => m[1]);
}

const hours = Array.from({ length: 24 }, (_, i) => Date.parse("2026-02-28T16:00:00Z") + i * 3_600_000);
const taipei: WireColumn = { ...(f64(hours) as { data: string }), kind: "time", zone: "Asia/Taipei" };
const values = f64(hours.map((_, i) => i));

describe("a rule's label, against real ECharts", () => {
  it("reads a time as the axis's clock does", () => {
    const doc = {
      ...base,
      layer: [
        { mark: "line", encoding: { x: { field: "at", type: "temporal" }, y: { field: "v", type: "quantitative" } } },
        { mark: "rule", encoding: { x: { datum: "2026-03-01T12:00" } } },
      ],
    };
    const drawn = texts(doc, answer(layer("line", 24, { at: taipei, v: values }), layer("rule", 0, {})));
    expect(drawn).toContain("2026-03-01 12:00");
    expect(drawn.some((t) => /^\d{12,}$/.test(t))).toBe(false);
  });

  it("names the category it sits on, not its index", () => {
    const doc = {
      ...base,
      layer: [
        { mark: "bar", encoding: { x: { field: "k", type: "nominal" }, y: { field: "v", type: "quantitative" } } },
        { mark: "rule", encoding: { x: { datum: "gamma" } } },
      ],
    };
    const drawn = texts(doc, answer(layer("bar", 3, { k: cat(["alpha", "beta", "gamma"]), v: f64([10, 20, 30]) }), layer("rule", 0, {})));
    // the axis draws "gamma" once; the rule's label is the second
    expect(drawn.filter((t) => t === "gamma")).toHaveLength(2);
    expect(drawn).not.toContain("2");
  });

  it("names a grid's cell as the grid's axis does", () => {
    const grid = (x: object) => ({
      mark: "grid",
      encoding: { x, y: { field: "y", type: "ordinal" }, color: { field: "v", type: "quantitative" } },
    });
    // hourly Taipei cells: the rule's cell reads like its axis label, 00:00 and all
    const hourly: WireColumn = { ...(f64(hours.slice(0, 3)) as { data: string }), kind: "time", zone: "Asia/Taipei" };
    const cells = { y: f64([0, 0, 0]), v: f64([1, 2, 3]) };
    const onTime = texts(
      { ...base, layer: [grid({ field: "at", type: "temporal" }), { mark: "rule", encoding: { x: { datum: "2026-03-01" } } }] },
      answer(layer("grid", 3, { at: hourly, ...cells }), layer("rule", 0, {})),
    );
    expect(onTime.filter((t) => t === "2026-03-01 00:00")).toHaveLength(2);
    // a text cell reads as written
    const onText = texts(
      { ...base, layer: [grid({ field: "k", type: "nominal" }), { mark: "rule", encoding: { x: { datum: "beta" } } }] },
      answer(layer("grid", 3, { k: cat(["alpha", "beta", "gamma"]), ...cells }), layer("rule", 0, {})),
    );
    expect(onText.filter((t) => t === "beta")).toHaveLength(2);
  });

  it("(pin) keeps a number axis's value as it is", () => {
    const doc = {
      ...base,
      layer: [
        { mark: "scatter", encoding: { x: { field: "a", type: "quantitative" }, y: { field: "v", type: "quantitative" } } },
        { mark: "rule", encoding: { y: { datum: 2.5 } } },
      ],
    };
    expect(texts(doc, answer(layer("scatter", 3, { a: f64([1, 2, 3]), v: f64([1, 2, 3]) }), layer("rule", 0, {})))).toContain("2.5");
  });
});
