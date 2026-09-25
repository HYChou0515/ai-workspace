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

const ok = (out: unknown) => ({ stdout: JSON.stringify(out), stderr: "", exit_code: 0 });
const fail = (code: number) => ({ stdout: "", stderr: `exit ${code}`, exit_code: code });

let answers: { build?: Run["data"]; index?: Run["data"]; page?: (positions: number[]) => Run["data"] };
const write = vi.fn();
let INDEX_OVER: Partial<FacetIndex> = {};
let failUntil = { build: 0, index: 0, page: 0, exact: 0 };
let failCode = { build: 3, index: 3, page: 3, exact: 3 };
/** The epoch whose build is still loading (no answer yet); -1 for none. */
let loadingBuildAt = -1;
const answerCache = new Map<string, Run["data"]>();
const epochsSeen: number[] = [];
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
  failUntil = { build: 0, index: 0, page: 0, exact: 0 };
  failCode = { build: 3, index: 3, page: 3, exact: 3 };
  loadingBuildAt = -1;
  answerCache.clear();
  epochsSeen.length = 0;
  sdk.useSandboxRun.mockImplementation((_plugin: string, cmd: string, args: Record<string, unknown>, opts?: { enabled?: boolean }): Run => {
    const enabled = opts?.enabled ?? true;
    const base = { error: null, isLoading: false, refetch: vi.fn() };
    if (!enabled) return { ...base, data: undefined };
    const epoch = typeof args.epoch === "number" ? args.epoch : 0;
    epochsSeen.push(epoch);
    // the real hook hands back ONE data object per (command, args) -- as the
    // query cache does -- so a double that made a new one each render would
    // hide an effect keyed on it
    if (cmd === "facet_build" && epoch === loadingBuildAt) return { ...base, data: undefined };
    const cacheKey = `${cmd} ${JSON.stringify(args)}`;
    if (!answerCache.has(cacheKey)) {
      let data: Run["data"];
      if (cmd === "facet_build") data = epoch < failUntil.build ? fail(failCode.build) : answers.build;
      else if (cmd === "facet_index") data = epoch < failUntil.index ? fail(failCode.index) : answers.index;
      else if (cmd === "facet_page")
        data = epoch < failUntil.page ? fail(failCode.page) : answers.page?.(args.positions as number[]);
      else if (cmd === "facet_exact")
        data = epoch < failUntil.exact ? fail(failCode.exact) : ok({ kind: "f64", data: f64b64(exactValues) });
      answerCache.set(cacheKey, data);
    }
    return { ...base, data: answerCache.get(cacheKey) };
  });
});
afterEach(cleanup);

const view = () =>
  render(<ChartView spec={{} as never} type={null} entities={[]} onCreate={() => {}} onPatch={() => {}} path="views/w.ai.yaml" />);

function calls(cmd: string) {
  return sdk.useSandboxRun.mock.calls.filter((c) => c[1] === cmd && (c[3]?.enabled ?? true));
}

describe("FacetGallery", () => {
  it("builds the cache from the view file, then opens its index by key — never runs query", () => {
    view();
    // #847/#848 P9: the file, not its text (the args are one argv string, capped at 128 KiB)
    expect(calls("facet_build")[0][2]).toEqual({ path: "views/w.ai.yaml", rev: expect.any(String), epoch: 0 });
    expect(calls("facet_index")[0][2]).toEqual({ key: KEY, epoch: 0 });
    expect(calls("query")).toHaveLength(0);
    expect(screen.getByText(/1000 groups/)).toBeTruthy();
  });

  it("asks for a spec over the argv cap with a call the size of any other (#847/#848 P9)", () => {
    const big = { ...DOC, transform: [{ filter: { field: "lot", oneOf: Array.from({ length: 30_000 }, (_, i) => `L${i}`) } }] };
    expect(JSON.stringify(big).length).toBeGreaterThan(128 * 1024);
    sdk.viewDocument.mockReturnValue(big);
    view();
    const args = calls("facet_build")[0][2] as Record<string, unknown>;
    expect(args.path).toBe("views/w.ai.yaml");
    expect(JSON.stringify({ args }).length).toBeLessThan(200);
    // and an edited file is a new build
    cleanup();
    sdk.viewDocument.mockReturnValue(DOC);
    view();
    expect((calls("facet_build").at(-1)![2] as Record<string, unknown>).rev).not.toEqual(args.rev);
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

  it("recovers from an index with no cache (exit 3) by asking the whole chain again at the next epoch", () => {
    failUntil.index = 1;
    view();
    expect(calls("facet_build").some((c) => c[2].epoch === 1)).toBe(true);
    expect(calls("facet_index").some((c) => c[2].epoch === 1)).toBe(true);
    expect(screen.getByText(/1000 groups/)).toBeTruthy();
  });

  it.each([
    [3, "the cache went (a reap)"],
    [4, "the cache was rebuilt since the index"],
  ])("recovers a page that answers exit %i (%s) at the next epoch", (code) => {
    failUntil.page = 1;
    failCode.page = code;
    view();
    const pages = calls("facet_page").map((c) => c[2] as { epoch: number });
    expect(pages.some((p) => p.epoch === 1)).toBe(true);
    expect(Math.max(...epochsSeen)).toBe(1);
  });

  it("moves the epoch ONCE for any number of pages failing in it, and not again on re-render", () => {
    // big records make small pages (groupsPerPage(60k) is 19), so a screen and
    // its lookahead mount several pages at once, all failing at epoch 0: that
    // must move the epoch to 1, not once per page
    answers.index = ok({ ...INDEX, cells: 60_000 });
    failUntil.page = 1;
    const { rerender } = view();
    const pagesAtZero = new Set(
      calls("facet_page")
        .filter((c) => c[2].epoch === 0)
        .map((c) => (c[2] as { positions: number[] }).positions[0]),
    );
    expect(pagesAtZero.size).toBeGreaterThan(1);
    for (let i = 0; i < 5; i++) rerender(<ChartView spec={{} as never} type={null} entities={[]} onCreate={() => {}} onPatch={() => {}} path="views/w.ai.yaml" />);
    expect(Math.max(...epochsSeen)).toBe(1);
  });

  it("stops after two recoveries and says why, rather than rebuilding forever", () => {
    failUntil.index = 99;
    view();
    expect(Math.max(...epochsSeen)).toBe(2);
    expect(screen.getByRole("alert").textContent).toContain("exit 3");
  });

  it("starts over — epoch and all — when the spec is edited, even after giving up", () => {
    failUntil.index = 99;
    const { rerender } = view();
    expect(screen.getByRole("alert")).toBeTruthy();
    failUntil.index = 0;
    answerCache.clear();
    epochsSeen.length = 0;
    sdk.viewDocument.mockReturnValue({ ...DOC, title: "edited" });
    rerender(<ChartView spec={{} as never} type={null} entities={[]} onCreate={() => {}} onPatch={() => {}} path="views/w.ai.yaml" />);
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByText(/1000 groups/)).toBeTruthy();
    expect(Math.max(...epochsSeen)).toBe(0);
  });

  it("recovers a build that answers exit 3 (its cache went between write and read)", () => {
    failUntil.build = 1;
    view();
    expect(calls("facet_build").some((c) => c[2].epoch === 1)).toBe(true);
    expect(screen.getByText(/1000 groups/)).toBeTruthy();
  });

  it.each(["facet_index", "facet_page"])("shows %s's HTTP error rather than waiting forever", (cmd) => {
    const real = sdk.useSandboxRun.getMockImplementation()!;
    sdk.useSandboxRun.mockImplementation((plugin: string, c: string, args: Record<string, unknown>, opts?: { enabled?: boolean }) =>
      c === cmd && (opts?.enabled ?? true)
        ? { data: undefined, error: new Error(`view plugin "chart" could not run "${cmd}": HTTP 502`), isLoading: false, refetch: vi.fn() }
        : real(plugin, c, args, opts),
    );
    view();
    expect(screen.getByRole("alert").textContent).toContain("HTTP 502");
  });

  it("gives up an enlarged group with the sandbox's reason, not a generic one", () => {
    failUntil.exact = 99;
    view();
    fireEvent.click(screen.getAllByRole("button", { name: /enlarge/i })[0]);
    expect(screen.getByRole("alert").textContent).toContain("exit 3");
  });

  it("keeps the scroll position through a recovery", () => {
    const { rerender } = view();
    const before = document.querySelector("[data-gallery-scroll]") as HTMLElement;
    before.scrollTop = 2400;
    act(() => {
      fireEvent.scroll(before);
    });
    // a page fails: the next epoch's build is still loading, so the gallery is
    // replaced by a notice and its scroller unmounts ...
    failUntil.page = 1;
    loadingBuildAt = 1;
    answerCache.clear();
    rerender(<ChartView spec={{} as never} type={null} entities={[]} onCreate={() => {}} onPatch={() => {}} path="views/w.ai.yaml" />);
    expect(document.querySelector("[data-gallery-scroll]")).toBeNull();
    // ... and when the build answers, the scroller comes back where it was
    loadingBuildAt = -1;
    rerender(<ChartView spec={{} as never} type={null} entities={[]} onCreate={() => {}} onPatch={() => {}} path="views/w.ai.yaml" />);
    const after = document.querySelector("[data-gallery-scroll]") as HTMLElement;
    expect(after.scrollTop).toBe(2400);
  });

  it("recovers an enlarged group whose exact values come back exit 3", () => {
    failUntil.exact = 1;
    view();
    fireEvent.click(screen.getAllByRole("button", { name: /enlarge/i })[0]);
    expect(calls("facet_exact").some((c) => c[2].epoch === 1)).toBe(true);
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

  it("labels a zoned facet column's groups on its clock, naming the zone (#847/#848 P14)", () => {
    const groups = INDEX.groups.map((g, i) => ({ ...g, key: ["L1", `2026-03-${String((i % 28) + 1).padStart(2, "0")} 00:00:00+08:00`] }));
    answers.index = ok({ ...INDEX, facet: ["lot", "day"], zones: { day: "Asia/Taipei" }, groups });
    view();
    const labels = screen.getAllByRole("button", { name: /^group / }).map((b) => b.getAttribute("aria-label"));
    expect(labels).toContain("group L1 · 2026-03-01 Asia/Taipei");
    fireEvent.click(screen.getAllByRole("button", { name: /enlarge/i })[0]);
    expect(screen.getByRole("dialog").getAttribute("aria-label")).toMatch(/^group L1 · 2026-03-\d\d Asia\/Taipei$/);
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

  it("gives each tile a row tall enough for its thumbnail AND its label, so the next row never covers ⤢", () => {
    // In a real browser the label row (and its ⤢) sat under the next row's
    // thumbnails: 0 of 44 enlarge buttons reachable. happy-dom does no layout,
    // so what is pinned is the box arithmetic the layout rests on.
    view();
    const px = (v: string) => Number.parseFloat(v);
    const tiles = screen.getAllByRole("button", { name: /^group / }).map((b) => b.parentElement as HTMLElement);
    const tile = tiles[0];
    const [thumb, labelRow] = [...tile.children] as HTMLElement[];
    const thumbBox = thumb.firstElementChild as HTMLElement;
    expect(getComputedStyle(thumbBox).display).toBe("block"); // no inline baseline gap under a canvas
    const nextRow = tiles.find((t) => px(t.style.top) > px(tile.style.top))!;
    const pitch = px(nextRow.style.top) - px(tile.style.top);
    expect(px(tile.style.height)).toBeGreaterThanOrEqual(px(thumbBox.style.height) + px(labelRow.style.height));
    expect(pitch).toBeGreaterThanOrEqual(px(tile.style.height));
    expect(labelRow.style.overflow).toBe("hidden");
  });

  it("bounds its own scroller by the window, not by whatever height the host pane gives it", () => {
    // The host pane is height:auto, so a `height: 100%` scroller grew to its
    // content (11 102 px for 1000 groups): the host scrolled instead, every
    // tile mounted and every page was asked at once -- virtualization inert.
    view();
    const scroller = document.querySelector("[data-gallery-scroll]") as HTMLElement;
    expect(scroller.style.overflow).toBe("auto");
    expect(scroller.style.maxHeight).toMatch(/^\d+vh$/);
  });

  it("takes focus when a group is enlarged, and Escape puts it away", () => {
    // It covered the gallery's toolbar with no way out but its Close button:
    // Escape did nothing, since focus stayed on the ⤢ underneath.
    const focus = vi.spyOn(HTMLElement.prototype, "focus");
    view();
    fireEvent.click(screen.getAllByRole("button", { name: /enlarge/i })[0]);
    const dialog = screen.getByRole("dialog");
    expect(dialog.contains(document.activeElement)).toBe(true);
    // taking focus must not scroll the host page to the gallery
    expect(focus.mock.contexts.includes(dialog)).toBe(true);
    expect(focus.mock.calls[focus.mock.contexts.indexOf(dialog)][0]).toEqual({ preventScroll: true });
    focus.mockRestore();
    fireEvent.keyDown(document.activeElement as Element, { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("keeps its Escape to itself, and gives focus back to the ⤢ that opened it", () => {
    // The host's ModalShell listens for Escape on document: one press must not
    // close the enlarged view AND whatever the chart sits in.
    const heard = vi.fn();
    document.addEventListener("keydown", heard);
    try {
      view();
      const opener = screen.getAllByRole("button", { name: /enlarge/i })[0];
      opener.focus();
      fireEvent.click(opener);
      fireEvent.keyDown(document.activeElement as Element, { key: "Escape" });
      expect(screen.queryByRole("dialog")).toBeNull();
      expect(heard).not.toHaveBeenCalled();
      expect(document.activeElement).toBe(screen.getAllByRole("button", { name: /enlarge/i })[0]);
    } finally {
      document.removeEventListener("keydown", heard);
    }
  });

  it("enlarges one group with its exact values", () => {
    view();
    fireEvent.click(screen.getAllByRole("button", { name: /enlarge/i })[0]);
    const exact = calls("facet_exact").at(-1)![2] as { key: string; build: string; position: number };
    expect(exact).toEqual({ key: KEY, build: INDEX.build, position: N - 1, epoch: 0 });
    expect(screen.getByRole("dialog")).toBeTruthy();
  });
});
