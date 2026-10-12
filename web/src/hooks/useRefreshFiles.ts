import { useQueryClient } from "@tanstack/react-query";
import { useCallback } from "react";

import { api } from "../api";
import { useFileBufferStore } from "./fileBuffer";
import { invalidateTree } from "./invalidateTree";
import { useWorkspaceSlug } from "./useWorkspaceSlug";

/**
 * Full refresh of an investigation's file state — fixes #31's two faces of
 * sandbox/cache drift:
 *
 *   1. **Server snapshot may be behind the live sandbox.** Throttled flush,
 *      out-of-band sandbox mutation, slow disk. We POST `/files/refresh`
 *      first so the snapshot the FE reads is the truth.
 *   2. **The FE has two independent caches that BOTH need busting.**
 *        - TanStack Query: `qk.files` (files AND folders — one key since the
 *          two listings became one traversal) and every `qk.file(id, *)`
 *          opened from the read-only viewer. Invalidating the list alone
 *          (the old refresh path) leaves open files showing old content.
 *        - `FileBufferStore`: the editor's per-path `Map<path, BufferEntry>`.
 *          Independent of TanStack — has to be reloaded directly.
 *
 * Call this from: the refresh button, agent-turn end, terminal exec end.
 * Must be invoked inside a `<FileBufferProvider>` (uses `useFileBufferStore`).
 */
export function useRefreshFiles(investigationId: string): () => Promise<void> {
  const slug = useWorkspaceSlug();
  const reload = useReloadFileViews(investigationId);
  return useCallback(async () => {
    // 1. Sandbox → snapshot. Tolerate failure (a stale snapshot is still
    //    better than nothing; the reload below still helps).
    try {
      await api.refreshFiles(slug, investigationId);
    } catch {
      /* ignore */
    }
    await reload();
  }, [slug, investigationId, reload]);
}

/**
 * Steps 2 and 3 of `useRefreshFiles` — re-read what the panes show, without
 * asking the server to flush first.
 *
 * What a turn's end uses: the app chat's turn already flushed on the server
 * before it announced, and the tree and file reads go to the live sandbox when
 * one is up (#345). A viewer's own flush there is a whole-workspace write-back
 * WITH deletions, once per viewer per turn — and with turns ending in other
 * chats or a workflow's lanes, often in the middle of someone else's turn.
 */
export function useReloadFileViews(investigationId: string): () => Promise<void> {
  const queryClient = useQueryClient();
  const buffers = useFileBufferStore();
  return useCallback(async () => {
    // 2. Drop both TanStack caches: the file list + dirs + every open file's
    //    content. Prefix invalidation on `["file", id]` covers every
    //    `qk.file(id, *)` query — opened-file readers refetch on next render.
    await Promise.all([
      // The tree: the pruned preload AND the lazily-listed folders that are
      // open — one event, one helper, shared with the chat's `file_changed`.
      invalidateTree(queryClient, investigationId),
      queryClient.invalidateQueries({ queryKey: ["file", investigationId] }),
      // A WUI folder's manifest (`package.json`) is a file the WUI pane itself
      // acts on — whether it offers Rebuild, whether Deploy builds — and the
      // writer that scaffolds or removes one is the agent, whose tool writes
      // reach the FE through THIS chokepoint and not the `file_changed` bus.
      // (`WuiView` listens on the bus too, for a human's save; one rule, two
      // doors.) Without this the toolbar kept its opening answer until the
      // tab was reloaded.
      queryClient.invalidateQueries({ queryKey: ["wuiBuildable", investigationId] }),
    ]);
    // 3. Reload the editor's per-path buffers. Skip dirty ones — `reload()`
    //    would silently clobber the user's unsaved edits.
    for (const path of buffers.bufferedPaths()) {
      if (!buffers.isDirty(path)) buffers.reload(path);
    }
  }, [investigationId, queryClient, buffers]);
}
