/**
 * PDF preview — renders the BUFFER's current bytes as an `application/pdf` Blob
 * URL inside an <iframe> (the browser's native PDF viewer), the same way the
 * doc viewer iframes `/blobs/{id}`. Without this a .pdf fell through to the
 * catch-all text editor, which dumped the raw bytes as mojibake (#117). The
 * Edit toggle flips to the byte editor like every other file (#all-editable).
 */
import { useEffect, useMemo } from "react";

import { encodeText } from "../api/encoding";
import { useEditMode } from "../hooks/editMode";
import { useFileBuffer } from "../hooks/fileBuffer";
import { TextRenderer } from "./TextRenderer";

export function PdfRenderer({ path }: { path: string }) {
  const { isEditing } = useEditMode();
  const { entry } = useFileBuffer(path);
  const editing = isEditing(path);

  const url = useMemo(() => {
    if (entry.status !== "ready" || entry.kind !== "text") return null;
    const bytes = encodeText(entry.text, entry.encoding);
    return URL.createObjectURL(
      new Blob([bytes.buffer as ArrayBuffer], { type: "application/pdf" }),
    );
  }, [entry.status, entry.kind, entry.text, entry.encoding]);

  useEffect(() => () => void (url && URL.revokeObjectURL(url)), [url]);

  if (editing) return <TextRenderer path={path} />;
  if (entry.status === "loading" || !url) {
    return <div style={{ color: "var(--text-paper-d)" }}>Loading {path}…</div>;
  }
  return (
    <iframe
      title={path}
      src={url}
      style={{ width: "100%", height: "100%", minHeight: 0, border: 0, background: "#fff" }}
    />
  );
}
