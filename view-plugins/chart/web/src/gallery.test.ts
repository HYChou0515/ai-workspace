/**
 * #848 P6/P7: the gallery's pure half — sort from the index in hand, page by
 * record size, paint a thumbnail the way the full grid paints, and turn a
 * range of sorted positions into the marking of every group in it.
 */
import { describe, expect, it } from "vitest";

import {
  cellAt,
  type FacetIndex,
  groupLabel,
  groupsLit,
  groupsPerPage,
  rangeMarking,
  sortedPositions,
  thumbnail,
} from "./gallery";
import { toOption } from "./option";
import { lattice } from "./raster";
import { answer, base, layer, q8 } from "./testAnswer";

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
