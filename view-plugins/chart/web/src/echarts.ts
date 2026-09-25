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

export type Chart = echarts.ECharts;

export function createChart(el: HTMLElement): Chart {
  return echarts.init(el);
}
