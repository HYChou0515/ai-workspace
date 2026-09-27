// @vitest-environment happy-dom
/**
 * #847/#848 P18: a lasso on a grid that is on NO marking showed only a count --
 * the grid is one raster image, which ECharts' own brush styling cannot dim,
 * so nothing on the chart said which cells were taken. They are now painted
 * lit and the rest dimmed, as a marking would. What the raster is painted
 * with is observed at `paintCells`, the one call that paints it.
 */
import { act, cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { answer, cat, f64, layer, q8 } from "./testAnswer";

const sdk = vi.hoisted(() => ({
  useSandboxRun: vi.fn(),
  viewDocument: vi.fn(),
  registerViewKind: vi.fn(),
  // a store that takes every write, as the host's does (P40 row 19: a write
  // no store took is the chart's own)
  useMarking: vi.fn(() => [undefined, vi.fn(() => true)]),
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
    // the canvas's own clicks (a click on empty space, P30): not driven here
    getZr: () => ({ on: vi.fn() }),
  };
  return { instance, handlers, createChart: vi.fn(() => instance) };
});
vi.mock("./echarts", () => ({ createChart: chart.createChart }));

const painted = vi.hoisted(() => ({ lits: [] as (boolean[] | undefined)[] }));
vi.mock("./raster", async (original) => {
  const real = await original<typeof import("./raster")>();
  return {
    ...real,
    paintCells: (...args: Parameters<typeof real.paintCells>) => {
      painted.lits.push(args[2] ? [...args[2]] : undefined);
      return real.paintCells(...args);
    },
  };
});

import { ChartView } from "./ChartView";

const GRID = {
  view: "chart",
  source: "data/a.csv",
  keys: ["lot"],
  mark: "grid",
  encoding: {
    x: { field: "x", type: "ordinal" },
    y: { field: "y", type: "ordinal" },
    color: { field: "v", type: "quantitative" },
  },
};
const ANSWER = answer(layer("grid", 3, { x: f64([0, 1, 2]), y: f64([0, 0, 0]), v: q8([0, 127, 254], 0, 1), lot: cat(["L1", "L2", "L3"]) }));

beforeEach(() => {
  vi.clearAllMocks();
  chart.handlers.clear();
  painted.lits.length = 0;
  sdk.viewDocument.mockReturnValue(GRID);
  sdk.useSandboxRun.mockReturnValue({ data: { stdout: JSON.stringify(ANSWER), stderr: "", exit_code: 0 }, error: null, isLoading: false, refetch: vi.fn() });
});
afterEach(cleanup);

const view = () =>
  render(<ChartView spec={{} as never} path="views/g.ai.yaml" type={null} entities={[]} onCreate={() => {}} onPatch={() => {}} />);

const lasso = (outline: number[][]) =>
  act(() => chart.handlers.get("brushselected")!({ batch: [{ areas: [{ brushType: "polygon", coordRange: outline }], selected: [] }] }));

describe("a grid's own selection, on no marking", () => {
  it("paints the cells a lasso took lit and the rest dimmed", () => {
    view();
    expect(painted.lits.at(-1)).toBeUndefined(); // nothing lit yet: drawn plain
    lasso([[0.6, -0.4], [2.9, -0.4], [2.9, 0.4], [0.6, 0.4]]); // cells 1 and 2
    expect(painted.lits.at(-1)).toEqual([false, true, true]);
  });

  it("paints it plain again once the selection is cleared", () => {
    view();
    lasso([[0.6, -0.4], [2.9, -0.4], [2.9, 0.4], [0.6, 0.4]]);
    act(() => chart.handlers.get("brushselected")!({ batch: [{ areas: [], selected: [] }] }));
    expect(painted.lits.at(-1)).toBeUndefined();
  });

  // #847/#848 PR 5 P34 row 5: a grid with no `keys:` on a marking writes
  // nothing, so what its lasso took is lit by the grid itself, as with no
  // marking -- it showed a count over a plain raster.
  it("on a marking it cannot write, paints the cells a lasso took lit (P34)", () => {
    const { keys: _, ...keyless } = GRID;
    sdk.viewDocument.mockReturnValue(keyless);
    render(<ChartView spec={{} as never} marking="m" path="views/g.ai.yaml" type={null} entities={[]} onCreate={() => {}} onPatch={() => {}} />);
    lasso([[0.6, -0.4], [2.9, -0.4], [2.9, 0.4], [0.6, 0.4]]); // cells 1 and 2
    expect(painted.lits.at(-1)).toEqual([false, true, true]);
  });

  it("(control) on a marking it writes, the lasso's cells are left to the marking (P34)", () => {
    // the double's marking holds nothing: every view on it draws undimmed
    render(<ChartView spec={{} as never} marking="m" path="views/g.ai.yaml" type={null} entities={[]} onCreate={() => {}} onPatch={() => {}} />);
    lasso([[0.6, -0.4], [2.9, -0.4], [2.9, 0.4], [0.6, 0.4]]);
    expect(painted.lits.at(-1)).toBeUndefined();
  });

  it("re-reports of the same selection draw nothing new", () => {
    // ECharts re-reports the areas it holds; the same cells are the same draw
    view();
    const outline = [[0.6, -0.4], [2.9, -0.4], [2.9, 0.4], [0.6, 0.4]];
    lasso(outline);
    const draws = chart.instance.setOption.mock.calls.length;
    lasso(outline);
    expect(chart.instance.setOption.mock.calls.length).toBe(draws);
  });
});
