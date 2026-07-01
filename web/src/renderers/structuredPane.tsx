/**
 * StructuredPane — shared plumbing for the structured-data previews (#361:
 * json / jsonl / yaml / csv). Editing flips to the byte editor so every file
 * stays #all-editable (the tree/grid is a read-only projection); loading and
 * error states mirror the other renderers. The `render` prop receives the ready
 * file text and returns the projection.
 */

import type { ReactNode } from "react";

import { useEditMode } from "../hooks/editMode";
import { useFileBuffer } from "../hooks/fileBuffer";
import { TextRenderer } from "./TextRenderer";

export function StructuredPane({ path, render }: { path: string; render: (text: string) => ReactNode }) {
  const { isEditing } = useEditMode();
  const { entry } = useFileBuffer(path);

  if (isEditing(path)) return <TextRenderer path={path} />;
  if (entry.status === "loading") {
    return <div style={{ color: "var(--text-paper-d)" }}>Loading {path}…</div>;
  }
  if (entry.status === "error") {
    return <div style={{ color: "var(--err)" }}>{entry.error ?? "load failed"}</div>;
  }
  return <>{render(entry.text)}</>;
}
