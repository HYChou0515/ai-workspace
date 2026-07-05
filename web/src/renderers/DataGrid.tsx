/**
 * DataGrid — a pure presentational table for delimited data (issue #361).
 * Takes already-parsed `rows` (row 0 = header) so it serves both the workspace
 * CsvRenderer (reads text via FileService → parseCsv → DataGrid) and the KB
 * read-only viewer (feeds raw text it already holds). Large files are capped to
 * keep the DOM light; the row/col count + cap notice are shown.
 */

import { pxToRem } from "../lib/pxToRem";

const DEFAULT_MAX_ROWS = 500; // preview cap — the byte editor (Edit) shows all

export function DataGrid({ rows, maxRows = DEFAULT_MAX_ROWS }: { rows: string[][]; maxRows?: number }) {
  if (rows.length === 0) return <div style={{ color: "var(--text-paper-d)" }}>Empty file.</div>;

  const [header, ...body] = rows;
  const shown = body.slice(0, maxRows);
  const capped = body.length - shown.length;

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
          {shown.map((r, ri) => (
            <tr key={ri}>
              {header.map((_, ci) => (
                <td key={ci} style={cell(false)}>
                  {r[ci] ?? ""}
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
