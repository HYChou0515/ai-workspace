/**
 * Pure structural edits on a sheet's rows (docs/plan-ai-sheet.md Phase 2).
 *
 * Every op takes rows and returns new rows, so the behaviour is testable without
 * a DOM and the renderer stays a thin caller: it serialises whatever comes back
 * through the same buffer path a cell edit uses.
 *
 * Renaming a column is deliberately NOT here: the first row is an ordinary
 * editable row, so renaming a column is just editing its cell. A separate op
 * would be a second way to do one thing.
 */

/** Column count of the grid — the widest row, so an inserted row spans every
 * column the grid actually shows even when the file is ragged. */
export function width(rows: string[][]): number {
  return rows.reduce((w, row) => Math.max(w, row.length), 0);
}

function blankRow(rows: string[][]): string[] {
  return Array.from({ length: Math.max(1, width(rows)) }, () => "");
}

/** Insert a blank row at `at` (clamped to the ends). */
export function insertRow(rows: string[][], at: number): string[][] {
  const index = Math.max(0, Math.min(at, rows.length));
  return [...rows.slice(0, index), blankRow(rows), ...rows.slice(index)];
}

export type SortDir = "asc" | "desc";

/** Sort the DATA rows by one column, leaving row 0 pinned as the header — a CSV's
 * first row names the columns, and dragging it into the middle of the data is
 * never what a header click means.
 *
 * Numeric cells compare as numbers (otherwise "120" sorts before "9"), and blanks
 * always sink to the bottom in BOTH directions: a descending sort whose first
 * screenful is empty cells hides the data the user asked to see. */
export function sortedIndices(rows: string[][], column: number, dir: SortDir): number[] {
  const cell = (fileRow: number): string => rows[fileRow]?.[column] ?? "";
  const asNumber = (text: string): number | null => {
    if (text.trim() === "") return null;
    const n = Number(text);
    return Number.isFinite(n) ? n : null;
  };
  // File indices of the DATA rows (row 0 is the header and never moves).
  const order = rows.map((_, i) => i).slice(1);
  return order.sort((a, b) => {
    const x = cell(a);
    const y = cell(b);
    if (x === "" || y === "") return x === y ? 0 : x === "" ? 1 : -1; // blanks last, both directions
    const nx = asNumber(x);
    const ny = asNumber(y);
    const cmp = nx !== null && ny !== null ? nx - ny : x.localeCompare(y);
    return dir === "asc" ? cmp : -cmp;
  });
}

/** The sorted rows themselves — the same comparator as `sortedIndices`, which is
 * what the grid uses so a sorted cell can still be written back to the file row
 * it came from. One comparator, two shapes. */
export function sortRows(rows: string[][], column: number, dir: SortDir): string[][] {
  const header = rows[0];
  if (header === undefined) return rows;
  return [header, ...sortedIndices(rows, column, dir).map((i) => rows[i] as string[])];
}

/** Insert a blank cell at column `at` in every row. A row shorter than `at`
 * gets the cell appended rather than being padded out to the grid width —
 * widening rows the user did not touch would rewrite the file beyond the edit
 * they asked for, and raggedness is something we surface, not silently repair. */
export function insertColumn(rows: string[][], at: number): string[][] {
  return rows.map((row) => {
    const index = Math.max(0, Math.min(at, row.length));
    return [...row.slice(0, index), "", ...row.slice(index)];
  });
}

/** Drop the row at `at`. Never returns an empty grid: removing the last row
 * leaves one blank row, so there is always a cell to click — an empty grid has
 * no affordance to get back from. */
export function removeRow(rows: string[][], at: number): string[][] {
  const left = rows.filter((_, r) => r !== at);
  return left.length > 0 ? left : [blankRow(rows)];
}

/** Drop column `at` from every row, for the same reason keeping one blank
 * column when it was the only one. */
export function removeColumn(rows: string[][], at: number): string[][] {
  const left = rows.map((row) => row.filter((_, c) => c !== at));
  return width(left) > 0 ? left : left.map(() => [""]);
}

/** A rectangular span of cells, in whatever coordinate space the caller uses. */
export type Range = { top: number; left: number; bottom: number; right: number };

/** Two corners → a normalised range, so a selection dragged up-and-left is the
 * same span as one dragged down-and-right. */
export function normalizeRange(a: { row: number; col: number }, b: { row: number; col: number }): Range {
  return {
    top: Math.min(a.row, b.row),
    left: Math.min(a.col, b.col),
    bottom: Math.max(a.row, b.row),
    right: Math.max(a.col, b.col),
  };
}

/** The cells inside a range, as its own little grid (what lands on the clipboard). */
export function sliceBlock(rows: string[][], range: Range): string[][] {
  const out: string[][] = [];
  for (let r = range.top; r <= range.bottom; r++) {
    const row: string[] = [];
    for (let c = range.left; c <= range.right; c++) row.push(rows[r]?.[c] ?? "");
    out.push(row);
  }
  return out;
}

/** Apply a list of cell writes, GROWING the grid to fit any that fall outside it.
 *
 * One primitive serves paste, cut and clear, and it takes SCATTERED targets on
 * purpose: with a sort active a rectangle on screen is a set of rows all over the
 * file, so an op shaped as "a contiguous block at an offset" could only ever be
 * right for the unsorted case. Growing rather than clipping matters for the same
 * reason a paste must not silently lose its last rows.
 *
 * Returns the input untouched when there is nothing to write, so a no-op edit
 * can't dirty the file. */
export function writeCells(rows: string[][], writes: { row: number; col: number; value: string }[]): string[][] {
  if (writes.length === 0) return rows;
  const height = Math.max(rows.length, ...writes.map((w) => w.row + 1));
  const out: string[][] = Array.from({ length: height }, (_, r) => [...(rows[r] ?? [])]);
  // Rows that did not exist start at the grid's current width so they line up
  // with their neighbours; rows that DID exist keep their own length, because
  // padding untouched rows would silently rewrite a ragged file into a
  // rectangular one (the rule `insertColumn` already follows).
  const base = width(rows);
  for (let r = rows.length; r < height; r++) out[r] = Array.from({ length: base }, () => "");
  for (const w of writes) {
    const row = out[w.row];
    if (!row) continue;
    while (row.length <= w.col) row.push("");
    row[w.col] = w.value;
  }
  return out;
}
