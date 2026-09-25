/**
 * #848 P6/P7: the facet gallery's pure half.
 *
 * - Sorting uses the index in hand (`facet_index`), never a rebuild.
 * - A page is a run of SORTED positions, sized so its records are about 1-2 MB.
 * - A thumbnail is painted by the same calls the full grid makes
 *   (`decodeColumn` -> `lattice` -> `colourTable` -> `paintCells`), so the same
 *   cells are the same pixels (Q13).
 * - A selection is a range of sorted positions; it names every group in it,
 *   loaded or not, and lights groups by the platform's own `isLit`.
 */
import { clockFor } from "./clock";
import type { MarkingValues, IsLit } from "./marking";
import { parseInstant } from "./option";
import { colourTable, lattice, paintCells, type Cell, type Cells, type RasterImage } from "./raster";
import { decodeColumn, type WireColumn } from "./wire";

export type FacetScale =
  | { kind: "continuous"; lo: number; hi: number }
  | { kind: "category"; labels: string[] };

/** What `facet_index` answers. */
export type FacetIndex = {
  build: string;
  scale: FacetScale;
  facet: string[];
  cells: number;
  layout: { x: (Cell | null)[]; y: (Cell | null)[] };
  groups: { key: string[]; sort: Record<string, string | number | boolean | null> }[];
  /** #847/#848 P14: a zoned facet column's zone. Absent from an older sandbox
   * or cache, which leaves every key shown as it is. */
  zones?: Record<string, string>;
};

/** A group's label: its keys, a zoned column's on that zone's clock and named
 * (the key itself -- the marking string -- is pandas' wall time and offset,
 * e.g. `2026-03-01 00:00:00+08:00`). A key that reads as no time shows as is. */
export function groupLabel(index: FacetIndex, position: number): string {
  return index.groups[position].key
    .map((key, i) => {
      const zone = index.zones?.[index.facet[i]];
      if (!zone) return key;
      // pandas writes up to nanoseconds; an instant is read to the millisecond
      const ms = parseInstant(key.replace(/(\.\d{3})\d+/, "$1"));
      return Number.isNaN(ms) ? key : `${clockFor(zone).text(ms)} ${zone}`;
    })
    .join(" · ");
}

export type FacetSort = { field: string; order?: "ascending" | "descending" } | null;

/** Group positions (written order) in the order the gallery shows them. */
export function sortedPositions(index: FacetIndex, sort: FacetSort): number[] {
  const positions = index.groups.map((_, i) => i);
  if (!sort) return positions;
  const sign = sort.order === "descending" ? -1 : 1;
  const value = (i: number) => index.groups[i].sort[sort.field] ?? null;
  // Array.prototype.sort is stable: ties keep the written order.
  return positions.sort((a, b) => {
    const va = value(a);
    const vb = value(b);
    if (va === null || vb === null) return va === null ? (vb === null ? 0 : 1) : -1;
    return va < vb ? -sign : va > vb ? sign : 0;
  });
}

const PAGE_BYTES = 1.5 * 1024 * 1024;
const MAX_PAGE = 200;

/** Groups per page: about PAGE_BYTES of base64 records (4/3 bytes per cell). */
export function groupsPerPage(cells: number): number {
  const perGroup = Math.max(1, Math.ceil((cells * 4) / 3));
  return Math.min(MAX_PAGE, Math.max(1, Math.floor(PAGE_BYTES / perGroup)));
}

/** The marking a selection of positions writes: each facet column's key values
 * over every group in it. `{}` (the write that clears) for none. */
export function rangeMarking(index: FacetIndex, positions: readonly number[]): Record<string, Set<string>> {
  if (positions.length === 0) return {};
  const out: Record<string, Set<string>> = Object.fromEntries(index.facet.map((c) => [c, new Set<string>()]));
  for (const p of positions) index.facet.forEach((c, k) => out[c].add(index.groups[p].key[k]));
  return out;
}

/** Per group (written order), whether the marking lights it; null when the
 * marking shares none of the facet columns — then nothing is dimmed. */
export function groupsLit(index: FacetIndex, marking: MarkingValues, isLit: IsLit): boolean[] | null {
  if (!index.facet.some((c) => c in marking)) return null;
  return index.groups.map((g) => isLit(Object.fromEntries(index.facet.map((c, k) => [c, g.key[k]])), marking));
}

/** Where tiles sit: `count` of them, `columns` per row, each `width` x `height`
 * at a pitch of `pitchX` x `pitchY` from the content's top left. */
export type TileGrid = { count: number; columns: number; pitchX: number; pitchY: number; width: number; height: number };

/** The ranks (sorted order) of every tile a box touches, edges included, in
 * rank order. The box is in the gallery's content coordinates, any corner
 * first. Worked out from the layout arithmetic alone, so a tile the
 * virtualised wall has not drawn is hit exactly as a drawn one is. */
export function tilesInBox(box: { x0: number; y0: number; x1: number; y1: number }, grid: TileGrid): number[] {
  const left = Math.min(box.x0, box.x1);
  const right = Math.max(box.x0, box.x1);
  const top = Math.min(box.y0, box.y1);
  const bottom = Math.max(box.y0, box.y1);
  // an axis's slots (rows or columns) whose [start, start + size] meets [lo, hi]
  const slots = (lo: number, hi: number, pitch: number, size: number, n: number) => {
    const out: number[] = [];
    for (let s = Math.max(0, Math.floor(lo / pitch)); s < n && s * pitch <= hi; s++) {
      if (s * pitch + size >= lo) out.push(s);
    }
    return out;
  };
  const rows = slots(top, bottom, grid.pitchY, grid.height, Math.ceil(grid.count / grid.columns));
  const cols = slots(left, right, grid.pitchX, grid.width, grid.columns);
  const out: number[] = [];
  for (const r of rows) for (const c of cols) if (r * grid.columns + c < grid.count) out.push(r * grid.columns + c);
  return out;
}

const placements = new WeakMap<FacetIndex, Cells>();

/** The cache cell drawn at (col, row) of a group's lattice, or -1 for none.
 * Read back through lattice() itself -- it sorts each axis, fills integer gaps
 * and drops null coordinates, so no hand inversion of its placement agrees. */
export function cellAt(index: FacetIndex, col: number, row: number): number {
  let cells = placements.get(index);
  if (!cells) {
    cells = lattice(index.layout.x, index.layout.y, new Array<number>(index.cells).fill(0));
    placements.set(index, cells);
  }
  if (col < 0 || row < 0 || col >= cells.width || row >= cells.height) return -1;
  return cells.rowAt(col, row);
}

/** One group's thumbnail: its page column (`q8` or `cat`) painted over the
 * cache's lattice. `lit === false` dims every cell, as an unlit row is dimmed
 * in the full grid. */
export function thumbnail(
  index: FacetIndex,
  column: WireColumn,
  scheme: "sequential" | "diverging",
  lit?: boolean,
): RasterImage {
  const col = decodeColumn(column);
  // a column shorter than the lattice (a short page) leaves the rest missing
  // rather than reading past its bytes
  const codes = Array.from({ length: index.cells }, (_, i) => (col.code && i < col.length ? col.code(i) : 255));
  const cells = lattice(index.layout.x, index.layout.y, codes);
  const rows = lit === undefined ? undefined : new Array<boolean>(index.cells).fill(lit);
  return paintCells(cells, colourTable(scheme, col.min ?? 0, col.max ?? 0), rows);
}
