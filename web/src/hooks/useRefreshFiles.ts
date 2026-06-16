import { useQueryClient } from "@tanstack/react-query";
import { useCallback } from "react";

import { api } from "../api";
import { qk } from "../api/queryKeys";
import { useFileBufferStore } from "./fileBuffer";
import { useWorkspaceSlug } from "./useWorkspaceSlug";

/**
 * Full refresh of an investigation's file state — fixes #31's two faces of
 * sandbox/cache drift:
 *
 *   1. **Server snapshot may be behind the live sandbox.** Throttled flush,
 *      out-of-band sandbox mutation, slow disk. We POST `/files/refresh`
 *      first so the snapshot the FE reads is the truth.
 *   2. **The FE has two independent caches that BOTH need busting.**
 *        - TanStack Query: `qk.files`, `qk.dirs`, and every `qk.file(id, *)`
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
  const queryClient = useQueryClient();
  const buffers = useFileBufferStore();
  return useCallback(async () => {
    // 1. Sandbox → snapshot. Tolerate failure (a stale snapshot is still
    //    better than nothing; the invalidations below still help).
    try {
      await api.refreshFiles(slug, investigationId);
    } catch {
      /* ignore */
    }
    // 2. Drop both TanStack caches: the file list + dirs + every open file's
    //    content. Prefix invalidation on `["file", id]` covers every
    //    `qk.file(id, *)` query — opened-file readers refetch on next render.
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: qk.files(investigationId) }),
      queryClient.invalidateQueries({ queryKey: qk.dirs(investigationId) }),
      queryClient.invalidateQueries({ queryKey: ["file", investigationId] }),
    ]);
    // 3. Reload the editor's per-path buffers. Skip dirty ones — `reload()`
    //    would silently clobber the user's unsaved edits.
    for (const path of buffers.bufferedPaths()) {
      if (!buffers.isDirty(path)) buffers.reload(path);
    }
  }, [slug, investigationId, queryClient, buffers]);
}
