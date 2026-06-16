import { useQuery } from "@tanstack/react-query";

import { useFileService } from "../api/fileService";
import { qk } from "../api/queryKeys";
import type { FileContent } from "../api/types";

type State =
  | { kind: "loading" }
  | { kind: "ready"; content: FileContent }
  | { kind: "error"; error: Error };

/**
 * Read a single file from the active `FileService`, cached under
 * `qk.file(scopeId, path)`. Disabled until a path is given. A write that
 * invalidates `qk.files(scopeId)` does not touch this key, so callers that edit
 * a file should invalidate `qk.file(scopeId, path)` too.
 */
export function useFileContent(path: string | null): State {
  const svc = useFileService();
  const q = useQuery({
    queryKey: qk.file(svc.scopeId, path ?? ""),
    queryFn: () => svc.readFile(path as string),
    enabled: path !== null,
  });
  if (q.isPending) return { kind: "loading" };
  if (q.isError) return { kind: "error", error: q.error };
  return { kind: "ready", content: q.data };
}
