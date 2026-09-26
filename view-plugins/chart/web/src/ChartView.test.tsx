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
    // the canvas's own clicks (a click on empty space, P30): not driven here
    getZr: () => ({ on: vi.fn() }),
  };
  return { instance, handlers, createChart: vi.fn(() => instance) };
});
vi.mock("./echarts", () => ({ createChart: chart.createChart }));

import { ChartView, gridCanvas } from "./ChartView";
import { lattice, upscale } from "./raster";

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

  it("ends a count too long for a narrow chart in an ellipsis, the whole line on hover (P27)", () => {
    // at 390 wide a 155 px panel cut "16 selected · by lot, wafer" to
    // "16 selected · by lot, wa" with nothing to say it was cut
    run({ data: ok() });
    view();
    act(() =>
      chart.handlers.get("brushselected")?.({
        batch: [{ areas: [{ brushType: "rect" }], selected: [{ seriesIndex: 0, dataIndex: [0, 1] }] }],
      }),
    );
    const count = screen.getByText("2 selected");
    // (one line: its line is `white-space: nowrap`, which it inherits)
    const { overflow, textOverflow, minWidth } = count.style;
    expect({ overflow, textOverflow, minWidth }).toEqual({ overflow: "hidden", textOverflow: "ellipsis", minWidth: "0" });
    expect(count.parentElement!.style.whiteSpace).toBe("nowrap");
    expect(count.parentElement!.title).toBe("2 selected");
  });

  it("ends a note too long for a narrow chart in an ellipsis, the whole line on hover (P42 row 29)", () => {
    // at 390 wide the stack's note needed 338 px of a 334 px line: its field
    // names were cut with nothing to say so
    const stacked = {
      ...DOC,
      mark: undefined,
      encoding: undefined,
      layer: [
        { mark: { type: "bar", stack: true }, encoding: { x: { field: "a", type: "nominal" }, y: { field: "b", type: "quantitative" } } },
        { mark: "scatter", encoding: DOC.encoding },
      ],
    };
    sdk.viewDocument.mockReturnValue(stacked);
    const two = answer(layer("bar", 3, { a: cat(["1", "2", "3"]), b: f64([4, 5, 6]) }, { measured: ["b"] }), ANSWER.layers[0]!);
    run({ data: ok(two) });
    view();
    const note = screen.getByText("the stack links by a only (each a sum)");
    const { overflow, textOverflow } = note.style;
    expect({ overflow, textOverflow }).toEqual({ overflow: "hidden", textOverflow: "ellipsis" });
    expect(note.parentElement!.title).toBe("the stack links by a only (each a sum)");
  });

  it("puts the count first and never cuts it for a note: the note yields (P44 row 36)", () => {
    // P43's demo at 390 wide: the note and the count shared the line and the
    // count was cut ("6 selected · …"), hiding "by item"
    const stacked = {
      ...DOC,
      mark: undefined,
      encoding: undefined,
      layer: [
        { mark: { type: "bar", stack: true }, encoding: { x: { field: "a", type: "nominal" }, y: { field: "b", type: "quantitative" } } },
        { mark: "scatter", encoding: DOC.encoding },
      ],
    };
    sdk.viewDocument.mockReturnValue(stacked);
    const two = answer(layer("bar", 3, { a: cat(["1", "2", "3"]), b: f64([4, 5, 6]) }, { measured: ["b"] }), ANSWER.layers[0]!);
    run({ data: ok(two) });
    view();
    act(() =>
      chart.handlers.get("brushselected")?.({
        batch: [{ areas: [{ brushType: "rect" }], selected: [{ seriesIndex: 1, dataIndex: [0, 1] }] }],
      }),
    );
    const count = screen.getByText("2 selected");
    const note = screen.getByText("the stack links by a only (each a sum)");
    expect(count.parentElement).toBe(note.parentElement);
    expect(count.nextElementSibling).toBe(note);
    // the count keeps its width while a note is beside it; only a line too
    // narrow for the count and each note's ellipsis ends it in one (P27, P45
    // row 42: below)
    expect(count.style.flexShrink).toBe("0");
    // the note yields, its whole text on hover
    expect({ flexShrink: note.style.flexShrink || "1", textOverflow: note.style.textOverflow }).toEqual({ flexShrink: "1", textOverflow: "ellipsis" });
    expect(note.title).toBe("the stack links by a only (each a sum)");
    // the line's title reads in the line's order
    expect(count.parentElement!.title).toBe("2 selected · the stack links by a only (each a sum)");
  });

  // P45 row 42: beside a count wider than its line ("128 selected · by group,
  // item, region, value, kind" in a 250 px pane) the note shrank to 0 px, so a
  // warning -- a log axis's "values at or below 0 not drawn" -- vanished. A note
  // never vanishes: it keeps the width of its ellipsis, and the count's cap
  // leaves it that and the gap. (happy-dom lays nothing out: the widths are
  // read in Chromium in the demo; here, the styles that make them.)
  it("keeps every note at least its ellipsis wide beside a count wider than the line (P45 row 42)", () => {
    // two notes: a scatter's value of 0 on a log y, and a rule's value with no place
    const logged = {
      view: "chart",
      source: "data/a.csv",
      layer: [
        { mark: "scatter", encoding: { x: { field: "a", type: "quantitative" }, y: { field: "b", type: "quantitative", scale: { type: "log" } } } },
        { mark: "rule", encoding: { y: { field: "b", type: "quantitative" } } },
      ],
    };
    sdk.viewDocument.mockReturnValue(logged);
    run({ data: ok(answer(layer("scatter", 3, { a: f64([1, 2, 3]), b: f64([1, 0, 4]) }), layer("rule", 2, { b: f64([1, 0]) }))) });
    view();
    act(() =>
      chart.handlers.get("brushselected")?.({
        batch: [{ areas: [{ brushType: "rect" }], selected: [{ seriesIndex: 0, dataIndex: [0, 1] }] }],
      }),
    );
    const count = screen.getByText("2 selected");
    const line = count.parentElement!;
    const notes = [...line.children].slice(1) as HTMLElement[];
    expect(notes.map((n) => n.textContent)).toEqual([
      "1 rule value with no place on the y axis — not drawn",
      "1 value at or below 0 not drawn on the log y axis",
    ]);
    expect(line.style.gap).toBe("12px");
    // each note: never narrower than its ellipsis
    for (const n of notes) expect({ minWidth: n.style.minWidth, textOverflow: n.style.textOverflow }).toEqual({ minWidth: "1.5em", textOverflow: "ellipsis" });
    // the count: capped at the line less each note's least width and its gap
    expect(count.style.maxWidth).toBe("calc(100% - 2 * (1.5em + 12px))");
    expect({ overflow: count.style.overflow, textOverflow: count.style.textOverflow, minWidth: count.style.minWidth }).toEqual({
      overflow: "hidden",
      textOverflow: "ellipsis",
      minWidth: "0",
    });
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

describe("the chart's canvas (#847/#848 PR 5 P29)", () => {
  it("is composited nearest-neighbour: at a fractional pixel ratio the browser scales it by a fraction of a pixel", () => {
    // measured in Chromium at 1.25 and 1.5: smoothed, that left one blended
    // pixel at every cell edge of a grid drawn 1:1 inside it
    run({ data: ok() });
    view();
    const drawnIn = (chart.createChart.mock.calls as unknown as [HTMLElement][])[0][0];
    expect(drawnIn.style.imageRendering).toBe("pixelated");
  });
});

describe("gridCanvas (#847/#848 PR 5 P29)", () => {
  // happy-dom draws nothing: record what the canvas is handed instead
  const put = vi.fn();
  beforeEach(() => {
    put.mockClear();
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({ putImageData: put } as never);
    vi.stubGlobal(
      "ImageData",
      class {
        constructor(
          readonly data: Uint8ClampedArray,
          readonly width: number,
          readonly height: number,
        ) {}
      },
    );
  });
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  const cells = lattice([0, 1, 2], [0, 0, 0], [0, 1, 2]);
  const data = new Uint8ClampedArray(12);
  for (let i = 0; i < 3; i++) data.set([i * 80, 0, 0, 255], i * 4);
  const image = { width: 3, height: 1, data };

  it("is the size it is asked for, each cell whole pixels of its colour", () => {
    const canvas = gridCanvas({ cells, image }, { width: 7, height: 2 });
    expect([canvas.width, canvas.height]).toEqual([7, 2]);
    expect(put).toHaveBeenCalledTimes(1);
    const [drawn, x, y] = put.mock.calls[0] as [ImageData, number, number];
    expect([drawn.width, drawn.height, x, y]).toEqual([7, 2, 0, 0]);
    expect(Array.from(drawn.data)).toEqual(Array.from(upscale(image, 7, 2).data));
  });

  it("paints once per image and size: a redraw at the same size reuses it", () => {
    const a = gridCanvas({ cells, image }, { width: 9, height: 3 });
    expect(gridCanvas({ cells, image }, { width: 9, height: 3 })).toBe(a);
    expect(put).toHaveBeenCalledTimes(1);
    const b = gridCanvas({ cells, image }, { width: 10, height: 3 });
    expect(b).not.toBe(a);
    expect([b.width, b.height]).toEqual([10, 3]);
    const c = gridCanvas({ cells, image }, { width: 10, height: 4 });
    expect(c).not.toBe(b);
    expect([c.width, c.height]).toEqual([10, 4]);
  });
});
