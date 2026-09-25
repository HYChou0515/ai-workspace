/**
 * DataGrid — a pure presentational table for delimited data (issue #361).
 * Takes already-parsed `rows` (row 0 = header) so it serves both the workspace
 * CsvRenderer (reads text via FileService → parseCsv → DataGrid) and the KB
 * read-only viewer (feeds raw text it already holds). Large files are capped to
 * keep the DOM light; the row/col count + cap notice are shown.
 */

import { pxToRem } from "../lib/pxToRem";

const DEFAULT_MAX_ROWS = 500; // preview cap — the byte editor (Edit) shows all

export function DataGrid({
  rows,
  maxRows = DEFAULT_MAX_ROWS,
  show,
  highlighted,
}: {
  rows: string[][];
  maxRows?: number;
  /** #847/#848 PR 5 — which body rows to draw (indices into `rows` after the
   * header), in order; all of them when omitted. A table on a marking passes
   * `useTableMarking`'s `shown`. */
  show?: readonly number[];
  /** Body rows drawn as marked (`data-marked`), by the same index. */
  highlighted?: ReadonlySet<number>;
}) {
  if (rows.length === 0) return <div style={{ color: "var(--text-paper-d)" }}>Empty file.</div>;

  const [header, ...body] = rows;
  const order = show ?? body.map((_, i) => i);
  const shown = order.slice(0, maxRows);
  const capped = order.length - shown.length;

  return (
    <div style={{ height: "100%", minHeight: 0, overflow: "auto" }}>
      <table className="csv-table" style={{ borderCollapse: "collapse", fontSize: pxToRem(12), width: "100%" }}>
        <thead>
          <tr>
            {header.map((h, i) => (
              <th key={i} style={cell(true)}>
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {shown.map((ri) => (
            <tr key={ri} data-marked={highlighted?.has(ri) ? "" : undefined}>
              {header.map((_, ci) => (
                <td key={ci} style={cell(false)}>
                  {body[ri]![ci] ?? ""}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      <div style={{ color: "var(--text-paper-d)", fontSize: pxToRem(12), padding: "6px 2px" }}>
        {body.length} rows × {header.length} columns
        {capped > 0 ? ` — showing first ${maxRows} (Edit to see all)` : ""}
      </div>
    </div>
  );
}

function cell(head: boolean): React.CSSProperties {
  return {
    border: "1px solid var(--paper-3)",
    padding: "3px 8px",
    textAlign: "left",
    whiteSpace: "nowrap",
    fontWeight: head ? 600 : 400,
    background: head ? "var(--paper-2, transparent)" : undefined,
    position: head ? "sticky" : undefined,
    top: head ? 0 : undefined,
  };
}
