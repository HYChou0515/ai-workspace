/**
 * Image preview (png/jpg/jpeg/gif/svg/webp/bmp). Renders the BUFFER's current
 * bytes as a Blob URL (so an edit shows immediately); the Edit toggle flips to
 * the byte editor like every other file (#all-editable).
 */
import { useEffect, useMemo } from "react";

import { encodeText } from "../api/encoding";
import { useEditMode } from "../hooks/editMode";
import { useFileBuffer } from "../hooks/fileBuffer";
import { imageMime } from "../pages/investigation/renderer";
import { TextRenderer } from "./TextRenderer";

export function ImageRenderer({ path }: { path: string }) {
  const { isEditing } = useEditMode();
  const { entry } = useFileBuffer(path);
  const editing = isEditing(path);

  const url = useMemo(() => {
    if (entry.status !== "ready" || entry.kind !== "text") return null;
    const bytes = encodeText(entry.text, entry.encoding);
    return URL.createObjectURL(new Blob([bytes.buffer as ArrayBuffer], { type: imageMime(path) }));
  }, [entry.status, entry.kind, entry.text, entry.encoding, path]);

  useEffect(() => () => void (url && URL.revokeObjectURL(url)), [url]);

  if (editing) return <TextRenderer path={path} />;
  if (entry.status === "loading" || !url) {
    return <div style={{ color: "var(--text-paper-d)" }}>Loading {path}…</div>;
  }
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <img src={url} alt={path} style={{ maxWidth: "100%", borderRadius: "var(--radius-card)" }} />
    </div>
  );
}
