/**
 * ECharts, tree-shaken to what the chart spec can ask for (plan Q2): seven
 * series types (`line` also draws `area` and every `rule`, as a mark line; `scatter` also
 * draws `text`, `custom` draws `grid` and `errorbar`), the components the
 * option uses, and the canvas renderer. Bundled into the plugin, never the SPA.
 */
import { BarChart, BoxplotChart, CustomChart, HeatmapChart, LineChart, PieChart, ScatterChart } from "echarts/charts";
import {
  BrushComponent,
  GridComponent,
  LegendComponent,
  MarkLineComponent,
  TitleComponent,
  ToolboxComponent,
  TooltipComponent,
  VisualMapComponent,
} from "echarts/components";
import * as echarts from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";

echarts.use([
  BarChart,
  BoxplotChart,
  CustomChart,
  HeatmapChart,
  LineChart,
  PieChart,
  ScatterChart,
  BrushComponent,
  GridComponent,
  LegendComponent,
  MarkLineComponent,
  TitleComponent,
  ToolboxComponent,
  TooltipComponent,
  VisualMapComponent,
  CanvasRenderer,
]);

/** A brush or a lasso selects a line's (and an area's) points too (#847/#848
 * PR 5 P28). ECharts' line series has no brush selector, so a brush over a
 * line selected none of its rows. This one is its scatter series' — point in
 * area — at the point's LAYOUT, where it is drawn: a stacked area's point is
 * tested where the stack puts it, on any axis type. A line keeps its layout
 * as one typed array (`points`: x0, y0, x1, y1, …; echarts layout/points.js),
 * not per item. With it, ECharts reports a line's points in `brushselected`
 * as it does a scatter's, and `selectionFromBrush` maps them to layer rows
 * unchanged. */
// Every line this chart draws is on a grid (cartesian), so its layout is there
// whenever it has a point to test (a rule's line series has none).
type LineData = { getLayout(key: "points"): ArrayLike<number> };
// `getClass` is ECharts' class registry (enableClassManagement), untyped on SeriesModel.
const registry = echarts.SeriesModel as unknown as { getClass(main: string, sub: string): unknown };
const LineSeries = registry.getClass("series", "line") as {
  prototype: { brushSelector?: (i: number, data: LineData, selectors: { point: (xy: number[]) => boolean }) => boolean };
};
LineSeries.prototype.brushSelector = (i, data, selectors) => {
  const points = data.getLayout("points");
  return selectors.point([points[2 * i]!, points[2 * i + 1]!]);
};

/** What a selector reads of a series' data and its grid (the series model is
 * `this`: ECharts calls `seriesModel.brushSelector(...)`). */
type Data = {
  get(dim: string, i: number): number;
  mapDimension(coord: string): string;
  mapDimensionsAll(coord: string): string[];
  getItemLayout(i: number): unknown;
};
type Selectors = { point: (xy: number[]) => boolean; rect: (r: { x: number; y: number; width: number; height: number }) => boolean };
type Selector = (this: { coordinateSystem: { dataToPoint(p: number[]): number[] } }, i: number, data: Data, selectors: Selectors) => boolean;
const seriesClass = (sub: string) => registry.getClass("series", sub) as { prototype: { brushSelector?: Selector } };

/** A heatmap's cells (#847/#848 PR 5 P30): a cell is selected when its CENTRE
 * is in the area, as a grid's is (`selectionFromBrush`). ECharts' heatmap has
 * no selector, so a brush over one selected nothing. A cell with no value is
 * not drawn (echarts HeatmapView skips it), so it is not selected either. */
seriesClass("heatmap").prototype.brushSelector = function (i, data, selectors) {
  if (Number.isNaN(data.get(data.mapDimension("value"), i))) return false;
  const at = [data.get(data.mapDimension("x"), i), data.get(data.mapDimension("y"), i)];
  return selectors.point(this.coordinateSystem.dataToPoint(at));
};

/** The smallest rect holding `points` (pixels). */
function bounds(points: number[][]): { x: number; y: number; width: number; height: number } {
  const xs = points.map((p) => p[0]!);
  const ys = points.map((p) => p[1]!);
  const [x, y] = [Math.min(...xs), Math.min(...ys)];
  return { x, y, width: Math.max(...xs) - x, height: Math.max(...ys) - y };
}

/** A boxplot's groups (#847/#848 PR 5 P30): a group is selected when the area
 * meets its BOX (q1..q3, the box's width) -- ECharts' own rect test, as a
 * bar's. Its first four layout ends are the box's corners (echarts
 * boxplotLayout `ends`). ECharts' boxplot has no selector. */
seriesClass("boxplot").prototype.brushSelector = (i, data, selectors) => {
  // (every item has one: option.ts always encodes x and the five y values)
  const layout = data.getItemLayout(i) as { ends: number[][] };
  return selectors.rect(bounds(layout.ends.slice(0, 4)));
};

/** An errorbar's groups (#847/#848 PR 5 P30): an errorbar is a custom series
 * (option.ts) drawn as a line from its first y value to its second at its x;
 * a group is selected when the area meets that centre line. (The other
 * custom series, a grid's image, draws no row: `Built.series` maps its one
 * datum to none, and its cells are found by `selectionFromBrush`.) */
seriesClass("custom").prototype.brushSelector = function (i, data, selectors) {
  const x = data.get(data.mapDimension("x"), i);
  const ends = data.mapDimensionsAll("y").map((d) => this.coordinateSystem.dataToPoint([x, data.get(d, i)]));
  return selectors.rect(bounds(ends));
};

export type Chart = echarts.ECharts;

/** `devicePixelRatio`: the canvas's pixels per CSS pixel, when not the
 * screen's (a thumbnail drawn scaled down). */
export function createChart(el: HTMLElement, opts: { devicePixelRatio?: number } = {}): Chart {
  return echarts.init(el, null, opts);
}
