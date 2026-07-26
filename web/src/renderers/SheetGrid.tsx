/**
 * SheetGrid — the editable grid itself (docs/plan-ai-sheet.md Phases 1-3), pure
 * and prop-driven: it takes rows and reports the rows it would like to become,
 * knowing nothing about files, buffers or permissions. `SheetRenderer` is the
 * container that resolves those and feeds this — the same split as
 * `AiYamlRenderer` → the entity views, so every interaction here is testable
 * without a provider.
 *
 * Cell edits and structural edits share ONE outward path (`onRowsChange`), so
 * the caller has a single place to serialise and save; a second callback would
 * be a second place for the two to drift.
 *
 * Sorting is a VIEW: it reorders what you see and reports nothing, so clicking a
 * header never rewrites the file behind your back — "Apply this order to the
 * file" is the explicit way to make it stick. Because of that, a displayed row
 * is NOT the file's row, and every edit is keyed by the file index the displayed
 * row came from.
 *
 * Styling lives in `styles/sheet.css` and deliberately mirrors the entity
 * `.ev-table` skin: chrome only on interaction, because a mesh of borders around
 * thousands of live inputs reads as noise.
 */

import type { ReactNode } from "react";
import { useLayoutEffect, useRef, useState } from "react";

import { Icon } from "../components/Icon";

import { serializeCsv } from "./csv";
import {
  insertColumn,
  insertRow,
  normalizeRange,
  type Range,
  removeColumn,
  removeRow,
  type SortDir,
  sortedIndices,
} from "./sheetOps";
import { visibleRange } from "./sheetWindow";

/** Row height in px. Fixed, because windowing needs to know a row's height
 * WITHOUT measuring every row — cells are single-line inputs, so they are all
 * the same height anyway. Keep in sync with `.sheet-cell` in sheet.css. */
const ROW_HEIGHT = 25;

/** A cell's accessible name, keyed by DISPLAY position (so it follows the sort).
 *
 * The header is not row 1: it sits in its own band with no number, so numbering
 * the first data row "2" would read as "row 1 is missing". Data rows are 1..N and
 * the header cells are named for the column they head — which also announces
 * something more useful than a coordinate to a screen reader. The visible gutter
 * and this name are the SAME number on purpose; two numbering schemes would
 * guarantee one of them ends up lying. */
export function cellLabel(displayRow: number, col: number): string {
  return displayRow === 0 ? `Column ${col + 1} name` : `R${displayRow}C${col + 1}`;
}

type Edit = { fileRow: number; col: number; draft: string };
type Cell = { row: number; col: number };

export function SheetGrid({
  rows,
  readOnly = false,
  onRowsChange,
  onUndo,
  onRedo,
}: {
  rows: string[][];
  /** Disables every edit affordance. Set for a `.readonly/` path (#205) OR a
   * member without write permission — the grid doesn't care which. */
  readOnly?: boolean;
  onRowsChange: (next: string[][]) => void;
  /** Sheet-level history. Omitted ⇒ the shortcuts are left to the browser. */
  onUndo?: () => void;
  onRedo?: () => void;
}) {
  const [edit, setEdit] = useState<Edit | null>(null);
  // The cell the structural actions apply to, in FILE coordinates. Distinct from
  // `edit`, which is cleared on commit — the selection has to outlive the edit
  // for a toolbar button pressed afterwards to act on the cell you were just in.
  const [active, setActive] = useState<{ fileRow: number; col: number }>({ fileRow: 0, col: 0 });
  const [sort, setSort] = useState<{ column: number; dir: SortDir } | null>(null);
  // The selected rectangle, in DISPLAY coordinates (row 0 = header), so it
  // follows the sort the way the user's eyes do. Anchor is where the drag or
  // click started; focus is the end that moves.
  const [sel, setSel] = useState<{ anchor: Cell; focus: Cell } | null>(null);
  const dragging = useRef(false);
  const [scrollTop, setScrollTop] = useState(0);
  const [viewportHeight, setViewportHeight] = useState(0);
  // Scoped to THIS table: two panes can show two sheets, so a document-wide
  // lookup by cell label would sometimes focus the other pane's grid.
  const tableRef = useRef<HTMLTableElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  useLayoutEffect(() => {
    setViewportHeight(scrollRef.current?.clientHeight ?? 0);
  }, []);

  // An empty file still gets one cell: a blank pane offers nothing to click, so
  // there would be no way to start typing in a file you just created.
  const grid = rows.length > 0 ? rows : [[""]];
  const header = grid[0] ?? [];
  // File indices of the data rows, in display order.
  const order = sort ? sortedIndices(grid, sort.column, sort.dir) : grid.map((_, i) => i).slice(1);
  const { start, end } = visibleRange({ scrollTop, viewportHeight, rowHeight: ROW_HEIGHT, total: order.length });

  const range: Range | null = sel ? normalizeRange(sel.anchor, sel.focus) : null;
  const spansManyCells = !!range && (range.top !== range.bottom || range.left !== range.right);
  const inSelection = (row: number, col: number): boolean =>
    !!range && row >= range.top && row <= range.bottom && col >= range.left && col <= range.right;

  /** The value shown at a DISPLAY position — display row 0 is the header, and
   * every other row is whatever the current order puts there. */
  const valueAt = (displayRow: number, col: number): string =>
    displayRow === 0 ? (header[col] ?? "") : (grid[order[displayRow - 1] ?? -1]?.[col] ?? "");

  const selectionBlock = (r: Range): string[][] => {
    const out: string[][] = [];
    for (let row = r.top; row <= r.bottom; row++) {
      const line: string[] = [];
      for (let col = r.left; col <= r.right; col++) line.push(valueAt(row, col));
      out.push(line);
    }
    return out;
  };

  /** Clamp a display position to the grid so Shift+Arrow can't walk off the end. */
  const clamp = (c: Cell): Cell => ({
    row: Math.max(0, Math.min(c.row, order.length)),
    col: Math.max(0, Math.min(c.col, Math.max(0, header.length - 1))),
  });

  const extendTo = (c: Cell) => setSel((s) => (s ? { anchor: s.anchor, focus: clamp(c) } : s));

  const commit = (pending: Edit | null) => {
    setEdit(null);
    if (!pending || readOnly) return;
    const current = grid[pending.fileRow]?.[pending.col] ?? "";
    if (pending.draft === current) return; // nothing typed — don't dirty the file
    onRowsChange(
      grid.map((row, r) => (r === pending.fileRow ? row.map((v, c) => (c === pending.col ? pending.draft : v)) : row)),
    );
  };

  /** Move the caret to another DISPLAYED cell. Out-of-range is a no-op, so Enter
   * on the last row keeps the current cell rather than losing focus to the page. */
  const focusCell = (displayRow: number, col: number) => {
    const target = tableRef.current?.querySelector<HTMLInputElement>(`[aria-label="${cellLabel(displayRow, col)}"]`);
    target?.focus();
    target?.select();
  };

  // Header click cycles none → asc → desc → none, like `TableView`.
  const cycleSort = (column: number) =>
    setSort((s) => (s?.column !== column ? { column, dir: "asc" } : s.dir === "asc" ? { column, dir: "desc" } : null));

  // ONE glyph — a plus above a rule — rotated to say which side. A plus beside a
  // separate arrow was two characters wide and read as two ideas; the direction
  // belongs INSIDE the mark, the way a spreadsheet's own insert affordances do.
  const axisIcon = (name: "insert_line" | "remove_line", deg: number): ReactNode => (
    <Icon name={name} size={14} style={deg ? { transform: `rotate(${deg}deg)` } : undefined} />
  );
  const insertIcon = (deg: number): ReactNode => axisIcon("insert_line", deg);
  const rowActions = [
    { label: "Insert row above", short: <>{insertIcon(0)} Insert</>, run: () => insertRow(grid, active.fileRow) },
    { label: "Insert row below", short: <>{insertIcon(180)} Insert</>, run: () => insertRow(grid, active.fileRow + 1) },
    { label: "Delete row", short: <>{axisIcon("remove_line", 0)} Delete</>, run: () => removeRow(grid, active.fileRow) },
  ];
  const columnActions = [
    { label: "Insert column left", short: <>{insertIcon(-90)} Insert</>, run: () => insertColumn(grid, active.col) },
    { label: "Insert column right", short: <>{insertIcon(90)} Insert</>, run: () => insertColumn(grid, active.col + 1) },
    { label: "Delete column", short: <>{axisIcon("remove_line", -90)} Delete</>, run: () => removeColumn(grid, active.col) },
  ];

  const actionButton = (a: { label: string; short: ReactNode; run: () => string[][] }) => (
    <button
      key={a.label}
      type="button"
      className="btn"
      data-variant="ghost"
      data-size="sm"
      aria-label={a.label}
      title={a.label}
      onClick={() => onRowsChange(a.run())}
    >
      {a.short}
    </button>
  );

  const cellInput = (fileRow: number, displayRow: number, col: number, value: string) => {
    const editing = edit?.fileRow === fileRow && edit.col === col;
    return (
      <input
        className="sheet-cell"
        aria-label={cellLabel(displayRow, col)}
        value={editing ? edit.draft : value}
        readOnly={readOnly}
        onFocus={() => {
          setActive({ fileRow, col });
          setEdit({ fileRow, col, draft: value });
          setSel((s) => (s && dragging.current ? s : { anchor: { row: displayRow, col }, focus: { row: displayRow, col } }));
        }}
        onMouseDown={(e) => {
          if (e.shiftKey) {
            // Shift-click extends. Without preventDefault the input would also
            // run its own shift-select over the text.
            e.preventDefault();
            extendTo({ row: displayRow, col });
            return;
          }
          dragging.current = true;
          setSel({ anchor: { row: displayRow, col }, focus: { row: displayRow, col } });
        }}
        onMouseEnter={() => {
          if (dragging.current) extendTo({ row: displayRow, col });
        }}
        onChange={(e) => setEdit({ fileRow, col, draft: e.target.value })}
        onBlur={() => commit(editing ? edit : null)}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            // Enter commits and steps down the column (Shift+Enter steps up) —
            // Tab/Shift+Tab already walk the row via the inputs' DOM order.
            e.preventDefault();
            commit(editing ? edit : null);
            focusCell(e.shiftKey ? displayRow - 1 : displayRow + 1, col);
          }
          // Esc drops the draft; the input falls back to the caller's value, so
          // the cell visibly reverts.
          if (e.key === "Escape") setEdit(null);
          // Shift+Arrow grows the selection. Plain arrows are left alone on
          // purpose: every cell here is a live input, so they belong to the
          // caret (see docs/plan-ai-sheet.md — there is no "selected" mode).
          const step: Record<string, Cell> = {
            ArrowUp: { row: -1, col: 0 },
            ArrowDown: { row: 1, col: 0 },
            ArrowLeft: { row: 0, col: -1 },
            ArrowRight: { row: 0, col: 1 },
          };
          const d = e.shiftKey ? step[e.key] : undefined;
          if (d) {
            e.preventDefault();
            const from = sel?.focus ?? { row: displayRow, col };
            extendTo({ row: from.row + d.row, col: from.col + d.col });
          }
        }}
      />
    );
  };

  return (
    <div
      className="sheet"
      onCopy={(e) => {
        // Only take over when a BLOCK is selected. With one cell the input's own
        // copy has to keep working, or you could never copy half a value.
        if (!range || !spansManyCells) return;
        e.preventDefault();
        e.clipboardData.setData("text/plain", serializeCsv(selectionBlock(range), "\t"));
      }}
      onMouseUp={() => {
        dragging.current = false;
      }}
      onKeyDown={(e) => {
        if (e.key.toLowerCase() !== "z" || !(e.ctrlKey || e.metaKey)) return;
        // Mid-typing, Ctrl+Z belongs to the input: undoing keystrokes inside the
        // cell you are editing is what the user means. Only once the draft
        // matches what is stored does the shortcut mean "undo the last EDIT".
        const typing = edit && edit.draft !== (grid[edit.fileRow]?.[edit.col] ?? "");
        if (typing) return;
        const run = e.shiftKey ? onRedo : onUndo;
        if (!run) return;
        e.preventDefault();
        run();
      }}
    >
      {!readOnly && (
        <div className="sheet-toolbar">
          <span className="sheet-toolbar__group">
            <span className="sheet-toolbar__label">Row</span>
            {rowActions.map(actionButton)}
          </span>
          <span className="sheet-toolbar__sep" aria-hidden />
          <span className="sheet-toolbar__group">
            <span className="sheet-toolbar__label">Column</span>
            {columnActions.map(actionButton)}
          </span>
          {sort && (
            <button
              type="button"
              className="btn"
              data-variant="secondary"
              data-size="sm"
              onClick={() => {
                // The file is being reordered UNDER the selection, so the
                // selection has to travel with its row: `active` holds a FILE
                // index, and after this write that index points at a different
                // row. Left stale, the very next toolbar action lands somewhere
                // the user isn't looking.
                const moved = order.indexOf(active.fileRow);
                if (moved >= 0) setActive({ fileRow: moved + 1, col: active.col });
                // The view now IS the file order, so there is nothing left to
                // apply — dropping the sort also retires the button.
                setSort(null);
                onRowsChange([header, ...order.map((i) => grid[i] as string[])]);
              }}
            >
              Apply this order to the file
            </button>
          )}
        </div>
      )}
      <div
        ref={scrollRef}
        className="sheet-wrap scrollable"
        onScroll={(e) => {
          setScrollTop(e.currentTarget.scrollTop);
          setViewportHeight(e.currentTarget.clientHeight);
        }}
      >
        <table ref={tableRef} className="sheet-table">
          <thead>
            <tr>
              <th className="sheet-gutter" aria-hidden />
              {header.map((value, c) => (
                <th key={c}>
                  {/* A flex row, NOT an input followed by a button: the cell
                      input is full-width, so in a real browser it paints over
                      the sort control and swallows the click. happy-dom has no
                      layout and cannot catch that, so this is verified in a
                      browser (see docs/plan-ai-sheet.md). */}
                  <div className="sheet-th">
                    <span className="sheet-th__name">{cellInput(0, 0, c, value)}</span>
                    <button
                      type="button"
                      className="sheet-sort"
                      data-sorted={sort?.column === c ? "" : undefined}
                      aria-label={`Sort by ${value || `column ${c + 1}`}`}
                      onClick={() => cycleSort(c)}
                    >
                      {sort?.column === c ? (sort.dir === "asc" ? "▲" : "▼") : "↕"}
                    </button>
                  </div>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {/* Spacers stand in for the rows outside the window, so the scrollbar
                still reflects the whole file. */}
            {start > 0 && (
              <tr aria-hidden style={{ height: start * ROW_HEIGHT }}>
                <td colSpan={Math.max(1, header.length) + 1} />
              </tr>
            )}
            {order.slice(start, end).map((fileRow, k) => {
              const displayRow = start + k + 1; // +1: the header occupies display row 0
              const cells = grid[fileRow] ?? [];
              // A row whose field count disagrees with the header keeps its data
              // and its edits, and says so — dropping it or blanking the pane
              // would hide data the file really contains (the same lesson as
              // #646 on the ingest side). `displayRow` already counts data rows
              // from 1, which is exactly what the gutter shows.
              const ragged = cells.length !== header.length;
              const note = ragged ? `Row ${displayRow} — ${cells.length} of ${header.length} fields` : undefined;
              return (
                <tr key={fileRow} aria-label={note} className={ragged ? "sheet-row--ragged" : undefined}>
                  <td className="sheet-gutter" title={note}>
                    {displayRow}
                  </td>
                  {cells.map((value, c) => (
                    <td key={c} className={inSelection(displayRow, c) ? "sheet-td--sel" : undefined}>
                      {cellInput(fileRow, displayRow, c, value)}
                    </td>
                  ))}
                </tr>
              );
            })}
            {end < order.length && (
              <tr aria-hidden style={{ height: (order.length - end) * ROW_HEIGHT }}>
                <td colSpan={Math.max(1, header.length) + 1} />
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
