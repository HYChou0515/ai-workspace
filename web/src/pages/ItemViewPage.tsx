/**
 * The editor-area-only page, `/a/:slug/:itemId/view` (#847 Q5.3).
 *
 * Chat mode folds the workspace away, so a shown file or a `show_file(layout=…)`
 * card opens HERE in a new tab: the item's panes and views — the same
 * `EditorPanes` the workspace draws, inside the same providers — with no file
 * tree and no chat. Before this, chat mode linked the raw content URL, which
 * showed a `.ai.yaml` view as its YAML text.
 *
 * Whoever opens it must already be able to see the item: the address is a
 * shortcut, not a grant, and the API refuses exactly what it would refuse
 * inside the workspace.
 */
import { type ReactNode, useEffect, useMemo, useRef } from "react";
import { useParams, useSearchParams } from "react-router-dom";

import type { FileInfo } from "../api/types";
import { OpenFileProvider, OpenLayoutProvider, WorkspaceVisibleProvider } from "../hooks/openFile";
import { useEditorGroups } from "../hooks/useEditorGroups";
import { useFiles } from "../hooks/useInvestigation";
import { WorkspaceSlugProvider } from "../hooks/useWorkspaceSlug";
import type { FileBufferStore } from "../hooks/fileBuffer";
import { readViewPageTarget, type ViewPageTarget } from "../lib/viewPage";
import { EditorPanes, WorkspaceProviders } from "./investigation/WorkspaceShell";

export function ItemViewPage() {
  const { slug = "", itemId = "" } = useParams();
  const [search] = useSearchParams();
  const target = useMemo(() => readViewPageTarget(search), [search]);
  const id = decodeURIComponent(itemId);
  if (!target) {
    return <Msg>Nothing to show — this link names no file or layout.</Msg>;
  }
  return (
    <WorkspaceSlugProvider value={slug}>
      <Loaded slug={slug} id={id} target={target} />
    </WorkspaceSlugProvider>
  );
}

function Loaded({ slug, id, target }: { slug: string; id: string; target: ViewPageTarget }) {
  const files = useFiles(id);
  const items = files.kind === "ready" ? files.items : [];
  const unwalked = files.kind === "ready" ? files.unwalked : [];
  return (
    <WorkspaceProviders slug={slug} itemId={id} unwalked={unwalked}>
      {(bufferStore) => (
        <Panes id={id} target={target} files={items} bufferStore={bufferStore} />
      )}
    </WorkspaceProviders>
  );
}

function Panes({
  id,
  target,
  files,
  bufferStore,
}: {
  id: string;
  target: ViewPageTarget;
  files: FileInfo[];
  bufferStore: FileBufferStore;
}) {
  const groups = useEditorGroups("path" in target ? [target.path] : []);
  // The layout opens once, on arrival: afterwards the panes are the user's to
  // rearrange, and a re-render must not snap them back.
  const opened = useRef(false);
  useEffect(() => {
    if (opened.current || !("layout" in target)) return;
    opened.current = true;
    groups.openLayout(target.layout);
  }, [groups, target]);
  return (
    <OpenFileProvider value={groups.openInActive}>
      <OpenLayoutProvider value={groups.openLayout}>
        <WorkspaceVisibleProvider value>
          <div
            data-testid="page-item-view"
            // P13 — never wider than the window: at 390 px five panes' tab
            // strips pushed the page to 445 px and it scrolled sideways.
            style={{ height: "100vh", maxWidth: "100vw", overflow: "hidden", display: "flex", background: "var(--white)" }}
          >
            <EditorPanes
              groups={groups}
              investigationId={id}
              files={files}
              bufferStore={bufferStore}
            />
          </div>
        </WorkspaceVisibleProvider>
      </OpenLayoutProvider>
    </OpenFileProvider>
  );
}

function Msg({ children }: { children: ReactNode }) {
  return (
    <div
      data-testid="page-item-view"
      style={{
        height: "100vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: 40,
        color: "var(--text-paper-d)",
      }}
    >
      {children}
    </div>
  );
}
