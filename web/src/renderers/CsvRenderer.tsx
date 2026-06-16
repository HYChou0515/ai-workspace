/**
 * Preview a CSV as a table (issue #19). Edit toggle flips to the byte editor
 * (TextRenderer) like every other file, so it stays #all-editable. Large files
 * are capped to keep the DOM light; the row count + cap are shown.
 */

import { useMemo } from "react";

import { useEditMode } from "../hooks/editMode";
import { useFileBuffer } from "../hooks/fileBuffer";
import { parseCsv } from "./csv";
import { TextRenderer } from "./TextRenderer";

const MAX_ROWS = 500; // preview cap — the byte editor (Edit) shows the whole file

export function CsvRenderer({ path }: { path: string }) {
  const { isEditing } = useEditMode();
  const { entry } = useFileBuffer(path);
  const editing = isEditing(path);

  const text = entry.status === "ready" ? entry.text : "";
  const rows = useMemo(() => parseCsv(text), [text]);

  if (editing) return <TextRenderer path={path} />;
  if (entry.status === "loading") {
    return <div style={{ color: "var(--text-paper-d)" }}>Loading {path}…</div>;
  }
  if (entry.status === "error") {
    return <div style={{ color: "var(--err)" }}>{entry.error ?? "load failed"}</div>;
  }
  if (rows.length === 0) return <div style={{ color: "var(--text-paper-d)" }}>Empty CSV.</div>;

  const [header, ...body] = rows;
  const shown = body.slice(0, MAX_ROWS);
  const capped = body.length - shown.length;

  return (
    <div style={{ height: "100%", minHeight: 0, overflow: "auto" }}>
      <table className="csv-table" style={{ borderCollapse: "collapse", fontSize: 12, width: "100%" }}>
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
      <div style={{ color: "var(--text-paper-d)", fontSize: 12, padding: "6px 2px" }}>
        {body.length} rows × {header.length} columns
        {capped > 0 ? ` — showing first ${MAX_ROWS} (Edit to see all)` : ""}
      </div>
    </div>
  );
}

function cell(head: boolean): React.CSSProperties {
  return {
    border: "1px solid var(--rule)",
    padding: "3px 8px",
    textAlign: "left",
    whiteSpace: "nowrap",
    fontWeight: head ? 600 : 400,
    background: head ? "var(--paper-2, transparent)" : undefined,
    position: head ? "sticky" : undefined,
    top: head ? 0 : undefined,
  };
}
