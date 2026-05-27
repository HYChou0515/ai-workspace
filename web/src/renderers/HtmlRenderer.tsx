/**
 * HTML preview — renders the file in a sandboxed iframe (scripts/forms/popups
 * disabled, no same-origin), so static markup + CSS show safely. The Edit
 * toggle flips to the byte editor like every other file (#all-editable).
 */
import { useEditMode } from "../hooks/editMode";
import { useFileBuffer } from "../hooks/fileBuffer";
import { TextRenderer } from "./TextRenderer";

export function HtmlRenderer({ investigationId, path }: { investigationId: string; path: string }) {
  const { isEditing } = useEditMode();
  const { entry } = useFileBuffer(path);

  if (isEditing(path)) return <TextRenderer investigationId={investigationId} path={path} />;
  if (entry.status === "loading") {
    return <div style={{ color: "var(--text-paper-d)" }}>Loading {path}…</div>;
  }
  if (entry.status === "error") {
    return <div style={{ color: "var(--err)" }}>{entry.error ?? "load failed"}</div>;
  }
  return (
    <iframe
      title={path}
      // Empty sandbox: no scripts, no same-origin, no forms/popups — a safe
      // static preview of untrusted HTML.
      sandbox=""
      srcDoc={entry.text}
      style={{ width: "100%", height: "100%", minHeight: 0, border: 0, background: "#fff" }}
    />
  );
}
