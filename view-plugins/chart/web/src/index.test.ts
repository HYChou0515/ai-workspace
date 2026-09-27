/**
 * Importing the plugin registers `view: chart` — with its chat-card thumbnail
 * (#847/#848 P6), so a shown chart, gallery or layout pane is drawn small.
 */
import { expect, it, vi } from "vitest";

const sdk = vi.hoisted(() => ({ registerViewKind: vi.fn() }));
vi.mock("@aiws/view-sdk", () => sdk);
vi.mock("./echarts", () => ({ createChart: vi.fn() }));

it("registers the chart kind with its live view and its thumbnail", async () => {
  await import("./index");
  const { ChartView } = await import("./ChartView");
  const { ChartThumbnail } = await import("./Thumbnail");
  expect(sdk.registerViewKind).toHaveBeenCalledTimes(1);
  expect(sdk.registerViewKind).toHaveBeenCalledWith(
    expect.objectContaining({ kind: "chart", Component: ChartView, Thumbnail: ChartThumbnail }),
  );
});
