// @vitest-environment happy-dom
/**
 * #847/#848 PR 5 P43: beside "by <columns>", the chart's count is what went to
 * the marking. P42's demo boxed group b's six points and one stack segment on
 * a chart keyed by a field the stack does not link by: it said "7 selected ·
 * by item" while the marking held six items -- the segment wrote nothing.
 *
 * Real ECharts (SSR) and the host's real MarkingStore, as ChartView.drop.
 */
import { act, cleanup, render, screen } from "@testing-library/react";
import * as echarts from "echarts/core";
import { SVGRenderer } from "echarts/renderers";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { MarkingProvider } from "../../../../web/src/hooks/useMarking";
import { MarkingStore } from "../../../../web/src/lib/markings";
import { type Answer } from "./option";
import { stackCase } from "./stackCorpus";
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

const settle = () => act(() => new Promise((r) => setTimeout(r, 400)));

// The stack is the sandbox's real answer (x item, y value, colour group); the
// points beside it are the same eight rows, unsummed, carrying `region`.
const c = stackCase("an upright bar of rows");
const points = layer("scatter", 8, {
  item: cat(c.data.item as string[]),
  value: f64(c.data.value as number[]),
  region: cat(["n", "n", "s", "n", "s", "s", "n", "s"]),
});
const BOTH: Answer = answer(c.answer.layers[0]!, points);
const DOC = {
  view: "chart",
  source: "data/a.csv",
  keys: ["region"],
  layer: [
    { mark: { type: "bar", stack: true }, encoding: c.spec.encoding },
    {
      mark: "scatter",
      encoding: {
        x: { field: "item", type: "nominal" },
        y: { field: "value", type: "quantitative" },
        tooltip: { field: "region", type: "nominal" },
      },
    },
  ],
};

function mount(store: MarkingStore, data: Answer = BOTH, doc: unknown = DOC) {
  sdk.useSandboxRun.mockReturnValue({ data: { stdout: JSON.stringify(data), stderr: "", exit_code: 0 }, error: null, isLoading: false, refetch: vi.fn() });
  render(
    <MarkingProvider store={store}>
      <ChartView spec={{ __doc: doc } as never} marking="m" path="/v/a.ai.yaml" type={null} entities={[]} onCreate={() => {}} onPatch={() => {}} />
    </MarkingProvider>,
  );
  return made.charts.at(-1)!;
}

beforeEach(() => {
  made.charts.length = 0;
});
afterEach(() => {
  cleanup();
  for (const ch of made.charts) ch.dispose();
});

describe("the count beside 'by <columns>' (P43)", () => {
  it("counts what went to the marking: a box over every point and segment says 8, not 8 + the segments", async () => {
    const store = new MarkingStore();
    const chart = mount(store);
    await settle();
    act(() => chart.dispatchAction({ type: "brush", areas: [{ brushType: "rect", range: [[0, 600], [0, 400]] }] }));
    await settle();
    expect([...(store.get("m")?.marking.region ?? [])].sort()).toEqual(["n", "s"]);
    expect(screen.queryByText(/selected/)?.textContent).toBe("8 selected · by region");
  });
});

// P44 row 35: rows that give the key no value mark nothing and are not counted
// beside "by item" (review round 22's repro: items [null, null, r01] marked
// {item: [r01]} and said "3 selected · by item").
const SPARSE: Answer = answer(layer("scatter", 3, { x: f64([1, 2, 3]), value: f64([1, 2, 3]), item: cat([null, null, "r01"]) }));
const SPARSE_DOC = {
  view: "chart",
  source: "data/a.csv",
  keys: ["item"],
  mark: "scatter",
  encoding: { x: { field: "x", type: "quantitative" }, y: { field: "value", type: "quantitative" } },
};

describe("rows with no key value (P44 row 35)", () => {
  it("counts only the rows that gave the key a value", async () => {
    const store = new MarkingStore();
    const chart = mount(store, SPARSE, SPARSE_DOC);
    await settle();
    act(() => chart.dispatchAction({ type: "brush", areas: [{ brushType: "rect", range: [[0, 600], [0, 400]] }] }));
    await settle();
    expect([...(store.get("m")?.marking.item ?? [])]).toEqual(["r01"]);
    expect(screen.queryByText(/selected/)?.textContent).toBe("1 selected · by item");
  });
});

// P45 row 45: a selection that writes nothing says "N selected" (P36 row 10),
// whatever the reason it wrote nothing -- here every row it picked gives the
// key no value, so nothing went to the marking.
const EMPTY_KEY: Answer = answer(layer("scatter", 3, { x: f64([1, 2, 3]), value: f64([1, 2, 3]), item: cat([null, null, null]) }));

describe("a brush that writes nothing on a keyed chart (P45 row 45)", () => {
  it("says how many rows it picked, and not by what the marking marks", async () => {
    const store = new MarkingStore();
    const chart = mount(store, EMPTY_KEY, SPARSE_DOC);
    await settle();
    act(() => chart.dispatchAction({ type: "brush", areas: [{ brushType: "rect", range: [[0, 600], [0, 400]] }] }));
    await settle();
    expect(store.get("m")).toBeUndefined();
    expect(screen.queryByText(/selected/)?.textContent).toBe("3 selected");
  });

  it("says so on a marking another view holds, and leaves that marking as it was", async () => {
    const store = new MarkingStore();
    store.set("m", { item: new Set(["r01", "r02"]) }, "/v/table.ai.yaml");
    const chart = mount(store, EMPTY_KEY, SPARSE_DOC);
    await settle();
    act(() => chart.dispatchAction({ type: "brush", areas: [{ brushType: "rect", range: [[0, 600], [0, 400]] }] }));
    await settle();
    expect([...(store.get("m")?.marking.item ?? [])].sort()).toEqual(["r01", "r02"]);
    expect(screen.queryByText(/selected/)?.textContent).toBe("3 selected");
  });

  it("says so after a write of its own it replaced", async () => {
    const store = new MarkingStore();
    const chart = mount(store, SPARSE, SPARSE_DOC);
    await settle();
    act(() => chart.dispatchAction({ type: "brush", areas: [{ brushType: "rect", range: [[0, 600], [0, 400]] }] }));
    await settle();
    expect(screen.queryByText(/selected/)?.textContent).toBe("1 selected · by item");
    // a box over the two rows with no key value: it writes nothing
    const [x1] = chart.convertToPixel({ seriesIndex: 0 }, [1, 1]) as number[];
    const [x2] = chart.convertToPixel({ seriesIndex: 0 }, [2, 2]) as number[];
    act(() => chart.dispatchAction({ type: "brush", areas: [{ brushType: "rect", range: [[x1! - 5, x2! + 5], [0, 400]] }] }));
    await settle();
    expect(screen.queryByText(/selected/)?.textContent).toBe("2 selected");
  });
});
