/**
 * Dispatch a file path to its renderer (markdown / notebook / report / …).
 * Renderers that aren't shipped yet fall through to a "coming soon" stub.
 */

import { pickRenderer } from "../pages/investigation/renderer";
import { FishboneRenderer } from "./fishbone/FishboneRenderer";
import { MarkdownRenderer } from "./MarkdownRenderer";
import { NotebookRenderer } from "./notebook/NotebookRenderer";
import { ReportRenderer } from "./report/ReportRenderer";

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
    case "image":
    case "text":
      return <NotYet path={path} kind={kind} />;
  }
}

function NotYet({ path, kind }: { path: string; kind: string }) {
  return (
    <div style={{ color: "var(--text-paper-d)" }}>
      <div className="caps">Renderer pending — {kind}</div>
      <div style={{ marginTop: 8, fontSize: "var(--text-body)", color: "var(--text-paper)" }}>
        <code style={{ fontFamily: "var(--font-mono)" }}>{path}</code>
      </div>
      <div style={{ marginTop: 6, fontSize: 13 }}>
        Lands in a later step (§12.8 notebook · §12.9 report version selector ·
        §12.10 fishbone).
      </div>
    </div>
  );
}
