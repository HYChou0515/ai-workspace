// @vitest-environment happy-dom
/**
 * #847/#848 PR 5 P30: every mark a person can select from writes the marking,
 * as every other mark does. The chart is REAL ECharts (SSR), so the gesture
 * selects what ECharts' own selector for that mark finds; the marking is the
 * host's real store, through the SDK double.
 */
import { act, cleanup, render } from "@testing-library/react";
import * as echarts from "echarts/core";
import { SVGRenderer } from "echarts/renderers";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { MarkingProvider } from "../../../../web/src/hooks/useMarking";
import { MarkingStore } from "../../../../web/src/lib/markings";
import { type Answer } from "./option";
import { answer, cat, f64, layer } from "./testAnswer";

const sdk = vi.hoisted(() => ({
  useSandboxRun: vi.fn(),
  viewDocument: vi.fn((s: { __doc: unknown }) => s.__doc),
  registerViewKind: vi.fn(),
}));
vi.mock("@aiws/view-sdk", async () => {
  const hooks = await import("../../../../web/src/hooks/useMarking");
  const lib = await import("../../../../web/src/lib/markings");
  return { ...sdk, useMarking: hooks.useMarking, useMarkingNames: hooks.useMarkingNames, isLit: lib.isLit };
});

// The chart's own module (its series, components and brush selectors), with
// the one instance it makes kept for the test: SSR, no DOM to draw in.
const made = vi.hoisted(() => ({ charts: [] as import("echarts/core").ECharts[] }));
vi.mock("./echarts", async (original) => {
  const real = await original<typeof import("./echarts")>();
  const core = await import("echarts/core");
  const { SVGRenderer: svg } = await import("echarts/renderers");
  core.use([svg]);
  return {
    ...real,
    createChart: () => {
      const chart = core.init(null, null, { renderer: "svg", ssr: true, width: 600, height: 400 });
      made.charts.push(chart);
      return chart;
    },
  };
});

import { ChartView } from "./ChartView";

echarts.use([SVGRenderer]);

// The chart debounces brush events (throttleDelay 250 ms in the option).
const settle = () => act(() => new Promise((r) => setTimeout(r, 400)));

function mount(store: MarkingStore, doc: object, a: Answer) {
  const stdout = JSON.stringify(a);
  sdk.useSandboxRun.mockReturnValue({ data: { stdout, stderr: "", exit_code: 0 }, error: null, isLoading: false, refetch: vi.fn() });
  render(
    <MarkingProvider store={store}>
      <ChartView spec={{ __doc: doc } as never} marking="m" path="/v/a.ai.yaml" type={null} entities={[]} onCreate={() => {}} onPatch={() => {}} />
    </MarkingProvider>,
  );
  return made.charts.at(-1)!;
}

/** A person's drag over the data rectangle x0..x1, y0..y1 (category indices
 * allowed as fractions): dispatched in PIXELS, as a drag is. */
async function drag(chart: echarts.ECharts, [[x0, x1], [y0, y1]]: [[number, number], [number, number]]) {
  const at = (p: number[]) => chart.convertToPixel({ gridIndex: 0 }, p) as number[];
  const [o, ex, ey] = [at([0, 0]), at([1, 0]), at([0, 1])];
  const px = (x: number, y: number) => [o[0]! + x * (ex[0]! - o[0]!), o[1]! + y * (ey[1]! - o[1]!)];
  const [p, q] = [px(x0, y0), px(x1, y1)];
  const range = [
    [Math.min(p[0]!, q[0]!), Math.max(p[0]!, q[0]!)],
    [Math.min(p[1]!, q[1]!), Math.max(p[1]!, q[1]!)],
  ];
  act(() => chart.dispatchAction({ type: "brush", areas: [{ brushType: "rect", range }] }));
  await settle();
}

const marked = (store: MarkingStore) =>
  Object.fromEntries(Object.entries(store.get("m")?.marking ?? {}).map(([k, v]) => [k, [...v].sort()]));

beforeEach(() => {
  made.charts.length = 0;
});
afterEach(() => {
  cleanup();
  for (const c of made.charts) c.dispose();
});

describe("a selection on every mark writes the marking", () => {
  it("a brush over a heatmap writes the lots of the cells whose centre it holds", async () => {
    const doc = {
      view: "chart",
      source: "data/a.csv",
      keys: ["lot"],
      mark: "heatmap",
      encoding: {
        x: { field: "x", type: "nominal" },
        y: { field: "y", type: "nominal" },
        color: { field: "v", type: "quantitative" },
      },
    };
    const a = answer(
      layer("heatmap", 4, {
        x: cat(["a", "b", "a", "b"]),
        y: cat(["p", "p", "q", "q"]),
        v: f64([1, 2, 3, 4]),
        lot: cat(["L1", "L2", "L3", "L4"]),
      }),
    );
    const store = new MarkingStore();
    const chart = mount(store, doc, a);
    // x 0.6..1.4 holds b's centre only; y -0.4..1.4 both rows: cells (b, p) and (b, q)
    await drag(chart, [[0.6, 1.4], [-0.4, 1.4]]);
    expect(marked(store)).toEqual({ lot: ["L2", "L4"] });
  });
});
