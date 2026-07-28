import { useQuery } from "@tanstack/react-query";

import { api } from "../api";
import { qk } from "../api/queryKeys";
import type { FileInfo } from "../api/types";
import { useWorkspaceSlug } from "./useWorkspaceSlug";

/* --------------------------- files list ---------------------------- */

type FilesState =
  // `idle` = not fetching (the file IDE is collapsed) — distinct from `loading`
  // so the workspace can mount the chat immediately instead of waiting on files
  // (and so opening a chat-first item never touches the file endpoints, which is
  // what warms the sandbox on the backend).
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "ready"; items: FileInfo[]; dirs: string[]; refresh: () => void }
  | { kind: "error"; error: Error; refresh: () => void };

export function useFiles(investigationId: string, opts?: { enabled?: boolean }): FilesState {
  const enabled = opts?.enabled ?? true;
  const slug = useWorkspaceSlug();
  const q = useQuery({
    queryKey: qk.files(investigationId),
    queryFn: async () => {
      // One request, one workspace traversal. These were two endpoints fetched
      // in parallel, and each walked the whole workspace to answer half of the
      // same question — expensive warm, where the walk crosses the network.
      const { files, dirs } = await api.getTree(slug, investigationId);
      return { items: files, dirs };
    },
    enabled,
  });
  const refresh = () => {
    void q.refetch();
  };
  if (q.isError) return { kind: "error", error: q.error, refresh };
  if (q.data) return { kind: "ready", items: q.data.items, dirs: q.data.dirs, refresh };
  // With `enabled:false` a pending query never fetches (fetchStatus "idle"); only
  // an actually-in-flight fetch is `loading`. Otherwise we'd strand the page on
  // the loading gate forever.
  if (q.isLoading) return { kind: "loading" };
  return { kind: "idle" };
}
