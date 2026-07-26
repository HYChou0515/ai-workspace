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

import { useEffect, useRef, useState } from "react";

import { useOptionalFileService } from "../api/fileService";
import { useEditMode } from "../hooks/editMode";
import { useFileBuffer } from "../hooks/fileBuffer";
import { useItemCanWrite } from "../hooks/useItemCanWrite";
import { useOutsideFileChange } from "../hooks/useOutsideFileChange";
import { useWorkspaceSlug } from "../hooks/useWorkspaceSlug";
import { parseCsv, serializeCsv } from "./csv";
import { SheetGrid } from "./SheetGrid";
import { TextRenderer } from "./TextRenderer";

/** `.tsv` is tab-separated; everything else routed here (`.csv`) is comma-separated.
 * Same rule as `CsvRenderer` and the backend parser (`kb/parsers/tabular.py`). */
export function delimiterFor(path: string): string {
  return path.toLowerCase().endsWith(".tsv") ? "\t" : ",";
}

export function SheetRenderer({ path }: { path: string }) {
  const { isEditing } = useEditMode();
  const { entry, setText, readOnly, reload } = useFileBuffer(path);
  const [changedOutside, setChangedOutside] = useState(false);
  const slug = useWorkspaceSlug();
  // Optional: a grid can be mounted where no writable service exists (the
  // read-only FileTree select mode does the same), and then there is no item to
  // resolve a permission against.
  const itemId = useOptionalFileService()?.scopeId ?? "";
  const canWrite = useItemCanWrite(slug, itemId);

  // A peer or an agent wrote to this item. With nothing unsaved there is nothing
  // to lose, so pick up their change silently; with unsaved cells in flight,
  // say so and let the user decide — merging or discarding on their behalf would
  // both throw away work without asking.
  // Sheet-local history: snapshots of the file text this grid itself wrote.
  // `own` is the last text WE handed to the buffer, which is how a foreign
  // change is recognised.
  const history = useRef<{ past: string[]; future: string[]; own: string | null }>({ past: [], future: [], own: null });
  useEffect(() => {
    if (entry.status !== "ready" || history.current.own === entry.text) return;
    // The byte editor, a reload, a peer or the agent replaced the content. The
    // stack no longer describes this file, and a redo that reapplies a block
    // onto someone else's text is worse than having no redo at all.
    history.current = { past: [], future: [], own: entry.text };
  }, [entry.status, entry.text]);

  const write = (text: string) => {
    history.current.past.push(entry.text);
    history.current.future = [];
    history.current.own = text;
    setText(text);
  };
  const step = (from: "past" | "future", to: "past" | "future") => () => {
    const next = history.current[from].pop();
    if (next === undefined) return;
    history.current[to].push(entry.text);
    history.current.own = next;
    setText(next);
  };

  const dirty = entry.save === "dirty" || entry.save === "error";
  useOutsideFileChange(slug, itemId, () => {
    if (dirty) setChangedOutside(true);
    else reload();
  });

  // Edit hands the whole pane to the byte editor, like every other structured
  // preview (`StructuredPane`). Without this the registry's `editToggle` flag is
  // a lie: the button flips a switch nothing reads, so a file the grid can't
  // help with has no escape hatch at all.
  if (isEditing(path)) return <TextRenderer path={path} />;

  if (entry.status === "loading") {
    return <div style={{ color: "var(--text-paper-d)" }}>Loading {path}…</div>;
  }
  if (entry.status === "error") {
    return <div style={{ color: "var(--err)" }}>{entry.error ?? "load failed"}</div>;
  }

  // A grid over mojibake is worse than useless: it invites edits that would
  // re-encode the bytes on save. Degrade to the byte editor, and say why.
  if (entry.kind === "binary") {
    return (
      <div style={{ height: "100%", minHeight: 0, display: "flex", flexDirection: "column" }}>
        <div role="status" style={{ color: "var(--warn)", padding: "6px 2px" }}>
          This file is not text, so it can't be shown as a sheet — editing the bytes instead.
        </div>
        <div style={{ flex: 1, minHeight: 0 }}>
          <TextRenderer path={path} />
        </div>
      </div>
    );
  }

  const delimiter = delimiterFor(path);
  const rows = parseCsv(entry.text, delimiter);

  return (
    <div style={{ height: "100%", minHeight: 0, display: "flex", flexDirection: "column" }}>
      {changedOutside && (
        <div role="status" className="sheet-banner" style={{ display: "flex", gap: 8, alignItems: "center", padding: "6px 2px", color: "var(--warn)" }}>
          <span>This file changed outside the editor, and you have unsaved cells.</span>
          <button
            type="button"
            className="btn"
            data-variant="secondary"
            data-size="sm"
            onClick={() => {
              setChangedOutside(false);
              reload();
            }}
          >
            Reload and lose my changes
          </button>
          <button type="button" className="btn" data-variant="ghost" data-size="sm" onClick={() => setChangedOutside(false)}>
            Keep my changes
          </button>
        </div>
      )}
      <SheetGrid
        rows={rows}
        // Two independent reasons a cell can't be typed in: the path itself is
        // read-only (#205) or this member may not write the item (#455 §E).
        readOnly={readOnly || !canWrite}
        // `entry.text` is passed so the file's line-ending and trailing-newline
        // style survive the round-trip (see `serializeCsv`).
        onRowsChange={(next) => write(serializeCsv(next, delimiter, entry.text))}
        onUndo={step("past", "future")}
        onRedo={step("future", "past")}
      />
    </div>
  );
}
