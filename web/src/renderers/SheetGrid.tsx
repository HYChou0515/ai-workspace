/**
 * SheetGrid — the editable grid itself (docs/plan-ai-sheet.md Phase 1), pure and
 * prop-driven: it takes rows and reports a committed cell, and knows nothing
 * about files, buffers or permissions. `SheetRenderer` is the container that
 * resolves those and feeds this — the same split as `AiYamlRenderer` → the
 * entity views, so the behaviour here is testable without any provider.
 *
 * A draft lives locally until it is committed, so Esc can discard it and the
 * caller's dirty state means "you changed the file", not "you are mid-keystroke".
 */

import { useRef, useState } from "react";

import { pxToRem } from "../lib/pxToRem";

/** A cell's accessible name — `R1C1` spreadsheet notation, 1-based, so a caller
 * (and a screen reader) can address a cell without depending on its contents. */
export function cellLabel(row: number, col: number): string {
  return `R${row + 1}C${col + 1}`;
}

type Edit = { row: number; col: number; draft: string };

export function SheetGrid({
  rows,
  readOnly = false,
  onCommitCell,
}: {
  rows: string[][];
  /** Disables every edit affordance. Set for a `.readonly/` path (#205) OR a
   * member without write permission — the grid doesn't care which. */
  readOnly?: boolean;
  onCommitCell: (row: number, col: number, value: string) => void;
}) {
  const [edit, setEdit] = useState<Edit | null>(null);
  // Scoped to THIS table: two panes can show two sheets, so a document-wide
  // lookup by cell label would sometimes focus the other pane's grid.
  const tableRef = useRef<HTMLTableElement>(null);

  const commit = (pending: Edit | null) => {
    setEdit(null);
    if (!pending || readOnly) return;
    const current = rows[pending.row]?.[pending.col] ?? "";
    if (pending.draft === current) return; // nothing typed — don't dirty the file
    onCommitCell(pending.row, pending.col, pending.draft);
  };

  /** Move the caret to another cell. Out-of-range is a no-op, so Enter on the
   * last row keeps the current cell rather than losing focus to the page. */
  const focusCell = (row: number, col: number) => {
    const target = tableRef.current?.querySelector<HTMLInputElement>(`[aria-label="${cellLabel(row, col)}"]`);
    target?.focus();
    target?.select();
  };

  return (
    <div style={{ height: "100%", minHeight: 0, overflow: "auto" }}>
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
                      onFocus={() => setEdit({ row: r, col: c, draft: value })}
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
  );
}
