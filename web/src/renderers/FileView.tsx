/**
 * Dispatch a file path to its renderer (markdown / notebook / report / …).
 * Renderers that aren't shipped yet fall through to a "coming soon" stub.
 */

import { useEditMode } from "../hooks/editMode";
import { pickRenderer } from "../pages/investigation/renderer";
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
  // Preview by default; the tab strip's Edit toggle flips to the byte
  // editor so even an image can be opened and edited (#all-editable).
  const { isEditing } = useEditMode();
  if (isEditing(path)) return <TextRenderer investigationId={investigationId} path={path} />;

  // Served straight off the read endpoint; the browser fetches the bytes.
  const src = `/investigations/${encodeURIComponent(investigationId)}/files/${path
    .split("/")
    .map(encodeURIComponent)
    .join("/")}`;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <img
        src={src}
        alt={path}
        style={{ maxWidth: "100%", borderRadius: "var(--radius-card)" }}
      />
    </div>
  );
}
