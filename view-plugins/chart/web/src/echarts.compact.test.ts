/**
 * #847/#848 PR 5 P31, against REAL ECharts (SSR, SVG): a chart in a narrow
 * pane keeps a plot. Found live at 390 wide: in a pane that gave a `grid` a
 * 139 px canvas the plot was about 2 px wide -- 48 px kept for the y axis's
 * name on the left, 80 for the colour bar on the right, and the tick labels
 * between -- so no cell showed and the lasso had nothing to draw on.
 *
 * Below `COMPACT_BELOW` px the chart is laid out compact (`compactAt`, which
 * ChartView asks with the width it measures): the colour bar or category
 * legend goes under the plot, the y axis's name above it, and the tools to the
 * plot's right edge. The plot, the bar and the tools are read from the chart
 * as ECharts laid it out.
 */
import * as echarts from "echarts/core";
import { SVGRenderer } from "echarts/renderers";
import { describe, expect, it } from "vitest";

import "./echarts"; // registers the chart's series + components
import { COMPACT_BELOW, compactAt, toOption } from "./option";
import { answer, base, cat, f64, layer, q8 } from "./testAnswer";
import { toolAt } from "./testGesture";
import type { WireColumn } from "./wire";

echarts.use([SVGRenderer]);

type Box = { left: number; right: number; top: number; bottom: number };

function laidOut(doc: object, a: ReturnType<typeof answer>, width: number, height: number) {
  const built = toOption(doc, a, { gridImage: () => ({}) as unknown as HTMLCanvasElement, compact: compactAt(width) });
  const chart = echarts.init(null, null, { renderer: "svg", ssr: true, width, height });
  chart.setOption(built.option, true);
  type Rect = { x: number; y: number; width: number; height: number };
  type View = { type: string; group: { getBoundingRect(): Rect; x: number; y: number } };
  const views = (chart as unknown as { _componentsViews: View[] })._componentsViews;
  const boxOf = (v: View): Box => {
    const r = v.group.getBoundingRect();
    return { left: v.group.x + r.x, right: v.group.x + r.x + r.width, top: v.group.y + r.y, bottom: v.group.y + r.y + r.height };
  };
  type Grid = { coordinateSystem: { getRect(): Rect } };
  const rect = (chart as unknown as { getModel(): { getComponent(m: string): Grid } }).getModel().getComponent("grid").coordinateSystem.getRect();
  const plot: Box = { left: rect.x, right: rect.x + rect.width, top: rect.y, bottom: rect.y + rect.height };
  const tools = boxOf(views.find((v) => v.type === "toolbox")!);
  const bars = views.filter((v) => v.type.startsWith("visualMap")).map(boxOf);
  chart.dispose();
  return { plot, tools, bars };
}

const apart = (a: Box, b: Box) => a.right <= b.left || b.right <= a.left || a.bottom <= b.top || b.bottom <= a.top;
const inside = (a: Box, width: number, height: number) => a.left >= 0 && a.top >= 0 && a.right <= width && a.bottom <= height;

// a 24 x 24 lattice, as the live check's
const SIDE = 24;
const xs = Array.from({ length: SIDE * SIDE }, (_, i) => i % SIDE);
const ys = Array.from({ length: SIDE * SIDE }, (_, i) => Math.floor(i / SIDE));
const lattice = (color: WireColumn, type: string) => ({
  doc: {
    ...base,
    mark: "grid",
    encoding: { x: { field: "cx", type: "ordinal" }, y: { field: "cy", type: "ordinal" }, color: { field: "value", type } },
  },
  a: answer(layer("grid", SIDE * SIDE, { cx: f64(xs), cy: f64(ys), value: color })),
});
const GRID = lattice(q8(xs.map((x) => (x * 10) % 255), 0, 0.71), "quantitative");
const CATEGORY = lattice(cat(xs.map((x) => ["alpha", "beta", "gamma"][x % 3]!)), "nominal");
const HEATMAP = {
  doc: {
    ...base,
    mark: "heatmap",
    encoding: { x: { field: "cx", type: "ordinal" }, y: { field: "cy", type: "ordinal" }, color: { field: "value", type: "quantitative" } },
  },
  a: answer(layer("heatmap", 36, { cx: cat(xs.slice(0, 36).map((x) => x % 6)), cy: cat(ys.slice(0, 36).map((y) => y % 6)), value: f64(xs.slice(0, 36).map((x) => x / 10)) })),
};
const COLOURED_SCATTER = {
  doc: {
    ...base,
    mark: "scatter",
    encoding: {
      x: { field: "level", type: "quantitative" },
      y: { field: "value", type: "quantitative" },
      color: { field: "value", type: "quantitative" },
    },
  },
  a: answer(layer("scatter", 40, { level: f64(xs.slice(0, 40).map((x) => 0.42 + x / 100)), value: f64(xs.slice(0, 40).map((x) => x / 40)) })),
};

describe("a chart in a narrow pane keeps a plot, against real ECharts", () => {
  const cases = [
    ["a grid", GRID],
    ["a category grid", CATEGORY],
    ["a heatmap", HEATMAP],
    ["a scatter with a colour scale", COLOURED_SCATTER],
  ] as const;

  it.each(cases)("%s, 139 px wide: a plot at least 90 px wide and 300 tall", (_, { doc, a }) => {
    const { plot } = laidOut(doc, a, 139, 564);
    expect({ width: plot.right - plot.left, height: plot.bottom - plot.top }).toMatchObject({
      width: expect.toSatisfy((w: number) => w >= 90),
      height: expect.toSatisfy((h: number) => h >= 300),
    });
  });

  it.each(cases)("%s, 84 px wide: a plot at least 40 px wide", (_, { doc, a }) => {
    const { plot } = laidOut(doc, a, 84, 549);
    expect(plot.right - plot.left).toBeGreaterThanOrEqual(40);
  });

  // 139: the live check's pane; 84 and 69: three panes side by side at 390
  const SIZES = [
    [139, 564],
    [84, 549],
    [69, 515],
  ] as const;
  const each = cases.flatMap(([name, c]) => SIZES.map(([w, h]) => [name, w, h, c] as const));

  it.each(each)("%s, %i px wide: its colour bar or legend is inside the chart, clear of the plot and the tools", (_, w, h, { doc, a }) => {
    const { plot, tools, bars } = laidOut(doc, a, w, h);
    expect(bars.length).toBe(1);
    const [bar] = bars;
    expect({ bar, inside: inside(bar!, w, h), clearOfPlot: apart(bar!, plot), clearOfTools: apart(bar!, tools) }).toMatchObject({
      inside: true,
      clearOfPlot: true,
      clearOfTools: true,
    });
  });

  it.each(each)("%s, %i px wide: the three tools sit in one row inside the chart", (_, w, h, { doc, a }) => {
    const built = toOption(doc, a, { gridImage: () => ({}) as unknown as HTMLCanvasElement, compact: compactAt(w) });
    const chart = echarts.init(null, null, { renderer: "svg", ssr: true, width: w, height: h });
    chart.setOption(built.option, true);
    const at = ["rect", "polygon", "clear"].map((t) => toolAt(chart, t)!);
    chart.dispose();
    expect(new Set(at.map(([, y]) => Math.round(y!))).size).toBe(1);
    // an icon is 15 px or less: its centre at least 7 px in from either edge
    expect(at.every(([x]) => x! >= 7 && x! <= w - 7)).toBe(true);
  });

  it.each(each)("%s, %i px wide: the axis names are inside the chart, clear of the tools and of the bar", (_, w, h, { doc, a }) => {
    const built = toOption(doc, a, { gridImage: () => ({}) as unknown as HTMLCanvasElement, compact: compactAt(w) });
    const chart = echarts.init(null, null, { renderer: "svg", ssr: true, width: w, height: h });
    chart.setOption(built.option, true);
    const svg = chart.renderToSVGString();
    const { tools, bars } = laidOut(doc, a, w, h);
    chart.dispose();
    const y = (doc as { encoding: { y: { field: string } } }).encoding.y.field;
    const x = (doc as { encoding: { x: { field: string } } }).encoding.x.field;
    const at = (name: string) => {
      const m = new RegExp(`<text[^>]*transform="translate\\(([-\\d.]+) ([-\\d.]+)\\)"[^>]*>${name}</text>`).exec(svg);
      return m ? { x: Number(m[1]), y: Number(m[2]) } : null;
    };
    const [yName, xName] = [at(y), at(x)];
    expect({ yName: !!yName, xName: !!xName }).toEqual({ yName: true, xName: true });
    // ~7 px a character at 12 px, from its anchor rightwards (aligned left), 12 px tall
    const yBox = { left: yName!.x, right: yName!.x + 7 * y.length, top: yName!.y - 12, bottom: yName!.y + 2 };
    expect({ inside: inside(yBox, w, h), clearOfTools: apart(yBox, tools) }).toEqual({ inside: true, clearOfTools: true });
    // the x axis's name is centred on its anchor, and sits above the bar or legend under the plot
    const xBox = { left: xName!.x - 3.5 * x.length, right: xName!.x + 3.5 * x.length, top: xName!.y - 12, bottom: xName!.y + 2 };
    expect({ inside: inside(xBox, w, h), clearOfBar: apart(xBox, bars[0]!) }).toEqual({ inside: true, clearOfBar: true });
  });

  it("is laid out compact below COMPACT_BELOW px and not at it", () => {
    expect(compactAt(COMPACT_BELOW - 1)).toBe(true);
    expect(compactAt(COMPACT_BELOW)).toBe(false);
    // unmeasured (0): the wide layout, as before anything is known
    expect(compactAt(0)).toBe(false);
  });

  it("(control) a wide chart keeps its colour bar beside the plot", () => {
    const { plot, bars } = laidOut(GRID.doc, GRID.a, 600, 400);
    expect(bars[0]!.left).toBeGreaterThanOrEqual(plot.right);
  });
});
