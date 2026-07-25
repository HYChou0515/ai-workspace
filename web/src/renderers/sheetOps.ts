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
