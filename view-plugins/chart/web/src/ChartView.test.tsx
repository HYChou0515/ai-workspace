// @vitest-environment happy-dom
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
  // Markings are exercised in ChartView.marking.test.tsx; here, no marking.
  useMarking: vi.fn(() => [undefined, vi.fn()]),
  isLit: vi.fn(() => false),
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
    { highlight: btoa(String.fromCharCode(0b110)), lit: 2 },
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

  // #847/#848 P9: the args travel as ONE argv string, capped at 128 KiB by the
  // kernel; a spec over that passed show_file (which sends only its path) and
  // got a 413 on every render. A view in a file names the file, as show_file does.
  describe("a view in a file names the file, whatever its size", () => {
    const ARGV_CAP = 128 * 1024;
    // a legitimate spec over the cap: a long filter
    const BIG = { ...DOC, transform: [{ filter: { field: "lot", oneOf: Array.from({ length: 30_000 }, (_, i) => `L${i}`) } }] };
    const inFile = () =>
      render(<ChartView spec={{} as never} type={null} entities={[]} onCreate={() => {}} onPatch={() => {}} path="views/c.ai.yaml" />);
    const queryArgs = () => sdk.useSandboxRun.mock.calls.filter((c) => c[1] === "query").at(-1)![2] as Record<string, unknown>;

    it("sends the path, and a call the same size for a spec over the argv cap", () => {
      expect(JSON.stringify(BIG).length).toBeGreaterThan(ARGV_CAP);
      sdk.viewDocument.mockReturnValue(BIG);
      run({ isLoading: true });
      inFile();
      const args = queryArgs();
      expect(Object.keys(args).sort()).toEqual(["path", "rev"]);
      expect(args.path).toBe("views/c.ai.yaml");
      expect(JSON.stringify({ args }).length).toBeLessThan(200);
    });

    it("makes an edited file a new call, and the same text the same call", () => {
      run({ isLoading: true });
      inFile();
      const first = queryArgs();
      cleanup();
      inFile();
      expect(queryArgs()).toEqual(first);
      cleanup();
      sdk.viewDocument.mockReturnValue({ ...DOC, title: "edited" });
      inFile();
      expect(queryArgs().rev).not.toEqual(first.rev);
    });
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

  it("draws the answer with the highlight already in it, dispatching nothing", () => {
    run({ data: ok() });
    view();
    expect(chart.createChart).toHaveBeenCalledTimes(1);
    const [option, notMerge] = chart.instance.setOption.mock.calls[0];
    expect(notMerge).toBe(true);
    expect((option as { series: unknown[] }).series).toHaveLength(2);
    // Rows 1 and 2 are lit (lot B's first point, lot A's second); row 0 is
    // not, so it is dimmed in the data. No hover-state action: a mouse passing
    // over the chart would clear it.
    const series = (option as { series: { data: unknown[] }[] }).series;
    expect(series[0].data[0]).toMatchObject({ itemStyle: { opacity: 0.15 } });
    expect(Array.isArray(series[0].data[1])).toBe(true);
    expect(chart.instance.dispatchAction).not.toHaveBeenCalled();
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
        batch: [{ areas: [{ brushType: "rect" }], selected: [{ seriesIndex: 0, dataIndex: [0, 1] }] }],
      }),
    );
    expect(screen.getByText("2 selected")).toBeTruthy();
    act(() => chart.handlers.get("brushselected")?.({ batch: [{ areas: [], selected: [] }] }));
    expect(screen.queryByText(/selected/)).toBeNull();
  });

  it("keeps the chart where it is when a selection is counted (#847/#848 P18)", () => {
    // the count's line was added only once something was selected, which
    // pushed the chart down under the pointer mid-gesture
    run({ data: ok() });
    view();
    const host = () => (chart.createChart.mock.calls[0] as unknown[])[0] as HTMLElement;
    const before = host().previousElementSibling as HTMLElement | null;
    expect(before).not.toBeNull();
    expect(before!.style.height).toBe("20px");
    act(() =>
      chart.handlers.get("brushselected")?.({
        batch: [{ areas: [{ brushType: "rect" }], selected: [{ seriesIndex: 0, dataIndex: [0, 1] }] }],
      }),
    );
    expect(host().previousElementSibling).toBe(before);
    expect(before!.style.height).toBe("20px");
    expect(before!.textContent).toBe("2 selected");
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

  // #847/#848 PR 5 P13 — in a layout pane the chart takes the height the pane
  // gives it. A fixed 360 px made every pane shorter than that scroll. The DOM
  // test can only hold the styles; the pane sizes are measured in a browser.
  it("grows to the height its pane gives it, with no fixed height of its own", () => {
    run({ data: ok() });
    const { container } = view();
    const host = (chart.createChart.mock.calls[0] as unknown as [HTMLElement])[0];
    const chain: HTMLElement[] = [];
    for (let el: HTMLElement | null = host; el && el !== container; el = el.parentElement) chain.push(el);
    for (const el of chain) {
      expect(el.style.minHeight === "" || parseInt(el.style.minHeight, 10) <= 160).toBe(true);
      expect(el.style.height).not.toMatch(/px$/);
    }
    // every box from the panel down to the chart grows into the free height
    for (const el of chain) expect(el.style.flexGrow || el.style.flex.split(" ")[0]).toBe("1");
  });
});
