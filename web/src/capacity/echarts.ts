/**
 * ECharts for the capacity dashboard (docs/plan-capacity-usage.md §10, D15) —
 * the ONE module of the SPA that imports it. Everything else reaches it with
 * `import("./echarts")` (or a type-only import), so Vite gives it its own
 * chunk, downloaded when a superuser opens the page; `web/src/ext/imports.test.ts`
 * holds both rules. Tree-shaken to what the dashboard draws: bar, line and
 * scatter series; grid, tooltip, legend and mark line; the canvas renderer.
 * Same `^5.6` as the chart plugin, which keeps its own copy.
 */
import { BarChart, LineChart, ScatterChart } from "echarts/charts";
import { GridComponent, LegendComponent, MarkLineComponent, TooltipComponent } from "echarts/components";
import * as echarts from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";

echarts.use([
  BarChart,
  LineChart,
  ScatterChart,
  GridComponent,
  LegendComponent,
  MarkLineComponent,
  TooltipComponent,
  CanvasRenderer,
]);

export { echarts };
export type { EChartsCoreOption as ChartOption, ECharts } from "echarts/core";
