import { useQuery } from "@tanstack/react-query";

import { api } from "../api";
import { qk } from "../api/queryKeys";
import type { FileContent } from "../api/types";

type State =
  | { kind: "loading" }
  | { kind: "ready"; content: FileContent }
  | { kind: "error"; error: Error };

/**
 * Read a single file, cached under `qk.file(id, path)`. Disabled until a path
 * is given. A write that invalidates `qk.files(id)` does not touch this key,
 * so callers that edit a file should invalidate `qk.file(id, path)` too.
 */
export function useFileContent(investigationId: string, path: string | null): State {
  const q = useQuery({
    queryKey: qk.file(investigationId, path ?? ""),
    queryFn: () => api.readFile(investigationId, path as string),
    enabled: path !== null,
  });
  if (q.isPending) return { kind: "loading" };
  if (q.isError) return { kind: "error", error: q.error };
  return { kind: "ready", content: q.data };
}
