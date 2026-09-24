// @vitest-environment happy-dom
/**
 * #847 PR 3 P2: charts on one named marking link — a brush in one lights the
 * matched rows in the other; a chart on another marking does not react.
 * The SDK double hands out the HOST's real marking hook and matching rule, over
 * a real store; ECharts is a double whose event handlers the test fires.
 */
import { act, cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { MarkingProvider } from "../../../../web/src/hooks/useMarking";
import { MarkingStore } from "../../../../web/src/lib/markings";
import { answer, cat, f64, layer } from "./testAnswer";

const sdk = vi.hoisted(() => ({
  useSandboxRun: vi.fn(),
  viewDocument: vi.fn(),
  registerViewKind: vi.fn(),
}));
vi.mock("@aiws/view-sdk", async () => {
  const hooks = await import("../../../../web/src/hooks/useMarking");
  const lib = await import("../../../../web/src/lib/markings");
  return { ...sdk, useMarking: hooks.useMarking, useMarkingNames: hooks.useMarkingNames, isLit: lib.isLit };
});

type Handler = (p: unknown) => void;
type Instance = { setOption: ReturnType<typeof vi.fn>; handlers: Map<string, Handler> };
const charts = vi.hoisted(() => ({ made: [] as Instance[] }));
vi.mock("./echarts", () => ({
  createChart: vi.fn(() => {
    const handlers = new Map<string, Handler>();
    const inst = {
      handlers,
      setOption: vi.fn(),
      dispatchAction: vi.fn(),
      resize: vi.fn(),
      dispose: vi.fn(),
      on: vi.fn((name: string, fn: Handler) => handlers.set(name, fn)),
    };
    charts.made.push(inst);
    return inst;
  }),
}));

import { ChartView } from "./ChartView";

const enc = {
  x: { field: "a", type: "quantitative" },
  y: { field: "b", type: "quantitative" },
};
const docOn = (marking: string | null, extra: Record<string, unknown> = {}) => ({
  view: "chart",
  source: "data/a.csv",
  keys: ["lot"],
  mark: "scatter",
  encoding: enc,
  ...(marking ? { marking } : {}),
  ...extra,
});
const ANSWER = answer(layer("scatter", 3, { a: f64([1, 2, 3]), b: f64([4, 5, 6]), lot: cat(["L1", "L2", "L1"]) }));
const ok = (a = ANSWER) => ({ data: { stdout: JSON.stringify(a), stderr: "", exit_code: 0 }, error: null, isLoading: false, refetch: vi.fn() });

function mount(store: MarkingStore, docs: Record<string, unknown>[], paths?: string[]) {
  // One doc per view, in mount order: viewDocument answers per call site, so
  // key each ChartView's spec object to its doc.
  const specs = docs.map((d) => ({ __doc: d }));
  sdk.viewDocument.mockImplementation((s: { __doc: unknown }) => s.__doc);
  return render(
    <MarkingProvider store={store}>
      {specs.map((spec, i) => (
        <ChartView
          key={i}
          spec={spec as never}
          path={paths?.[i] ?? `/v/${i}.ai.yaml`}
          type={null}
          entities={[]}
          onCreate={() => {}}
          onPatch={() => {}}
        />
      ))}
    </MarkingProvider>,
  );
}

function brush(inst: Instance, dataIndex: number[]) {
  act(() =>
    inst.handlers.get("brushselected")!({
      batch: [{ areas: [{ brushType: "rect", coordRange: [[0, 9], [0, 9]] }], selected: [{ seriesIndex: 0, dataIndex }] }],
    }),
  );
}

const lastData = (inst: Instance) =>
  (inst.setOption.mock.calls.at(-1)![0] as { series: { data: unknown[] }[] }).series[0]!.data;
const dim = (value: unknown) => ({ value, itemStyle: { opacity: 0.15 } });

beforeEach(() => {
  charts.made.length = 0;
  sdk.useSandboxRun.mockReturnValue(ok());
});
afterEach(cleanup);

describe("ChartView on a named marking", () => {
  it("a brush in one chart writes the marking, and the other chart lights the matched rows", () => {
    const store = new MarkingStore();
    mount(store, [docOn("fail"), docOn("fail")], ["/v/grid.ai.yaml", "/v/scatter.ai.yaml"]);
    const [a, b] = charts.made;
    brush(a!, [1]); // row 1 is lot L2
    expect([...store.get("fail")!.marking.lot!]).toEqual(["L2"]);
    expect(store.get("fail")!.source).toBe("/v/grid.ai.yaml");
    expect(lastData(b!)).toEqual([dim([1, 4]), [2, 5], dim([3, 6])]);
  });

  it("a chart on another marking, or none, does not react — not even a re-render", () => {
    const store = new MarkingStore();
    mount(store, [docOn("fail"), docOn("other"), docOn(null)]);
    const [a, other, none] = charts.made;
    const before = [other!.setOption.mock.calls.length, none!.setOption.mock.calls.length];
    brush(a!, [0]);
    expect([other!.setOption.mock.calls.length, none!.setOption.mock.calls.length]).toEqual(before);
    expect(lastData(other!)).toEqual([[1, 4], [2, 5], [3, 6]]);
  });

  it("the spec's highlight seeds an empty marking on open", () => {
    const store = new MarkingStore();
    const lit = answer(
      layer("scatter", 3, { a: f64([1, 2, 3]), b: f64([4, 5, 6]), lot: cat(["L1", "L2", "L1"]) }, {
        highlight: btoa(String.fromCharCode(0b010)),
        lit: 1,
      }),
    );
    sdk.useSandboxRun.mockReturnValue(ok(lit));
    mount(store, [docOn("fail")]);
    expect([...store.get("fail")!.marking.lot!]).toEqual(["L2"]);
  });

  it("the highlight does not overwrite a marking another view already holds", () => {
    const store = new MarkingStore();
    store.set("fail", { lot: new Set(["L1"]) }, "/v/other.ai.yaml");
    const lit = answer(
      layer("scatter", 3, { a: f64([1, 2, 3]), b: f64([4, 5, 6]), lot: cat(["L1", "L2", "L1"]) }, {
        highlight: btoa(String.fromCharCode(0b010)),
        lit: 1,
      }),
    );
    sdk.useSandboxRun.mockReturnValue(ok(lit));
    mount(store, [docOn("fail")]);
    expect([...store.get("fail")!.marking.lot!]).toEqual(["L1"]);
  });

  it("a chart without keys can be lit but never writes", () => {
    const store = new MarkingStore();
    mount(store, [docOn("fail", { keys: undefined })]);
    brush(charts.made[0]!, [0]);
    expect(store.get("fail")).toBeUndefined();
    act(() => store.set("fail", { lot: new Set(["L2"]) }, null));
    expect(lastData(charts.made[0]!)).toEqual([dim([1, 4]), [2, 5], dim([3, 6])]);
  });

  it("the brush component's own empty event does not clear the marking", () => {
    // ECharts fires `brushselected` with no areas when a brush component is
    // (re)built — which every setOption does. Taken as "cleared", a write would
    // re-render, rebuild the brush, fire again: the marking would never stick.
    const store = new MarkingStore();
    mount(store, [docOn("fail")]);
    const [a] = charts.made;
    brush(a!, [0]);
    act(() => a!.handlers.get("brushselected")!({ batch: [{}] }));
    expect(store.get("fail")).toBeDefined();
  });

  it("an empty brush event in a view the person never brushed does not clear another view's marking", () => {
    const store = new MarkingStore();
    mount(store, [docOn("fail"), docOn("fail")]);
    const [a, b] = charts.made;
    brush(a!, [0]);
    act(() => b!.handlers.get("brushselected")!({ batch: [{ areas: [], selected: [] }] }));
    expect(store.get("fail")).toBeDefined();
  });

  it("follows the header's choice over the file: detached, it writes nothing", () => {
    const store = new MarkingStore();
    sdk.viewDocument.mockImplementation((s: { __doc: unknown }) => s.__doc);
    render(
      <MarkingProvider store={store}>
        <ChartView
          spec={{ __doc: docOn("fail") } as never}
          marking={null}
          path="/v/a.ai.yaml"
          type={null}
          entities={[]}
          onCreate={() => {}}
          onPatch={() => {}}
        />
      </MarkingProvider>,
    );
    brush(charts.made[0]!, [0]);
    expect(store.names()).toEqual([]);
  });

  it("clearing the brush the person drew clears the marking", () => {
    const store = new MarkingStore();
    mount(store, [docOn("fail")]);
    const [a] = charts.made;
    brush(a!, [0]);
    // The redraw with the new lit rows replaces the series only, so the brush
    // the person drew stays on the chart — and can be cleared.
    expect(a!.setOption.mock.calls.at(-1)![1]).toEqual({ replaceMerge: ["series"] });
    act(() => a!.handlers.get("brushselected")!({ batch: [{ areas: [], selected: [] }] }));
    expect(store.get("fail")).toBeUndefined();
  });
});
