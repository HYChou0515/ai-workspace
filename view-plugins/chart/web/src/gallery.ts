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
import type { MarkingValues, IsLit } from "./marking";
import { colourTable, lattice, paintCells, type Cell, type RasterImage } from "./raster";
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
};

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
  const codes = Array.from({ length: index.cells }, (_, i) => (col.code ? col.code(i) : 255));
  const cells = lattice(index.layout.x, index.layout.y, codes);
  const rows = lit === undefined ? undefined : new Array<boolean>(index.cells).fill(lit);
  return paintCells(cells, colourTable(scheme, col.min ?? 0, col.max ?? 0), rows);
}
