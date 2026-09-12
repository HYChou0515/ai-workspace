import type { QueryClient } from "@tanstack/react-query";

import { qk } from "../api/queryKeys";

/**
 * Stale the whole file tree for one scope: the pruned preload (`qk.files`)
 * AND every lazily-listed folder (`["treeDir", id, *]`). The two live under
 * different keys because they are fetched by different calls, but "the tree
 * changed" is one event — a turn ended, a human saved — and the two doors
 * that report it (`useRefreshFiles`, the chat's `file_changed`) must not
 * each remember half of it.
 *
 * Only the lazy folders on screen refetch: a collapsed one has no observer,
 * so it waits for its next expand (TanStack refetches active queries only).
 * That is what keeps a turn end from re-listing `node_modules/` every time.
 */
export function invalidateTree(qc: QueryClient, scopeId: string): Promise<void> {
  return Promise.all([
    qc.invalidateQueries({ queryKey: qk.files(scopeId) }),
    qc.invalidateQueries({ queryKey: qk.treeDirs(scopeId) }),
  ]).then(() => undefined);
}
