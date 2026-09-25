/**
 * #848 P6/P7: the gallery's pure half — sort from the index in hand, page by
 * record size, paint a thumbnail the way the full grid paints, and turn a
 * range of sorted positions into the marking of every group in it.
 */
import { describe, expect, it } from "vitest";

import {
  cellAt,
  cellsLit,
  type FacetIndex,
  groupLabel,
  groupsLit,
  groupsPerPage,
  rangeMarking,
  sortArgs,
  sortedPositions,
  stackImage,
  stackSet,
  thumbnail,
  tilesInBox,
} from "./gallery";
import { toOption } from "./option";
import { categoryTable, lattice } from "./raster";
import { answer, base, f64, layer, q8 } from "./testAnswer";

// the platform's rule, as the SDK double in FacetGallery.test.tsx has it
const isLitDouble = (row: Readonly<Record<string, string>>, marking: Record<string, ReadonlySet<string>>) => {
  let shared = false;
  for (const [c, v] of Object.entries(marking)) {
    if (!(c in row)) continue;
    shared = true;
    if (!v.has(row[c])) return false;
  }
  return shared;
};

function index(over: Partial<FacetIndex> = {}): FacetIndex {
  return {
    build: "b".repeat(32),
    scale: { kind: "continuous", lo: 0, hi: 254 },
    facet: ["lot", "wafer"],
    cells: 4,
    layout: { x: [0, 1, 0, 1], y: [0, 0, 1, 1] },
    groups: [
      { key: ["L1", "1"], sort: { rate: 0.3 } },
      { key: ["L1", "2"], sort: { rate: null } },
      { key: ["L2", "3"], sort: { rate: 0.9 } },
      { key: ["L2", "4"], sort: { rate: 0.3 } },
    ],
    columns: [],
    ...over,
  };
}

describe("sortedPositions", () => {
  it("orders groups by the sort field, missing values last, ties in written order", () => {
    expect(sortedPositions(index(), { field: "rate", order: "descending" })).toEqual([2, 0, 3, 1]);
    expect(sortedPositions(index(), { field: "rate", order: "ascending" })).toEqual([0, 3, 2, 1]);
  });

  it("keeps the written order without a sort", () => {
    expect(sortedPositions(index(), null)).toEqual([0, 1, 2, 3]);
  });
});

describe("groupsPerPage", () => {
  it("targets about 1-2 MB of records per page: tens of groups at 50k cells", () => {
    const n = groupsPerPage(50_000);
    expect(n).toBeGreaterThanOrEqual(10);
    expect(n).toBeLessThanOrEqual(60);
  });

  it("is at least one group and at most a screenful for tiny lattices", () => {
    expect(groupsPerPage(10_000_000)).toBe(1);
    expect(groupsPerPage(4)).toBe(200);
  });
});

describe("rangeMarking", () => {
  it("names every group in the positions, loaded or not, per facet column", () => {
    const m = rangeMarking(index(), [2, 0, 3]);
    expect(Object.keys(m).sort()).toEqual(["lot", "wafer"]);
    expect([...m.lot].sort()).toEqual(["L1", "L2"]);
    expect([...m.wafer].sort()).toEqual(["1", "3", "4"]);
  });

  it("is empty — the write that clears — for no positions", () => {
    expect(rangeMarking(index(), [])).toEqual({});
  });
});

describe("groupsLit", () => {
  const isLit = (row: Readonly<Record<string, string>>, marking: Record<string, ReadonlySet<string>>) => {
    let shared = false;
    for (const [c, values] of Object.entries(marking)) {
      if (!(c in row)) continue;
      shared = true;
      if (!values.has(row[c])) return false;
    }
    return shared;
  };

  it("lights the groups whose key the marking holds, by the platform's rule", () => {
    expect(groupsLit(index(), { wafer: new Set(["3", "4"]) }, isLit)).toEqual([false, false, true, true]);
  });

  it("is null when the marking shares no facet column: nothing is dimmed", () => {
    expect(groupsLit(index(), { die: new Set(["7"]) }, isLit)).toBeNull();
  });
});

describe("cellAt", () => {
  // lattice() sorts each axis, fills integer gaps and drops null coordinates;
  // the enlarged view must read back through the SAME placement
  it.each([
    ["unsorted x", [1, 0, 1, 0], [0, 0, 1, 1]],
    ["y written top-first", [0, 1, 0, 1], [1, 1, 0, 0]],
    ["a gap in x", [0, 2, 0, 2], [0, 0, 1, 1]],
    ["a null coordinate", [null, 0, 1, 0], [0, 0, 0, 1]],
  ] as const)("maps a drawn cell back to its cache cell (%s)", (_name, x, y) => {
    const idx = index({ cells: x.length, layout: { x: [...x], y: [...y] } });
    const cells = lattice([...x], [...y], x.map((_, i) => i));
    for (let row = 0; row < cells.height; row++)
      for (let col = 0; col < cells.width; col++) expect(cellAt(idx, col, row)).toBe(cells.rowAt(col, row));
  });
});

describe("cellAt, outside the drawing", () => {
  it("is no cell, not another group's cell, past any edge", () => {
    const idx = index({ cells: 2, layout: { x: [0, 1], y: [0, 0] } });
    expect([cellAt(idx, -1, 0), cellAt(idx, 2, 0), cellAt(idx, 0, 1), cellAt(idx, 0, -1)]).toEqual([-1, -1, -1, -1]);
  });
});

describe("thumbnail", () => {
  it("paints a page column shorter than the lattice as missing cells, not a crash", () => {
    const thumb = thumbnail(index(), q8([10], 0, 254), "sequential");
    const alphas = Array.from({ length: 4 }, (_, i) => thumb.data[i * 4 + 3]);
    expect(alphas.filter((a) => a > 0)).toHaveLength(1);
  });

  // diverging reads the range (a sequential ramp only reads the code), so both
  // schemes are needed for a wrong range on one path to show
  it.each(["sequential", "diverging"] as const)(
    "paints exactly the pixels the full grid paints for the same cells (Q13, %s)",
    (scheme) => {
    const codes = [0, 127, 254, 255];
    const scale = { kind: "continuous" as const, lo: -3, hi: 9 };
    const idx = index({ scale });
    const full = toOption(
      {
        ...base,
        mark: "grid",
        encoding: {
          x: { field: "x", type: "ordinal" },
          y: { field: "y", type: "ordinal" },
          color: { field: "v", type: "quantitative", scale: { scheme } },
        },
      },
      answer(
        layer("grid", 4, {
          x: { kind: "cat", levels: [0, 1], width: 1, codes: btoa(String.fromCharCode(0, 1, 0, 1)) },
          y: { kind: "cat", levels: [0, 1], width: 1, codes: btoa(String.fromCharCode(0, 0, 1, 1)) },
          v: q8(codes, -3, 9),
        }),
      ),
    ).grids[0].image;
    const thumb = thumbnail(idx, q8(codes, -3, 9), scheme);
    expect(thumb.width).toBe(full.width);
    expect(thumb.height).toBe(full.height);
    expect(Array.from(thumb.data)).toEqual(Array.from(full.data));
  },
  );

  it("dims the cells of a group the marking leaves unlit, as the full grid does", () => {
    const thumb = thumbnail(index(), q8([0, 1, 2, 3], 0, 254), "sequential", false);
    const alphas = Array.from({ length: 4 }, (_, i) => thumb.data[i * 4 + 3]);
    expect(alphas.every((a) => a === 64)).toBe(true);
  });
});

describe("groupLabel (#847/#848 P14)", () => {
  const two = (zones?: Record<string, string>) =>
    index({
      facet: ["lot", "day"],
      ...(zones ? { zones } : {}),
      groups: [
        { key: ["L1", "2026-03-01 00:00:00+08:00"], sort: {} },
        { key: ["L1", "2026-03-01 12:30:00.500000+08:00"], sort: {} },
        { key: ["L2", "not a date"], sort: {} },
      ],
    });

  it("shows a zoned column's key on its clock, and names the zone", () => {
    const idx = two({ day: "Asia/Taipei" });
    expect(groupLabel(idx, 0)).toBe("L1 · 2026-03-01 Asia/Taipei");
    // pandas writes microseconds; the label reads them to the millisecond
    expect(groupLabel(idx, 1)).toBe("L1 · 2026-03-01 12:30:00.500 Asia/Taipei");
    // a key that is no time is shown as it is
    expect(groupLabel(idx, 2)).toBe("L2 · not a date");
  });

  it("shows every key as it is when the index names no zone", () => {
    expect(groupLabel(two(), 0)).toBe("L1 · 2026-03-01 00:00:00+08:00");
    expect(groupLabel(index(), 0)).toBe(index().groups[0].key.join(" · "));
  });
});

describe("sortArgs (P4)", () => {
  const doc = { view: "chart", facet: { field: "g", sort: { field: "rate", order: "descending" } }, mark: "grid" };

  it("adds nothing when the choice is the spec's own sort: the same call, no new build", () => {
    expect(sortArgs(doc, { field: "rate" })).toEqual({});
  });

  it("names the column and statistic of any other choice (the order stays the spec's)", () => {
    expect(sortArgs(doc, { field: "v", stat: "median" })).toEqual({ sort: { field: "v", stat: "median" } });
    expect(sortArgs(doc, { field: "lot" })).toEqual({ sort: { field: "lot" } });
  });

  it("is a new choice when only the statistic changes", () => {
    const withStat = { facet: { field: "g", sort: { field: "v", stat: "mean" } } };
    expect(sortArgs(withStat, { field: "v", stat: "max" })).toEqual({ sort: { field: "v", stat: "max" } });
    expect(sortArgs(withStat, { field: "v", stat: "mean" })).toEqual({});
  });

  it("says null for the written order when the spec sorts, and nothing when it does not", () => {
    expect(sortArgs(doc, null)).toEqual({ sort: null });
    expect(sortArgs({ view: "chart", facet: { field: "g" } }, null)).toEqual({});
  });
});

describe("a category thumbnail (P19)", () => {
  it("paints each category its own colour from the category palette, not one ramp end", () => {
    const idx = index({ scale: { kind: "category", labels: ["a", "b", "c"] } });
    // cat codes: cell 0 -> a, 1 -> b, 2 -> c, 3 missing
    const column = { kind: "cat" as const, levels: ["a", "b", "c"], width: 1 as const, codes: btoa(String.fromCharCode(0, 1, 2, 255)) };
    const image = thumbnail(idx, column, "sequential");
    const table = categoryTable(3);
    // lattice rows run top first: cells 2, 3 are the top row, 0, 1 the bottom
    const px = (i: number) => Array.from(image.data.slice(i * 4, i * 4 + 4));
    const code = (c: number) => Array.from(table.slice(c * 4, c * 4 + 4));
    expect([px(0), px(1), px(2), px(3)]).toEqual([code(2), [0, 0, 0, 0], code(0), code(1)]);
    expect(new Set([px(0), px(2), px(3)].map(String)).size).toBe(3);
  });
});

describe("the stack panel's pure half (P5)", () => {
  it("paints a stack as the thumbnail paints the same values, over the gallery's lattice", () => {
    // values that are their own q8 codes over [0, 254]: the thumbnail of those
    // codes is the oracle (the same lattice, table and paint)
    const values = [0, 254, 127, null];
    const got = stackImage(index(), f64(values), "sequential")!;
    const want = thumbnail(index(), q8([0, 254, 127, 255], 0, 254), "sequential");
    expect(got.min).toBe(0);
    expect(got.max).toBe(254);
    expect(Array.from(got.image.data)).toEqual(Array.from(want.data));
  });

  it("rounds a half-way value to even, as the sandbox's q8 (numpy rint) does", () => {
    const got = stackImage(index(), f64([0, 254, 0.5, 1.5]), "sequential")!;
    const want = thumbnail(index(), q8([0, 254, 0, 2], 0, 254), "sequential");
    expect(Array.from(got.image.data)).toEqual(Array.from(want.data));
  });

  it("dims the cells the marking leaves unlit", () => {
    const got = stackImage(index(), f64([1, 2, 3, 4]), "sequential", [true, false, true, false])!;
    const alphas = [0, 1, 2, 3].map((i) => got.image.data[i * 4 + 3]);
    // lattice rows run top first: cells 2, 3 (y = 1) are the top row
    expect(alphas).toEqual([255, 64, 255, 64]);
  });

  it("is nothing to paint when no cell has a value", () => {
    expect(stackImage(index(), f64([null, null, null, null]), "sequential")).toBeNull();
  });

  it("paints a flat stack (one value everywhere) without dividing by zero", () => {
    const got = stackImage(index(), f64([5, 5, 5, 5]), "diverging")!;
    expect([got.min, got.max]).toEqual([5, 5]);
    expect(got.image.data.every((v) => Number.isFinite(v))).toBe(true);
  });

  it("lights a cell by the marking's x / y values, as the full grid does", () => {
    const lit = cellsLit(index(), "x", "y", { x: new Set(["1"]) }, isLitDouble);
    expect(lit).toEqual([false, true, false, true]); // layout x: [0, 1, 0, 1]
  });

  it("dims nothing when the marking names neither axis", () => {
    expect(cellsLit(index(), "x", "y", { wafer: new Set(["1"]) }, isLitDouble)).toBeNull();
  });

  it("stacks the selection, else the tiles the marking lights, else every tile", () => {
    const idx = index();
    expect(stackSet(idx, new Set([2, 0]), [false, true, false, false])).toEqual([["L1", "1"], ["L2", "3"]]);
    expect(stackSet(idx, new Set(), [false, true, false, true])).toEqual([["L1", "2"], ["L2", "4"]]);
    expect(stackSet(idx, new Set(), [false, false, false, false])).toBeNull();
    expect(stackSet(idx, new Set(), null)).toBeNull();
  });
});

describe("tilesInBox (P3 box select)", () => {
  // tiles 10 wide on a 15 px pitch, 20 tall on a 25 px pitch, 3 per row
  const grid = { count: 8, columns: 3, pitchX: 15, pitchY: 25, width: 10, height: 20 };

  it("hits every tile the box touches, by rank, from the layout alone", () => {
    // from inside tile 0 to inside tile 4 (row 1, column 1)
    expect(tilesInBox({ x0: 5, y0: 5, x1: 20, y1: 30 }, grid)).toEqual([0, 1, 3, 4]);
  });

  it("is the same box whichever corner the drag started from", () => {
    expect(tilesInBox({ x0: 20, y0: 30, x1: 5, y1: 5 }, grid)).toEqual([0, 1, 3, 4]);
  });

  it("hits nothing in the gaps between tiles", () => {
    expect(tilesInBox({ x0: 11, y0: 0, x1: 14, y1: 70 }, grid)).toEqual([]);
    expect(tilesInBox({ x0: 0, y0: 21, x1: 40, y1: 24 }, grid)).toEqual([]);
  });

  it("counts a tile the box's edge only touches", () => {
    expect(tilesInBox({ x0: 10, y0: 20, x1: 14, y1: 24 }, grid)).toEqual([0]);
  });

  it("stops at the last tile: an empty slot in the last row is no rank", () => {
    // row 2 holds ranks 6, 7; column 2 of it is empty
    expect(tilesInBox({ x0: 0, y0: 50, x1: 60, y1: 60 }, grid)).toEqual([6, 7]);
  });

  it("reaches tiles far past any that are drawn (rows are arithmetic, not DOM)", () => {
    const big = { ...grid, count: 3000 };
    expect(tilesInBox({ x0: 0, y0: 25 * 900, x1: 1, y1: 25 * 900 + 1 }, big)).toEqual([2700]);
  });

  it("hits nothing right of the last column, nor above the first row", () => {
    expect(tilesInBox({ x0: 41, y0: 0, x1: 90, y1: 90 }, grid)).toEqual([]);
    expect(tilesInBox({ x0: 0, y0: -30, x1: 90, y1: -1 }, grid)).toEqual([]);
  });
});
