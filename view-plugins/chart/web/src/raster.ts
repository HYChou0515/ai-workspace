/**
 * The grid raster core (plan Q13) — one implementation, two hosts.
 *
 *   lattice(x, y, codes) → Cells          a q8 code per (x, y) cell
 *   colourTable(scheme, min, max)         256 RGBA entries, indexed by code
 *   paintCells(cells, table) → RasterImage one pixel per cell
 *
 * The full chart draws the image from a `custom` series (ECharts supplies the axes,
 * tooltip and brush; lasso hit-testing on cells uses `rowAt`). PR 4's
 * thumbnails put the same image on a plain canvas. Nothing here imports a host,
 * so "same cells → same pixels" holds by construction; `RasterImage` has
 * ImageData's shape, and a browser host wraps it with `new ImageData(...)`.
 *
 * Codes are the sandbox's q8 levels (`chart_view/wire.py`): 0..254 over
 * [min, max], 255 = no value.
 */

export const MISSING = 255;

export type Cell = string | number | boolean;

export type Cells = {
  /** Column labels, left to right. */
  xs: Cell[];
  /** Row labels, bottom to top (row 0 of `codes` is the LAST of these). */
  ys: Cell[];
  width: number;
  height: number;
  /** Row-major, top row first; MISSING where no data row names the cell. */
  codes: Uint8Array;
  /** The data row drawn at (column, row-from-top), or -1. */
  rowAt(column: number, row: number): number;
};

export type RasterImage = { width: number; height: number; data: Uint8ClampedArray };

/** Integer axes fill their gaps (a lattice with a missing cell still has
 * its slot) unless the span is far wider than the values — then it is a label set. */
const FILL_LIMIT = 4;

function axis(values: Cell[]): Cell[] {
  const unique = [...new Set(values)];
  if (unique.every((v) => typeof v === "number" && Number.isInteger(v))) {
    const nums = unique as number[];
    const lo = Math.min(...nums);
    const hi = Math.max(...nums);
    if (hi - lo + 1 <= FILL_LIMIT * nums.length) return Array.from({ length: hi - lo + 1 }, (_, i) => lo + i);
    return nums.sort((a, b) => a - b);
  }
  if (unique.every((v) => typeof v === "number")) return (unique as number[]).sort((a, b) => a - b);
  return unique.sort((a, b) => (String(a) < String(b) ? -1 : String(a) > String(b) ? 1 : 0));
}

export function lattice(x: (Cell | null)[], y: (Cell | null)[], codes: ArrayLike<number>): Cells {
  const keep: number[] = [];
  for (let i = 0; i < x.length; i++) if (x[i] !== null && y[i] !== null) keep.push(i);
  const xs = axis(keep.map((i) => x[i] as Cell));
  const ys = axis(keep.map((i) => y[i] as Cell));
  const col = new Map(xs.map((v, i) => [v, i]));
  const rowFromBottom = new Map(ys.map((v, i) => [v, i]));
  const width = xs.length;
  const height = ys.length;
  const out = new Uint8Array(width * height).fill(MISSING);
  const source = new Int32Array(width * height).fill(-1);
  for (const i of keep) {
    const c = col.get(x[i] as Cell) as number;
    const r = height - 1 - (rowFromBottom.get(y[i] as Cell) as number);
    out[r * width + c] = codes[i];
    source[r * width + c] = i;
  }
  return { xs, ys, width, height, codes: out, rowAt: (c, r) => source[r * width + c] };
}

type RGB = [number, number, number];

// viridis, 9 stops — perceptually even, dark → light, readable without colour vision.
const SEQUENTIAL: RGB[] = [
  [68, 1, 84],
  [71, 45, 123],
  [59, 82, 139],
  [44, 114, 142],
  [33, 145, 140],
  [40, 174, 128],
  [94, 201, 98],
  [173, 220, 48],
  [253, 231, 37],
];

// ColorBrewer RdBu, reversed: blue below zero, near-white at zero, red above.
const DIVERGING: RGB[] = [
  [33, 102, 172],
  [103, 169, 207],
  [209, 229, 240],
  [247, 247, 247],
  [253, 219, 199],
  [239, 138, 98],
  [178, 24, 43],
];

function ramp(stops: RGB[], t: number): RGB {
  const p = Math.min(1, Math.max(0, t)) * (stops.length - 1);
  const i = Math.min(stops.length - 2, Math.floor(p));
  const f = p - i;
  return [0, 1, 2].map((k) => Math.round(stops[i][k] + (stops[i + 1][k] - stops[i][k]) * f)) as RGB;
}

export function colourTable(scheme: "sequential" | "diverging", min: number, max: number): Uint8ClampedArray {
  const table = new Uint8ClampedArray(256 * 4);
  const reach = Math.max(Math.abs(min), Math.abs(max));
  for (let code = 0; code < MISSING; code++) {
    let rgb: RGB;
    if (scheme === "sequential") rgb = ramp(SEQUENTIAL, code / 254);
    else {
      const value = min + (code / 254) * (max - min);
      rgb = ramp(DIVERGING, reach === 0 ? 0.5 : (value / reach + 1) / 2);
    }
    table.set([...rgb, 255], code * 4);
  }
  // MISSING stays [0, 0, 0, 0]: transparent.
  return table;
}

/** Alpha of a cell a highlight leaves unlit (a lit or unhighlighted cell is opaque). */
export const DIM_ALPHA = 64;

/** `lit`, when given, says per DATA ROW whether it is lit (`Cells.rowAt` maps
 * a cell to its row); an unlit cell is painted at DIM_ALPHA. */
export function paintCells(cells: Cells, table: Uint8ClampedArray, lit?: readonly boolean[]): RasterImage {
  const data = new Uint8ClampedArray(cells.width * cells.height * 4);
  for (let i = 0; i < cells.codes.length; i++) {
    const c = cells.codes[i] * 4;
    data[i * 4] = table[c];
    data[i * 4 + 1] = table[c + 1];
    data[i * 4 + 2] = table[c + 2];
    data[i * 4 + 3] = table[c + 3];
    if (lit && data[i * 4 + 3] > 0) {
      const row = cells.rowAt(i % cells.width, Math.floor(i / cells.width));
      if (row >= 0 && !lit[row]) data[i * 4 + 3] = DIM_ALPHA;
    }
  }
  return { width: cells.width, height: cells.height, data };
}
