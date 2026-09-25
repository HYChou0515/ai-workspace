/**
 * The grid raster core (Q13): cells + one colour table → pixels. The full
 * chart embeds the result as an ECharts image; PR 4's thumbnails paint the
 * same result on a plain canvas, so it must not know about either host.
 */
import { describe, expect, it } from "vitest";

import { colourTable, lattice, MISSING, paintCells, upscale } from "./raster";

describe("lattice", () => {
  it("places each row's code at its (x, y) cell, row-major, top row = highest y", () => {
    const cells = lattice([0, 1, 0, 1], [0, 0, 1, 1], [10, 20, 30, 40]);
    expect(cells.xs).toEqual([0, 1]);
    expect(cells.ys).toEqual([0, 1]);
    // Row 0 is y = 1 (drawn at the top), row 1 is y = 0.
    expect(Array.from(cells.codes)).toEqual([30, 40, 10, 20]);
  });

  it("leaves a cell no row names empty", () => {
    const cells = lattice([0, 2], [0, 0], [5, 6]);
    expect(cells.xs).toEqual([0, 1, 2]);
    expect(Array.from(cells.codes)).toEqual([5, MISSING, 6]);
  });

  it("fills the integer gaps of a numeric axis but keeps a category axis as given", () => {
    expect(lattice([1, 4], [0, 0], [1, 2]).xs).toEqual([1, 2, 3, 4]);
    expect(lattice(["b", "a"], [0, 0], [1, 2]).xs).toEqual(["a", "b"]);
  });

  it("keeps sparse integers as labels instead of filling a huge span", () => {
    expect(lattice([1, 1000], [0, 0], [1, 2]).xs).toEqual([1, 1000]);
  });

  it("sorts fractional numbers numerically", () => {
    expect(lattice([0.5, 0.25, 10], [0, 0, 0], [1, 2, 3]).xs).toEqual([0.25, 0.5, 10]);
  });

  it("skips a row with no x or no y", () => {
    const cells = lattice([0, null, 1], [0, 0, null], [1, 2, 3]);
    expect(cells.xs).toEqual([0]);
    expect(Array.from(cells.codes)).toEqual([1]);
  });

  it("finds the row behind a cell, for lasso hit-testing", () => {
    const cells = lattice([0, 1, 0, 1], [0, 0, 1, 1], [10, 20, 30, 40]);
    expect(cells.rowAt(0, 0)).toBe(2); // top-left is (x 0, y 1) = row 2
    expect(cells.rowAt(1, 1)).toBe(1);
    expect(lattice([0, 2], [0, 0], [5, 6]).rowAt(1, 0)).toBe(-1);
  });
});

describe("colourTable", () => {
  it("makes 256 RGBA entries with the missing code transparent", () => {
    const t = colourTable("sequential", 0, 1);
    expect(t.length).toBe(256 * 4);
    expect(t[MISSING * 4 + 3]).toBe(0);
    expect(t[0 * 4 + 3]).toBe(255);
  });

  it("runs sequential from dark to light", () => {
    const t = colourTable("sequential", 0, 1);
    const lum = (c: number) => t[c * 4] * 0.299 + t[c * 4 + 1] * 0.587 + t[c * 4 + 2] * 0.114;
    expect(lum(254)).toBeGreaterThan(lum(127));
    expect(lum(127)).toBeGreaterThan(lum(0));
  });

  it("puts the neutral middle of a diverging scale at zero, not at the middle code", () => {
    // min -1, max 3: zero is code 254 * 1/4 ≈ 64.
    const t = colourTable("diverging", -1, 3);
    const near = (c: number) => [t[c * 4], t[c * 4 + 1], t[c * 4 + 2]];
    const [r, g, b] = near(64);
    expect(Math.min(r, g, b)).toBeGreaterThan(230); // near white
    const [r0, , b0] = near(0);
    expect(b0).toBeGreaterThan(r0); // negative side is blue
    const [r1, , b1] = near(254);
    expect(r1).toBeGreaterThan(b1); // positive side is red
  });

  it("makes a flat diverging scale neutral", () => {
    const t = colourTable("diverging", 0, 0);
    expect(Math.min(t[0], t[1], t[2])).toBeGreaterThan(230);
  });
});

describe("paintCells", () => {
  it("paints one pixel per cell from the table", () => {
    const cells = lattice([0, 1], [0, 0], [0, MISSING]);
    const img = paintCells(cells, colourTable("sequential", 0, 1));
    expect(img.width).toBe(2);
    expect(img.height).toBe(1);
    const t = colourTable("sequential", 0, 1);
    expect(Array.from(img.data.slice(0, 4))).toEqual(Array.from(t.slice(0, 4)));
    expect(img.data[7]).toBe(0);
  });
});

describe("upscale (#847/#848 PR 5 P29)", () => {
  it("draws each cell as whole pixels of its own colour, at a size no multiple of the cells", () => {
    // 3 x 2 cells, each its own colour (r = the cell's index)
    const data = new Uint8ClampedArray(3 * 2 * 4);
    for (let i = 0; i < 6; i++) data.set([i, 0, 0, 255], i * 4);
    const big = upscale({ width: 3, height: 2, data }, 7, 5);
    expect([big.width, big.height]).toEqual([7, 5]);
    const cellAt = (x: number, y: number) => big.data[(y * 7 + x) * 4];
    // each pixel is the cell its centre falls in: columns 0,0,1,1,1,2,2; rows 0,0,1,1,1
    const expected = [0, 0, 1, 1, 1].map((r) => [0, 0, 1, 1, 1, 2, 2].map((c) => r * 3 + c));
    expect(Array.from({ length: 5 }, (_, y) => Array.from({ length: 7 }, (_, x) => cellAt(x, y)))).toEqual(expected);
    // nothing between two cells' colours: every pixel is opaque, green and blue 0
    for (let i = 0; i < 35; i++) expect([big.data[i * 4 + 1], big.data[i * 4 + 2], big.data[i * 4 + 3]]).toEqual([0, 0, 255]);
  });

  it("keeps a clear cell clear and a dimmed one dimmed", () => {
    const data = Uint8ClampedArray.from([10, 20, 30, 0, 40, 50, 60, 64]);
    const big = upscale({ width: 2, height: 1, data }, 4, 2);
    expect(Array.from(big.data.slice(0, 16))).toEqual([10, 20, 30, 0, 10, 20, 30, 0, 40, 50, 60, 64, 40, 50, 60, 64]);
    expect(Array.from(big.data.slice(16))).toEqual(Array.from(big.data.slice(0, 16)));
  });
});
