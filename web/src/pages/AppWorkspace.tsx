/**
 * App item workspace (`/a/:slug/:itemId`) — #89 P7 (first cut).
 *
 * Loads the item via the App's `resource_route`, then feeds the existing
 * `WorkspaceShell`. Opening the item no longer waits on (or fetches) the file
 * tree: the chat renders immediately and files are loaded lazily, only when the
 * file IDE is open. This keeps a chat-first item instant to open AND avoids the
 * file-tree fetch that warms the sandbox on the backend (so merely clicking into
 * a chat never spins one up). The id in the URL is a slash-bearing specstar
 * resource id → decoded here.
 */

import { useQueryClient } from "@tanstack/react-query";
import { type ReactNode, useEffect, useRef } from "react";
import { useParams } from "react-router-dom";

import { qk } from "../api/queryKeys";
import type { AppItem, AppManifest } from "../api/types";
import { ChatListRail } from "../components/ChatListRail";
import { useFiles } from "../hooks/useInvestigation";
import { usePersistentBoolean } from "../hooks/usePersistentBoolean";
import { useNavChrome } from "../hooks/useNavChrome";
import { useAppItem, useAppManifest } from "../hooks/useResources";
import { WorkspaceSlugProvider } from "../hooks/useWorkspaceSlug";
import { rememberLastChat } from "../lib/lastChat";
import { initialIdeCollapsed, WorkspaceShell } from "./investigation/WorkspaceShell";

export function AppWorkspace() {
  // #95: the workspace routes nest under /a/{slug}/... — provide the slug
  // (from the URL, available immediately) so `useFiles` here AND the shell's
  // hooks all build the right paths.
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
  // Wait only for the item + its manifest (cheap specstar reads) — NOT the files.
  // Once they're here, `WorkspaceLoaded` can derive the IDE-collapse default from
  // the manifest before deciding whether to fetch files at all.
  if (!manifest || !item) {
    return <Msg>Loading…</Msg>;
  }
  return <WorkspaceLoaded slug={slug} id={id} manifest={manifest} item={item} />;
}

function WorkspaceLoaded({
  slug,
  id,
  manifest,
  item,
}: {
  slug: string;
  id: string;
  manifest: AppManifest;
  item: AppItem;
}) {
  const queryClient = useQueryClient();
  // The file IDE's collapse state is lifted here so file loading can follow it:
  // a chat-first item opens collapsed and never fetches files (no sandbox warm on
  // open); expanding the IDE enables the fetch. Persisted per-App.
  const [ideCollapsed, setIdeCollapsed] = usePersistentBoolean(
    `layout:ide-collapsed:${slug}`,
    initialIdeCollapsed(manifest),
  );
  const files = useFiles(id, { enabled: !ideCollapsed });
  const chatFirst = manifest.layout.primary_surface === "chat";
  // Remember this as the App's last-opened chat, so re-entering the App resumes
  // it (per-App, not one global chat).
  useEffect(() => {
    rememberLastChat(slug, id);
  }, [slug, id]);
  // A chat-first workspace hides the platform top bar (its overview lives in the
  // rail menu) so the chat surface is clean; restored when leaving.
  const { setHidden } = useNavChrome();
  useEffect(() => {
    setHidden(chatFirst);
    return () => setHidden(false);
  }, [chatFirst, setHidden]);
  // Only the FIRST paint waits on files, so an IDE-first item's editor still
  // opens its default tabs on entry. Once mounted, a later fetch (e.g. expanding
  // the IDE on a chat-first item) must not unmount the shell and flash "Loading…".
  const mounted = useRef(false);

  // #538: anything that changes the file list changes how full the workspace is,
  // so the usage bar refreshes on the same signal.
  const onFilesChanged = () => {
    if (files.kind === "ready" || files.kind === "error") files.refresh();
    queryClient.invalidateQueries({ queryKey: qk.workspaceUsage(slug, id) });
  };

  if (files.kind === "loading" && !mounted.current) {
    return <Msg>Loading…</Msg>;
  }
  if (files.kind === "error" && !mounted.current) {
    return <Msg tone="err">{files.error.message}</Msg>;
  }
  mounted.current = true;

  const items = files.kind === "ready" ? files.items : [];
  const dirs = files.kind === "ready" ? files.dirs : [];
  const shell = (
    <WorkspaceShell
      item={item}
      manifest={manifest}
      files={items}
      dirs={dirs}
      ideCollapsed={ideCollapsed}
      onIdeCollapsedChange={setIdeCollapsed}
      onFilesChanged={onFilesChanged}
      onInvestigationChanged={() => {}}
    />
  );
  // Chat-first Apps get the private-chat framing: a left rail of the user's own
  // chats beside the chat itself (manifest-gated, so it stays App-agnostic).
  // Other surfaces (IDE-first / views-first) keep the plain workspace.
  if (!chatFirst) {
    return shell;
  }
  return (
    <div className="chat-workspace">
      <ChatListRail slug={slug} resourceRoute={manifest.resource_route} currentId={id} />
      <div className="chat-workspace__main">{shell}</div>
    </div>
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
