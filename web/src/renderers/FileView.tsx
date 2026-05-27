/**
 * Dispatch a file path to its renderer (markdown / notebook / report / …).
 * Renderers that aren't shipped yet fall through to a "coming soon" stub.
 */

import { useEffect, useMemo } from "react";

import { encodeText } from "../api/encoding";
import { useEditMode } from "../hooks/editMode";
import { useFileBuffer } from "../hooks/fileBuffer";
import { imageMime, pickRenderer } from "../pages/investigation/renderer";
import { CsvRenderer } from "./CsvRenderer";
import { FishboneRenderer } from "./fishbone/FishboneRenderer";
import { MarkdownRenderer } from "./MarkdownRenderer";
import { NotebookRenderer } from "./notebook/NotebookRenderer";
import { ReportRenderer } from "./report/ReportRenderer";
import { TextRenderer } from "./TextRenderer";

export function FileView({
  investigationId,
  path,
}: {
  investigationId: string;
  path: string;
}) {
  const kind = pickRenderer(path);
  switch (kind) {
    case "markdown":
      return <MarkdownRenderer investigationId={investigationId} path={path} />;
    case "notebook":
      return <NotebookRenderer investigationId={investigationId} path={path} />;
    case "report":
      return <ReportRenderer investigationId={investigationId} path={path} />;
    case "fishbone":
      return <FishboneRenderer investigationId={investigationId} path={path} />;
    case "csv":
      return <CsvRenderer investigationId={investigationId} path={path} />;
    case "json":
    case "text":
      return <TextRenderer investigationId={investigationId} path={path} />;
    case "image":
      return <ImageView investigationId={investigationId} path={path} />;
  }
}

function ImageView({
  investigationId,
  path,
}: {
  investigationId: string;
  path: string;
}) {
  // Preview by default; the tab strip's Edit toggle flips to the byte editor
  // so even an image can be edited (#all-editable). The preview renders the
  // BUFFER's current bytes (not a cached server URL), so an edit shows up
  // immediately and a no-longer-valid image reads as broken, not stale.
  const { isEditing } = useEditMode();
  const { entry } = useFileBuffer(path);
  const editing = isEditing(path);

  const url = useMemo(() => {
    if (entry.status !== "ready" || entry.kind !== "text") return null;
    const bytes = encodeText(entry.text, entry.encoding);
    return URL.createObjectURL(
      new Blob([bytes.buffer as ArrayBuffer], { type: imageMime(path) }),
    );
  }, [entry.status, entry.kind, entry.text, entry.encoding, path]);

  useEffect(() => () => void (url && URL.revokeObjectURL(url)), [url]);

  if (editing) return <TextRenderer investigationId={investigationId} path={path} />;
  if (entry.status === "loading" || !url) {
    return <div style={{ color: "var(--text-paper-d)" }}>Loading {path}…</div>;
  }
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <img
        src={url}
        alt={path}
        style={{ maxWidth: "100%", borderRadius: "var(--radius-card)" }}
      />
    </div>
  );
}
