/**
 * The `view: chart` panel: spec → sandbox `query` → ECharts, with every state
 * visible. The SDK and the ECharts instance are doubles; what they are handed is
 * what is asserted.
 */
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { answer, cat, f64, layer } from "./testAnswer";

const sdk = vi.hoisted(() => ({
  useSandboxRun: vi.fn(),
  viewDocument: vi.fn(),
  registerViewKind: vi.fn(),
}));
vi.mock("@aiws/view-sdk", () => sdk);

type Handler = (p: unknown) => void;
const chart = vi.hoisted(() => {
  const handlers = new Map<string, Handler>();
  const instance = {
    setOption: vi.fn(),
    dispatchAction: vi.fn(),
    resize: vi.fn(),
    dispose: vi.fn(),
    on: vi.fn((name: string, fn: Handler) => handlers.set(name, fn)),
  };
  return { instance, handlers, createChart: vi.fn(() => instance) };
});
vi.mock("./echarts", () => ({ createChart: chart.createChart }));

import { ChartView } from "./ChartView";

const DOC = {
  view: "chart",
  source: "data/a.csv",
  keys: ["lot"],
  mark: "scatter",
  encoding: {
    x: { field: "a", type: "quantitative" },
    y: { field: "b", type: "quantitative" },
    color: { field: "lot", type: "nominal" },
  },
};

const ANSWER = answer(
  layer(
    "scatter",
    3,
    { a: f64([1, 2, 3]), b: f64([4, 5, 6]), lot: cat(["A", "B", "A"]) },
    { highlight: btoa(String.fromCharCode(0b100)), lit: 1 },
  ),
);

const refetch = vi.fn();
function run(over: Partial<ReturnType<typeof sdk.useSandboxRun>> = {}) {
  sdk.useSandboxRun.mockReturnValue({ data: undefined, error: null, isLoading: false, refetch, ...over });
}
const ok = (a = ANSWER) => ({ stdout: JSON.stringify(a), stderr: "", exit_code: 0 });

// `spec` is opaque to the component: it reads the document through viewDocument.
const view = () => render(<ChartView spec={{} as never} type={null} entities={[]} onCreate={() => {}} onPatch={() => {}} />);

beforeEach(() => {
  vi.clearAllMocks();
  chart.handlers.clear();
  sdk.viewDocument.mockReturnValue(DOC);
});
afterEach(cleanup);

describe("ChartView", () => {
  it("sends the whole document to the sandbox's query, as JSON", () => {
    run({ isLoading: true });
    view();
    expect(sdk.useSandboxRun).toHaveBeenCalledWith("chart", "query", { spec: JSON.stringify(DOC) }, { enabled: true });
    expect(screen.getByRole("status").textContent).toContain("Computing");
  });

  it("shows a spec the schema refuses, and runs nothing", () => {
    sdk.viewDocument.mockReturnValue({ ...DOC, colour: "red" });
    run();
    view();
    expect(sdk.useSandboxRun.mock.calls[0][3]).toEqual({ enabled: false });
    expect(screen.getByRole("alert").textContent).toContain("colour");
    expect(chart.createChart).not.toHaveBeenCalled();
  });

  it("shows what the sandbox refused", () => {
    run({ data: { stdout: "", stderr: "no column 'b' (columns: a)", exit_code: 2 } });
    view();
    expect(screen.getByRole("alert").textContent).toContain("no column 'b'");
  });

  it("shows a call that never ran", () => {
    run({ error: new Error('view plugin "chart" could not run "query": HTTP 502') });
    view();
    expect(screen.getByRole("alert").textContent).toContain("HTTP 502");
  });

  it("draws the answer and lights the highlight with ECharts' own action", () => {
    run({ data: ok() });
    view();
    expect(chart.createChart).toHaveBeenCalledTimes(1);
    const [option, notMerge] = chart.instance.setOption.mock.calls[0];
    expect(notMerge).toBe(true);
    expect((option as { series: unknown[] }).series).toHaveLength(2);
    // Row 2 is lit: lot A → series 0, its second point.
    expect(chart.instance.dispatchAction).toHaveBeenCalledWith({ type: "highlight", seriesIndex: 0, dataIndex: [1] });
  });

  it("says when a scatter was binned", () => {
    run({ data: ok(answer({ ...ANSWER.layers[0], columns: { ...ANSWER.layers[0].columns, $count: f64([1, 1, 1]) }, binned: { points: 30000, bins: 3 } })) });
    view();
    expect(screen.getByText(/30,000 points drawn as 3 bins/)).toBeTruthy();
  });

  it("counts what a brush selected, locally", () => {
    run({ data: ok() });
    view();
    act(() =>
      chart.handlers.get("brushselected")?.({
        areas: [{ brushType: "rect" }],
        batch: [{ selected: [{ seriesIndex: 0, dataIndex: [0, 1] }] }],
      }),
    );
    expect(screen.getByText("2 selected")).toBeTruthy();
    act(() => chart.handlers.get("brushselected")?.({ areas: [], batch: [{ selected: [] }] }));
    expect(screen.queryByText(/selected/)).toBeNull();
  });

  it("counts what a legend click leaves shown", () => {
    run({ data: ok() });
    view();
    act(() => chart.handlers.get("legendselectchanged")?.({ selected: { A: true, B: false } }));
    expect(screen.getByText("2 selected")).toBeTruthy();
  });

  it("reruns the query on Refresh, for data that changed under the same spec", () => {
    run({ data: ok() });
    view();
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    expect(refetch).toHaveBeenCalled();
  });

  it("disposes the chart when the panel goes", () => {
    run({ data: ok() });
    view().unmount();
    expect(chart.instance.dispose).toHaveBeenCalled();
  });

  it("refuses an answer in a format it does not know", () => {
    run({ data: ok({ ...ANSWER, format: 9 as 1 }) });
    view();
    expect(screen.getByRole("alert").textContent).toContain("format 9");
  });
});
