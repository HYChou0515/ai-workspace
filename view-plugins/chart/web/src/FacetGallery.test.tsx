// @vitest-environment happy-dom
/**
 * #848 P6/P7: a `facet:` spec opens as a gallery. The SDK is a double; what the
 * gallery asks the sandbox for, and what it writes to the marking, is asserted.
 */
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { FacetIndex } from "./gallery";
import { q8 } from "./testAnswer";

type Run = { data?: { stdout: string; stderr: string; exit_code: number }; error: Error | null; isLoading: boolean; refetch: () => void };

const sdk = vi.hoisted(() => ({
  useSandboxRun: vi.fn(),
  viewDocument: vi.fn(),
  registerViewKind: vi.fn(),
  useMarking: vi.fn(),
  isLit: vi.fn((row: Record<string, string>, marking: Record<string, Set<string>>) => {
    let shared = false;
    for (const [c, v] of Object.entries(marking)) {
      if (!(c in row)) continue;
      shared = true;
      if (!v.has(row[c])) return false;
    }
    return shared;
  }),
}));
vi.mock("@aiws/view-sdk", () => sdk);
vi.mock("./echarts", () => ({ createChart: vi.fn() }));
// thumbnail() itself, recorded: which groups were painted dimmed
const thumbnailSpy = vi.hoisted(() => vi.fn());
vi.mock("./gallery", async (original) => {
  const real = await original<typeof import("./gallery")>();
  thumbnailSpy.mockImplementation(real.thumbnail);
  return { ...real, thumbnail: thumbnailSpy };
});

import { ChartView } from "./ChartView";

const DOC = {
  view: "chart",
  source: "data/w.csv",
  marking: "wafers",
  facet: { field: ["lot", "wafer"], sort: { field: "rate", order: "descending" } },
  mark: "grid",
  encoding: {
    x: { field: "x", type: "ordinal" },
    y: { field: "y", type: "ordinal" },
    color: { field: "v", type: "quantitative" },
  },
};

const N = 1000;
const INDEX: FacetIndex = {
  build: "b".repeat(32),
  scale: { kind: "continuous", lo: 0, hi: 254 },
  facet: ["lot", "wafer"],
  // one cell per group (the page answers one code each): groupsPerPage(1) is
  // 200, so 1000 groups are 5 pages, and one screenful is the first of them
  cells: 1,
  layout: { x: [0], y: [0] },
  groups: Array.from({ length: N }, (_, i) => ({ key: ["L1", String(i)], sort: { rate: i } })),
};
const KEY = "k".repeat(64);

const refetch = { build: vi.fn(), index: vi.fn(), page: vi.fn(), exact: vi.fn() };
const ok = (out: unknown) => ({ stdout: JSON.stringify(out), stderr: "", exit_code: 0 });
const fail = (code: number) => ({ stdout: "", stderr: `exit ${code}`, exit_code: code });

let answers: { build?: Run["data"]; index?: Run["data"]; page?: (positions: number[]) => Run["data"] };
const write = vi.fn();
let INDEX_OVER: Partial<FacetIndex> = {};
let exactValues: number[] = [1.5];
const f64b64 = (values: number[]) =>
  btoa(String.fromCharCode(...new Uint8Array(new Float64Array(values).buffer)));

beforeEach(() => {
  vi.clearAllMocks();
  INDEX_OVER = {};
  exactValues = [1.5];
  sdk.viewDocument.mockReturnValue(DOC);
  sdk.useMarking.mockReturnValue([undefined, write]);
  answers = {
    build: ok({ key: KEY, build: INDEX.build, groups: N, cells: INDEX.cells, built: true }),
    index: ok(INDEX),
    page: (positions) => ok({ build: INDEX.build, groups: positions.map(() => q8([10], 0, 254)) }),
  };
  sdk.useSandboxRun.mockImplementation((_plugin: string, cmd: string, args: Record<string, unknown>, opts?: { enabled?: boolean }): Run => {
    const enabled = opts?.enabled ?? true;
    const base = { error: null, isLoading: false };
    if (!enabled) return { ...base, data: undefined, refetch: vi.fn() };
    if (cmd === "facet_build") return { ...base, data: answers.build, refetch: refetch.build };
    if (cmd === "facet_index") return { ...base, data: answers.index, refetch: refetch.index };
    if (cmd === "facet_page") return { ...base, data: answers.page?.(args.positions as number[]), refetch: refetch.page };
    if (cmd === "facet_exact") return { ...base, data: ok({ kind: "f64", data: f64b64(exactValues) }), refetch: refetch.exact };
    return { ...base, data: undefined, refetch: vi.fn() };
  });
});
afterEach(cleanup);

const view = () =>
  render(<ChartView spec={{} as never} type={null} entities={[]} onCreate={() => {}} onPatch={() => {}} path="views/w.ai.yaml" />);

function calls(cmd: string) {
  return sdk.useSandboxRun.mock.calls.filter((c) => c[1] === cmd && (c[3]?.enabled ?? true));
}

describe("FacetGallery", () => {
  it("builds the cache from the spec text, then opens its index by key — never runs query", () => {
    view();
    expect(calls("facet_build")[0][2]).toEqual({ spec: JSON.stringify(DOC) });
    expect(calls("facet_index")[0][2]).toEqual({ key: KEY });
    expect(calls("query")).toHaveLength(0);
    expect(screen.getByText(/1000 groups/)).toBeTruthy();
  });

  it("asks only for the pages near the viewport, positions in sorted order", () => {
    view();
    const pages = calls("facet_page").map((c) => c[2] as { key: string; build: string; positions: number[] });
    expect(pages.length).toBeGreaterThan(0);
    const asked = new Set(pages.map((p) => p.positions[0]));
    expect(asked.size).toBe(1); // the first of the 5 pages, not all of them
    // descending by rate: the first page starts at the highest rate
    expect(pages[0].positions[0]).toBe(N - 1);
    expect(pages.every((p) => p.key === KEY && p.build === INDEX.build)).toBe(true);
  });

  it("re-sorts from the index in hand: no new build", () => {
    view();
    fireEvent.click(screen.getByRole("button", { name: /descending/i }));
    // every render asks with the same arguments, so the run (keyed on them) is
    // the one already done: sorting is not a new build
    const builds = new Set(calls("facet_build").map((c) => JSON.stringify(c[2])));
    expect(builds.size).toBe(1);
    const last = calls("facet_page").at(-1)![2] as { positions: number[] };
    expect(last.positions[0]).toBe(0); // ascending now: lowest rate first
    expect(screen.getByRole("button", { name: /ascending/i })).toBeTruthy();
  });

  it("writes the WHOLE rank range to the marking, loaded groups or not (P7)", () => {
    view();
    fireEvent.change(screen.getByLabelText(/from rank/i), { target: { value: "1" } });
    fireEvent.change(screen.getByLabelText(/to rank/i), { target: { value: "300" } });
    fireEvent.click(screen.getByRole("button", { name: /select ranks/i }));
    const [marking, source] = write.mock.calls.at(-1)!;
    expect(source).toBe("views/w.ai.yaml");
    expect([...(marking as Record<string, Set<string>>).lot]).toEqual(["L1"]);
    const wafers = (marking as Record<string, Set<string>>).wafer;
    expect(wafers.size).toBe(300);
    expect(wafers.has("999")).toBe(true); // rank 1, descending
    expect(wafers.has("700")).toBe(true); // rank 300 — far past the loaded pages
    expect(wafers.has("699")).toBe(false);
  });

  it("clears the marking it wrote", () => {
    view();
    fireEvent.click(screen.getByRole("button", { name: /clear selection/i }));
    expect(write).toHaveBeenLastCalledWith({}, "views/w.ai.yaml");
  });

  it("lights the groups the marking holds and says how many", () => {
    sdk.useMarking.mockReturnValue([{ marking: { wafer: new Set(["5", "6"]) }, source: "x" }, write]);
    view();
    expect(screen.getByText(/2 of 1000 marked/)).toBeTruthy();
  });

  it("rebuilds when the index finds no cache (exit 3), and refetches the index when a page is stale (exit 4)", () => {
    answers.index = fail(3);
    const { unmount } = view();
    act(() => {});
    expect(refetch.build).toHaveBeenCalled();
    unmount();
    answers.index = ok(INDEX);
    answers.page = () => fail(4);
    const second = view();
    act(() => {});
    expect(refetch.index).toHaveBeenCalled();
    second.unmount();
    refetch.build.mockClear();
    answers.page = () => fail(3); // the cache went (a reap) between index and page
    view();
    act(() => {});
    expect(refetch.build).toHaveBeenCalled();
  });

  it("asks for a rebuild ONCE per failed answer, however often it re-renders", () => {
    answers.index = fail(3);
    const { rerender } = view();
    for (let i = 0; i < 5; i++)
      rerender(<ChartView spec={{} as never} type={null} entities={[]} onCreate={() => {}} onPatch={() => {}} path="views/w.ai.yaml" />);
    expect(refetch.build).toHaveBeenCalledTimes(1);
  });

  it("refetches the index once the rebuild answers (same key, so the old exit 3 would stay cached)", () => {
    answers.index = fail(3);
    const { rerender } = view();
    expect(refetch.index).not.toHaveBeenCalled();
    answers.build = ok({ key: KEY, build: "c".repeat(32), groups: N, cells: 1, built: true }); // the rebuild's answer
    rerender(<ChartView spec={{} as never} type={null} entities={[]} onCreate={() => {}} onPatch={() => {}} path="views/w.ai.yaml" />);
    expect(refetch.index).toHaveBeenCalledTimes(1);
  });

  it("asks a failed page's remedy ONCE, however often the gallery re-renders", () => {
    answers.page = () => fail(4);
    const staleAnswer = answers.page([0]);
    answers.page = () => staleAnswer; // one answer object, as the query cache holds it
    const { rerender } = view();
    for (let i = 0; i < 5; i++)
      rerender(<ChartView spec={{} as never} type={null} entities={[]} onCreate={() => {}} onPatch={() => {}} path="views/w.ai.yaml" />);
    expect(refetch.index).toHaveBeenCalledTimes(1);
  });

  it("selects the run of sorted positions between a click and a shift-click (P7)", () => {
    view();
    const tiles = screen.getAllByRole("button", { name: /^group / });
    fireEvent.click(tiles[2]);
    fireEvent.click(tiles[5], { shiftKey: true });
    const wafers = (write.mock.calls.at(-1)![0] as Record<string, Set<string>>).wafer;
    // ranks 3..6 of a descending sort over 1000
    expect([...wafers].sort()).toEqual(["994", "995", "996", "997"]);
    expect(tiles.slice(2, 6).every((t) => t.getAttribute("aria-pressed") === "true")).toBe(true);
  });

  it("forgets its outlines when the marking is cleared elsewhere", () => {
    const { rerender } = view();
    fireEvent.click(screen.getAllByRole("button", { name: /^group / })[0]);
    sdk.useMarking.mockReturnValue([{ marking: { wafer: new Set(["999"]) }, source: "views/w.ai.yaml" }, write]);
    rerender(<ChartView spec={{} as never} type={null} entities={[]} onCreate={() => {}} onPatch={() => {}} path="views/w.ai.yaml" />);
    expect(screen.getAllByRole("button", { name: /^group / })[0].getAttribute("aria-pressed")).toBe("true");
    sdk.useMarking.mockReturnValue([undefined, write]); // another view cleared it
    rerender(<ChartView spec={{} as never} type={null} entities={[]} onCreate={() => {}} onPatch={() => {}} path="views/w.ai.yaml" />);
    expect(screen.getAllByRole("button", { name: /^group / })[0].getAttribute("aria-pressed")).toBe("false");
  });

  it("starts fetching the next page a screen before it scrolls into view", () => {
    view();
    const scroller = document.querySelector("[data-gallery-scroll]") as HTMLElement;
    // 200 groups a page at 7 tiles a row: page 1 starts at row 28 (y 3136);
    // at scrollTop 2400 the view shows rows 21-26 — page 0 only, without lookahead
    Object.defineProperty(scroller, "scrollTop", { value: 2400, configurable: true });
    act(() => {
      fireEvent.scroll(scroller);
    });
    const firsts = new Set(calls("facet_page").map((c) => (c[2] as { positions: number[] }).positions[0]));
    expect(firsts.has(N - 1 - 200)).toBe(true); // page 1's first group, descending
  });

  it("shows the exact value of the cell under the pointer, placed as lattice places it", () => {
    // x written unsorted: the drawn column order is not the written order
    INDEX_OVER.layout = { x: [1, 0], y: [0, 0] };
    INDEX_OVER.cells = 2;
    answers.index = ok({ ...INDEX, ...INDEX_OVER });
    answers.page = (positions) => ok({ build: INDEX.build, groups: positions.map(() => q8([10, 20], 0, 254)) });
    exactValues = [111, 222]; // cache cell 0 is drawn at x=1 (right), cell 1 at x=0 (left)
    view();
    fireEvent.click(screen.getAllByRole("button", { name: /enlarge/i })[0]);
    const canvas = screen.getByRole("dialog").querySelector("canvas") as HTMLCanvasElement;
    canvas.getBoundingClientRect = () => ({ left: 0, top: 0, width: 200, height: 100, right: 200, bottom: 100, x: 0, y: 0, toJSON: () => ({}) });
    fireEvent.mouseMove(canvas, { clientX: 10, clientY: 10 }); // the left column
    expect(screen.getByRole("dialog").textContent).toContain("value: 222");
  });

  it("paints an unlit group's thumbnail dimmed", () => {
    sdk.useMarking.mockReturnValue([{ marking: { wafer: new Set(["999"]) }, source: "x" }, write]);
    view();
    const lits = thumbnailSpy.mock.calls.map((c) => c[3]);
    expect(lits).toContain(true); // wafer 999, rank 1
    expect(lits).toContain(false); // the rest
  });

  it("shows a refused spec's reason and asks for nothing else", () => {
    answers.build = { stdout: "", stderr: "a facet gallery reads a table file", exit_code: 2 };
    view();
    expect(screen.getByRole("alert").textContent).toContain("table file");
    expect(calls("facet_index")).toHaveLength(0);
  });

  it("enlarges one group with its exact values", () => {
    view();
    fireEvent.click(screen.getAllByRole("button", { name: /enlarge/i })[0]);
    const exact = calls("facet_exact").at(-1)![2] as { key: string; build: string; position: number };
    expect(exact).toEqual({ key: KEY, build: INDEX.build, position: N - 1 });
    expect(screen.getByRole("dialog")).toBeTruthy();
  });
});
