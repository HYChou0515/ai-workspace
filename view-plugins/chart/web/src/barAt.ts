/**
 * #861 D3: where ECharts laid a bar out on the drawn chart, for the lit bar
 * drawn in front of it (`Options.barAt`). Asked of the chart that laid it out,
 * on every draw, never worked out again here: a bar's slot beside its
 * neighbours, its stack's base and its width are ECharts' own layout.
 */
import type { BarAt } from "./option";

type Layout = { x: number; y: number; width: number; height: number };
type Data = {
  getItemLayout(i: number): Layout | undefined;
  getItemVisual(i: number, key: "style"): { fill?: unknown } | undefined;
};
type Model = { getSeriesByIndex(i: number): { getData(): Data } | undefined };

/** `barAt` for the chart `chart()` returns (null before it is drawn). */
export function barAtOf(chart: () => unknown): BarAt {
  return (seriesIndex, dataIndex) => {
    const drawn = chart() as { getModel?: () => Model } | null;
    const data = drawn?.getModel?.().getSeriesByIndex(seriesIndex)?.getData();
    const at = data?.getItemLayout(dataIndex);
    if (!at) return undefined;
    const fill = data!.getItemVisual(dataIndex, "style")?.fill;
    return { x: at.x, y: at.y, width: at.width, height: at.height, ...(typeof fill === "string" ? { fill } : {}) };
  };
}
