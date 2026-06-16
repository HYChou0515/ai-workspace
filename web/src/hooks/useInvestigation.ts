import { useQuery } from "@tanstack/react-query";

import { api } from "../api";
import { qk } from "../api/queryKeys";
import type { FileInfo } from "../api/types";
import { useWorkspaceSlug } from "./useWorkspaceSlug";

/* --------------------------- files list ---------------------------- */

type FilesState =
  | { kind: "loading" }
  | { kind: "ready"; items: FileInfo[]; dirs: string[]; refresh: () => void }
  | { kind: "error"; error: Error; refresh: () => void };

export function useFiles(investigationId: string): FilesState {
  const slug = useWorkspaceSlug();
  const q = useQuery({
    queryKey: qk.files(investigationId),
    queryFn: async () => {
      const [items, dirs] = await Promise.all([
        api.listFiles(slug, investigationId),
        api.listDirs(slug, investigationId),
      ]);
      return { items, dirs };
    },
  });
  const refresh = () => {
    void q.refetch();
  };
  if (q.isPending) return { kind: "loading" };
  if (q.isError) return { kind: "error", error: q.error, refresh };
  return { kind: "ready", items: q.data.items, dirs: q.data.dirs, refresh };
}
