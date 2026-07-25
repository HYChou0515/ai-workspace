/**
 * SheetRenderer — `*.ai.csv` / `*.ai.tsv` as an editable spreadsheet
 * (docs/plan-ai-sheet.md Phase 1).
 *
 * Inverts the other structured previews: for json / jsonl / yaml / csv the
 * projection is READ-ONLY and `Edit` drops to the byte editor, whereas here the
 * grid itself is where you type. The byte editor stays reachable as the escape
 * hatch for a file too malformed for a grid, so "any file is editable" holds.
 *
 * The registry only hands a renderer `{ path }`, so this container resolves the
 * rest from context — the file buffer, and the member's write permission — and
 * hands plain rows to the pure `SheetGrid`. Edits go through the buffer's
 * `setText`, so the dirty marker, the unsaved indicator and the save shortcut
 * behave exactly as they do in Monaco; there is no bespoke save path here.
 */

import { useOptionalFileService } from "../api/fileService";
import { useFileBuffer } from "../hooks/fileBuffer";
import { useItemCanWrite } from "../hooks/useItemCanWrite";
import { useWorkspaceSlug } from "../hooks/useWorkspaceSlug";
import { parseCsv, serializeCsv } from "./csv";
import { SheetGrid } from "./SheetGrid";

/** `.tsv` is tab-separated; everything else routed here (`.csv`) is comma-separated.
 * Same rule as `CsvRenderer` and the backend parser (`kb/parsers/tabular.py`). */
export function delimiterFor(path: string): string {
  return path.toLowerCase().endsWith(".tsv") ? "\t" : ",";
}

export function SheetRenderer({ path }: { path: string }) {
  const { entry, setText, readOnly } = useFileBuffer(path);
  const slug = useWorkspaceSlug();
  // Optional: a grid can be mounted where no writable service exists (the
  // read-only FileTree select mode does the same), and then there is no item to
  // resolve a permission against.
  const itemId = useOptionalFileService()?.scopeId ?? "";
  const canWrite = useItemCanWrite(slug, itemId);

  if (entry.status === "loading") {
    return <div style={{ color: "var(--text-paper-d)" }}>Loading {path}…</div>;
  }
  if (entry.status === "error") {
    return <div style={{ color: "var(--err)" }}>{entry.error ?? "load failed"}</div>;
  }

  const delimiter = delimiterFor(path);
  const rows = parseCsv(entry.text, delimiter);

  return (
    <SheetGrid
      rows={rows}
      // Two independent reasons a cell can't be typed in: the path itself is
      // read-only (#205) or this member may not write the item (#455 §E).
      readOnly={readOnly || !canWrite}
      onCommitCell={(row, col, value) => {
        const updated = rows.map((r, ri) => (ri === row ? r.map((v, ci) => (ci === col ? value : v)) : r));
        // `entry.text` is passed so the file's line-ending and trailing-newline
        // style survive the round-trip (see `serializeCsv`).
        setText(serializeCsv(updated, delimiter, entry.text));
      }}
    />
  );
}
