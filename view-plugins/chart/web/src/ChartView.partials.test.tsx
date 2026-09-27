// @vitest-environment happy-dom
/**
 * #861 D3: a chart with an aggregated bar asks the sandbox to split its bars
 * by the marking's keys (`partials`) -- once per key SET: new picks on the
 * same keys are folded in the browser, never asked for -- and draws each bar
 * the marking picks part of whole, dimmed, with a lit bar of the picked
 * value in front, where the chart laid the bar out.
 * The marking is the host's real store; the sandbox and ECharts are doubles.
 */
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { MarkingProvider } from "../../../../web/src/hooks/useMarking";
import { markingFrom, MarkingStore } from "../../../../web/src/lib/markings";
import type { LayerPartials, Partial } from "./partials";
import { answer, cat, f64, layer } from "./testAnswer";

const sdk = vi.hoisted(() => ({
  useSandboxRun: vi.fn(),
  viewDocument: vi.fn((s: { __doc: unknown }) => s.__doc),
  registerViewKind: vi.fn(),
}));
vi.mock("@aiws/view-sdk", async () => {
  const hooks = await import("../../../../web/src/hooks/useMarking");
  const lib = await import("../../../../web/src/lib/markings");
  return { ...sdk, useMarking: hooks.useMarking, useMarkingNames: hooks.useMarkingNames, ...lib };
});

const chart = vi.hoisted(() => ({
  setOption: vi.fn(),
  dispatchAction: vi.fn(),
  resize: vi.fn(),
  dispose: vi.fn(),
  on: vi.fn(),
  getZr: () => ({ on: vi.fn() }),
  // where the (double) chart laid bar j out: 100 px apart, 60 wide, 90 tall
  getModel: () => ({
    getSeriesByIndex: () => ({
      getData: () => ({
        getItemLayout: (j: number) => ({ x: 10 + 100 * j, y: 300, width: 60, height: -90 }),
        getItemVisual: () => ({ fill: "#123456" }),
      }),
    }),
  }),
}));
vi.mock("./echarts", () => ({ createChart: vi.fn(() => chart) }));

import { ChartView } from "./ChartView";

// a count of items per group: g1 counts 3 -- (g1, 1) twice, (g1, 2) once --
// and g2 counts 1, (g2, 1)
const ANSWER = answer(layer("bar", 2, { group: cat(["g1", "g2"]), item: f64([3, 1]) }, { measured: ["item"] }));
const DOC = {
  view: "chart",
  source: "data/a.csv",
  keys: ["group", "item"],
  mark: "bar",
  encoding: { x: { field: "group", type: "nominal" }, y: { field: "item", type: "quantitative", aggregate: "count" } },
};
const SPLIT: LayerPartials = {
  by: ["group", "item"],
  op: "count",
  keys: [
    ["g1", "1"],
    ["g1", "2"],
    ["g2", "1"],
  ],
  bars: [
    [
      [0, 2, 0, null, null],
      [1, 1, 0, null, null],
    ],
    [[2, 1, 0, null, null]],
  ] as Partial[][],
};
let partialsFor: (by: string[]) => LayerPartials = () => SPLIT;

const ran = (stdout: unknown) => ({ data: { stdout: JSON.stringify(stdout), stderr: "", exit_code: 0 }, error: null, isLoading: false, refetch: vi.fn() });

beforeEach(() => {
  vi.clearAllMocks();
  partialsFor = () => SPLIT;
  sdk.useSandboxRun.mockImplementation((_p: string, cmd: string, args: { by?: string[] }) =>
    cmd === "partials" ? ran({ format: 1, layers: [partialsFor(args.by ?? [])] }) : ran(ANSWER),
  );
});
afterEach(cleanup);

function mount(store: MarkingStore) {
  render(
    <MarkingProvider store={store}>
      <ChartView spec={{ __doc: DOC } as never} marking="m" path="/v/a.ai.yaml" type={null} entities={[]} onCreate={() => {}} onPatch={() => {}} />
    </MarkingProvider>,
  );
}

/** The distinct `partials` calls the view made enabled: what it asked. */
const asked = () =>
  [
    ...new Set(
      sdk.useSandboxRun.mock.calls
        .filter((c) => c[1] === "partials" && (c[3]?.enabled ?? true))
        .map((c) => JSON.stringify((c[2] as { by: string[] }).by)),
    ),
  ].map((t) => JSON.parse(t) as string[]);

/** Per series of the last drawing, the value each bar is drawn to (y). */
const drawnTo = () =>
  (chart.setOption.mock.calls.at(-1)![0] as { series: { data: unknown[] }[] }).series.map((s) =>
    s.data.map((d) => ((Array.isArray(d) ? d : (d as { value: number[] }).value) as number[])[1]),
  );

describe("a bar the marking picks part of (#861 D3)", () => {
  it("asks nothing while the marking holds nothing: the bar is drawn whole", () => {
    mount(new MarkingStore());
    expect(asked()).toEqual([]);
    expect(drawnTo()).toEqual([[3, 1]]);
  });

  it("asks once per key set, and folds each new set of picks itself", () => {
    const store = new MarkingStore();
    mount(store);
    act(() => void store.set("m", markingFrom(["group", "item"], [["g1", "2"]]), "/v/table.ai.yaml"));
    expect(asked()).toEqual([["group", "item"]]);
    // the bars whole (dimmed); g1: 1 of its 3 picked, lit in front. g2: none.
    expect(drawnTo()).toEqual([
      [3, 1],
      [1, null],
    ]);
    // new picks on the same keys: drawn anew, not asked for
    act(() => void store.set("m", markingFrom(["group", "item"], [["g1", "1"], ["g2", "1"]]), "/v/table.ai.yaml"));
    expect(asked()).toEqual([["group", "item"]]);
    expect(drawnTo()).toEqual([
      [3, 1],
      [2, 1],
    ]);
  });

  it("draws the lit bar where the drawn chart laid its bar out, a third of its width", () => {
    const store = new MarkingStore();
    mount(store);
    act(() => void store.set("m", markingFrom(["group", "item"], [["g1", "2"]]), "/v/table.ai.yaml"));
    const series = (chart.setOption.mock.calls.at(-1)![0] as { series: { type: string; renderItem?: Function }[] }).series;
    const lit = series.find((s) => s.type === "custom")!;
    // an axis of 30 px per unit, upward
    const api = { value: () => 0, coord: ([x, y]: number[]) => [x!, 300 - 30 * y!] };
    expect(lit.renderItem!({ dataIndex: 0 }, api)).toEqual({
      type: "rect",
      shape: { x: 10, y: 300, width: 20, height: -30 },
      style: { fill: "#123456", opacity: 1 },
    });
  });

  it("asks again when the keys change, and draws whole what it cannot split", () => {
    const store = new MarkingStore();
    partialsFor = (by) => (by.length === 1 ? null : SPLIT);
    mount(store);
    act(() => void store.set("m", markingFrom(["group", "item"], [["g1", "2"]]), "/v/table.ai.yaml"));
    act(() => void store.set("m", markingFrom(["group"], [["g1"]]), "/v/other.ai.yaml"));
    expect(asked()).toEqual([["group", "item"], ["group"]]);
    // by group alone each bar is one pick or none: lit whole (g2 dimmed)
    const last = (chart.setOption.mock.calls.at(-1)![0] as { series: { data: unknown[] }[] }).series;
    expect(last).toHaveLength(1);
    expect(last[0]!.data[1]).toMatchObject({ itemStyle: { opacity: 0.15 } });
  });

  it("says why a bar the sandbox would not split is lit whole", () => {
    const store = new MarkingStore();
    partialsFor = () => ({ whole: "lit whole: split by the marking it would be over 200,000 parts" });
    mount(store);
    act(() => void store.set("m", markingFrom(["group", "item"], [["g1", "2"]]), "/v/table.ai.yaml"));
    expect(screen.getByText("lit whole: split by the marking it would be over 200,000 parts")).toBeTruthy();
    expect(drawnTo()).toEqual([[3, 1]]);
  });
});
