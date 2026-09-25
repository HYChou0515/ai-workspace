/**
 * #847/#848 PR 5 P35 row 8: a log-y stack with gaps. A missing slot adds
 * nothing to the stack (0); it is empty (null) only where nothing lies
 * beneath it on a log axis -- the first series of a stack, or where every
 * series below is missing too -- since 0 has no place on a log axis (round 16
 * D3). Before, every filler on a log y was null, so a series whose rows never
 * sit on neighbouring slots drew nothing: its area broke at every gap (P34's
 * demo, three regions at every t, every 2nd t and every 3rd t).
 *
 * Against REAL ECharts (SSR): the drawn areas are read from the SVG.
 */
import * as echarts from "echarts/core";
import { SVGRenderer } from "echarts/renderers";
import { describe, expect, it } from "vitest";

import "./echarts"; // registers the chart's series + components
import { type Answer, toOption } from "./option";
import { answer, base, cat, f64, layer } from "./testAnswer";

echarts.use([SVGRenderer]);

type Model = {
  getSeriesByIndex(i: number): {
    getData(): { count(): number; get(dim: string, i: number): number; getCalculationInfo(key: string): string };
  };
};

// A at every t (5), B at every 2nd (40), C at every 3rd (300)
const REGIONS = [["A", 1, 5], ["B", 2, 40], ["C", 3, 300]] as const;
const T: number[] = [];
const Y: number[] = [];
const G: string[] = [];
for (let t = 1; t <= 12; t++) {
  for (const [g, every, y] of REGIONS) {
    if (t % every === 0) {
      T.push(t);
      Y.push(y);
      G.push(g);
    }
  }
}
const gaps = () => answer(layer("area", T.length, { t: f64(T), y: f64(Y), g: cat(G) }));
const encoding = {
  x: { field: "t", type: "quantitative" },
  y: { field: "y", type: "quantitative", scale: { type: "log" } },
  color: { field: "g", type: "nominal" },
};
const topLevel = { ...base, mark: { type: "area", stack: true }, encoding };
const asLayer = { ...base, layer: [{ mark: { type: "area", stack: true }, encoding }] };

function draw(doc: object, a: Answer) {
  const built = toOption(doc, a);
  const chart = echarts.init(null, null, { renderer: "svg", ssr: true, width: 600, height: 400 });
  chart.setOption(built.option, true);
  const model = (chart as unknown as { getModel(): Model }).getModel();
  return { chart, built, model, svg: chart.renderToSVGString() };
}

/** The area each filled region of the SVG covers, in px², per fill colour:
 * an area series is one path of closed subpaths (shoelace per subpath). */
function areas(svg: string): number[] {
  return [...svg.matchAll(/<path d="([^"]*)"[^>]*fill-opacity="0.7"/g)].map(([, d]) =>
    d
      .split("M")
      .filter(Boolean)
      .reduce((sum, sub) => {
        const pts = sub
          .replace(/Z/g, "")
          .split("L")
          .map((p) => p.trim().split(/\s+/).map(Number));
        let a = 0;
        for (let i = 0; i < pts.length; i++) {
          const [x0, y0] = pts[i];
          const [x1, y1] = pts[(i + 1) % pts.length];
          a += x0 * y1 - x1 * y0;
        }
        return sum + Math.abs(a) / 2;
      }, 0),
  );
}

/** Row -> its stacked top, over the points that draw a row. */
function tops(model: Model, built: ReturnType<typeof toOption>): Map<number, number> {
  const out = new Map<number, number>();
  built.series.forEach((s, i) => {
    const data = model.getSeriesByIndex(i).getData();
    const dim = data.getCalculationInfo("stackResultDimension");
    s.rows.forEach((r, j) => r !== null && out.set(r, data.get(dim, j)));
  });
  return out;
}

/** The oracle: each row's cumulative sum -- its own value plus every row of
 * the regions below it at its t. */
const cumulative = T.map((t, r) => {
  const below = REGIONS.slice(0, REGIONS.findIndex(([g]) => g === G[r]));
  return Y[r] + below.reduce((s, [, every, y]) => s + (t % every === 0 ? y : 0), 0);
});

describe.each([
  ["a top-level spec", topLevel],
  ["one layer of a `layer:` spec", asLayer],
])("a log-y stack with gaps, as %s (P35 row 8)", (_, doc) => {
  it("draws every series' area (red before: B and C drew none)", () => {
    const { chart, svg } = draw(doc, gaps());
    const drawn = areas(svg);
    expect(drawn).toHaveLength(3);
    for (const a of drawn) expect(a).toBeGreaterThan(100);
    chart.dispose();
  });

  it("puts every row's top at its cumulative sum", () => {
    const { chart, built, model } = draw(doc, gaps());
    const t = tops(model, built);
    expect(T.map((_, r) => t.get(r))).toEqual(cumulative);
    chart.dispose();
  });

  it("draws no Infinity or NaN", () => {
    const { chart, svg } = draw(doc, gaps());
    expect(svg).not.toContain("Infinity");
    expect(svg).not.toContain("NaN");
    chart.dispose();
  });
});

describe("a filler on a log y is empty only where nothing lies beneath it (P35 row 8)", () => {
  it("leaves the bottom series empty at its gaps, and a series above over a gap below it adds nothing", () => {
    // a at t 1, 3; b at t 1, 2, 3: a's filler at t=2 has nothing beneath (null),
    // b's rows at 1 and 3 sit on a, at 2 on nothing
    const a = answer(layer("area", 5, { t: f64([1, 3, 1, 2, 3]), y: f64([10, 10, 100, 100, 100]), g: cat(["a", "a", "b", "b", "b"]) }));
    const { chart, built, model, svg } = draw(topLevel, a);
    const series = built.option.series as { data: { value?: unknown[] }[] }[];
    // a: t 1, 2 (a filler), 3 -- y first
    expect(series[0].data[1]).toMatchObject({ value: [null, 2] });
    const t = tops(model, built);
    expect([2, 3, 4].map((r) => t.get(r))).toEqual([110, 100, 110]);
    expect(svg).not.toContain("Infinity");
    expect(svg).not.toContain("NaN");
    chart.dispose();
  });

  it("fills a series above a missing middle one with 0 where the bottom has a row", () => {
    // a at t 1, 2, 3; b at t 1, 3; c at t 1, 2, 3: b's filler at t=2 has a beneath (0)
    const a = answer(
      layer("area", 8, {
        t: f64([1, 2, 3, 1, 3, 1, 2, 3]),
        y: f64([10, 10, 10, 100, 100, 1000, 1000, 1000]),
        g: cat(["a", "a", "a", "b", "b", "c", "c", "c"]),
      }),
    );
    const { chart, built, model } = draw(topLevel, a);
    const series = built.option.series as { data: { value?: unknown[] }[] }[];
    expect(series[1].data[1]).toMatchObject({ value: [0, 2] });
    // c at t=2 sits on a alone: 10 + 1000
    expect(tops(model, built).get(6)).toBe(1010);
    chart.dispose();
  });
});
