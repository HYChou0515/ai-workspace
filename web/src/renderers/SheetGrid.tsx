/**
 * SheetGrid — the editable grid itself (docs/plan-ai-sheet.md Phase 1/2), pure
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
 * A draft lives locally until it is committed, so Esc can discard it and the
 * caller's dirty state means "you changed the file", not "you are mid-keystroke".
 */

import { useRef, useState } from "react";

import { pxToRem } from "../lib/pxToRem";
import { insertColumn, insertRow, removeColumn, removeRow } from "./sheetOps";

/** A cell's accessible name — `R1C1` spreadsheet notation, 1-based, so a caller
 * (and a screen reader) can address a cell without depending on its contents. */
export function cellLabel(row: number, col: number): string {
  return `R${row + 1}C${col + 1}`;
}

type Edit = { row: number; col: number; draft: string };

export function SheetGrid({
  rows,
  readOnly = false,
  onRowsChange,
}: {
  rows: string[][];
  /** Disables every edit affordance. Set for a `.readonly/` path (#205) OR a
   * member without write permission — the grid doesn't care which. */
  readOnly?: boolean;
  onRowsChange: (next: string[][]) => void;
}) {
  const [edit, setEdit] = useState<Edit | null>(null);
  // The cell the structural actions apply to. Distinct from `edit`, which is
  // cleared on commit — the selection has to outlive the edit for a toolbar
  // button pressed afterwards to act on the cell you were just in.
  const [active, setActive] = useState<{ row: number; col: number }>({ row: 0, col: 0 });
  // Scoped to THIS table: two panes can show two sheets, so a document-wide
  // lookup by cell label would sometimes focus the other pane's grid.
  const tableRef = useRef<HTMLTableElement>(null);

  const commit = (pending: Edit | null) => {
    setEdit(null);
    if (!pending || readOnly) return;
    const current = rows[pending.row]?.[pending.col] ?? "";
    if (pending.draft === current) return; // nothing typed — don't dirty the file
    onRowsChange(
      rows.map((row, r) => (r === pending.row ? row.map((v, c) => (c === pending.col ? pending.draft : v)) : row)),
    );
  };

  /** Move the caret to another cell. Out-of-range is a no-op, so Enter on the
   * last row keeps the current cell rather than losing focus to the page. */
  const focusCell = (row: number, col: number) => {
    const target = tableRef.current?.querySelector<HTMLInputElement>(`[aria-label="${cellLabel(row, col)}"]`);
    target?.focus();
    target?.select();
  };

  const actions: { label: string; run: () => string[][] }[] = [
    { label: "Insert row above", run: () => insertRow(rows, active.row) },
    { label: "Insert row below", run: () => insertRow(rows, active.row + 1) },
    { label: "Delete row", run: () => removeRow(rows, active.row) },
    { label: "Insert column left", run: () => insertColumn(rows, active.col) },
    { label: "Insert column right", run: () => insertColumn(rows, active.col + 1) },
    { label: "Delete column", run: () => removeColumn(rows, active.col) },
  ];

  return (
    <div style={{ height: "100%", minHeight: 0, display: "flex", flexDirection: "column" }}>
      {!readOnly && (
        <div className="sheet-toolbar" style={{ display: "flex", gap: 4, flexWrap: "wrap", paddingBottom: 6 }}>
          {actions.map((a) => (
            <button
              key={a.label}
              type="button"
              className="btn"
              data-variant="secondary"
              data-size="sm"
              onClick={() => onRowsChange(a.run())}
            >
              {a.label}
            </button>
          ))}
        </div>
      )}
      <div style={{ flex: 1, minHeight: 0, overflow: "auto" }}>
        <table ref={tableRef} className="sheet-table" style={{ borderCollapse: "collapse", fontSize: pxToRem(12) }}>
          <tbody>
            {rows.map((row, r) => (
              <tr key={r}>
                {row.map((value, c) => {
                  const editing = edit?.row === r && edit.col === c;
                  return (
                    <td key={c} style={{ border: "1px solid var(--paper-3)", padding: 0 }}>
                      <input
                        aria-label={cellLabel(r, c)}
                        value={editing ? edit.draft : value}
                        readOnly={readOnly}
                        onFocus={() => {
                          setActive({ row: r, col: c });
                          setEdit({ row: r, col: c, draft: value });
                        }}
                        onChange={(e) => setEdit({ row: r, col: c, draft: e.target.value })}
                        onBlur={() => commit(editing ? edit : null)}
                        onKeyDown={(e) => {
                          if (e.key === "Enter") {
                            // Enter commits and steps down the column (Shift+Enter
                            // steps up) — Tab/Shift+Tab already walk the row via
                            // the inputs' DOM order.
                            e.preventDefault();
                            commit(editing ? edit : null);
                            focusCell(e.shiftKey ? r - 1 : r + 1, c);
                          }
                          // Esc drops the draft; the input falls back to the
                          // caller's value, so the cell visibly reverts.
                          if (e.key === "Escape") setEdit(null);
                        }}
                        style={{
                          border: "none",
                          background: "transparent",
                          color: "inherit",
                          font: "inherit",
                          padding: "3px 8px",
                          width: "100%",
                        }}
                      />
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
