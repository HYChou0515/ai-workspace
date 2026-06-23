/**
 * App item workspace (`/a/:slug/:itemId`) — #89 P7 (first cut).
 *
 * Loads the item via the App's `resource_route` + its files, then feeds the
 * existing `InvestigationShell`. The id in the URL is a slash-bearing specstar
 * resource id → decoded here. The turn / file / sandbox machinery is already
 * item-id-keyed (backend P2/P4d), so the shell's chat + file ops work for a new
 * per-App item unchanged.
 *
 * Thin-wrapper limits (P7 remainder / P8): the shell still renders RCA's
 * severity/status/product hardcoded (not `layout`-driven), and its model picker
 * reads `attached_agent_config_id` — we map it from `attached_preset` here so
 * turns resolve right (the backend already routes new items through AppCatalog).
 */

import type { ReactNode } from "react";
import { useParams } from "react-router-dom";

import { useFiles } from "../hooks/useInvestigation";
import { useAppItem, useAppManifest } from "../hooks/useResources";
import { WorkspaceSlugProvider } from "../hooks/useWorkspaceSlug";
import { WorkspaceShell } from "./investigation/WorkspaceShell";

export function AppWorkspace() {
  // #95: the workspace routes nest under /a/{slug}/... — provide the slug
  // (from the URL, available immediately) so `useFiles` here AND the shell's
  // hooks all build the right paths. (Without this, useFiles ran with an empty
  // slug → GET /a//items/{id}/files → 404 → the SPA's index.html → a JSON
  // parse error "Unexpected token '<'".)
  const { slug = "", itemId = "" } = useParams();
  return (
    <WorkspaceSlugProvider value={slug}>
      <AppWorkspaceInner slug={slug} itemId={itemId} />
    </WorkspaceSlugProvider>
  );
}

function AppWorkspaceInner({ slug, itemId }: { slug: string; itemId: string }) {
  const id = decodeURIComponent(itemId);
  const manifest = useAppManifest(slug);
  const item = useAppItem(slug, manifest?.resource_route, id);
  const files = useFiles(id);

  if (!manifest || !item || files.kind === "loading") {
    return <Msg>Loading…</Msg>;
  }
  if (files.kind === "error") {
    return <Msg tone="err">{files.error.message}</Msg>;
  }

  return (
    <WorkspaceShell
      item={item}
      manifest={manifest}
      files={files.items}
      dirs={files.dirs}
      onFilesChanged={files.refresh}
      onInvestigationChanged={() => {}}
    />
  );
}

function Msg({ children, tone = "muted" }: { children: ReactNode; tone?: "muted" | "err" }) {
  return (
    <div
      data-testid="page-app-workspace"
      style={{
        height: "100%",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: 40,
        color: tone === "err" ? "var(--err)" : "var(--text-paper-d)",
      }}
    >
      {children}
    </div>
  );
}
