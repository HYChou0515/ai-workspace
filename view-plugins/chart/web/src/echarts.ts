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

export type Chart = echarts.ECharts;

export function createChart(el: HTMLElement): Chart {
  return echarts.init(el);
}
