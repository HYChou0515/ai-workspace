/**
 * The contents of the tree's lazy folders — the ones the preload listed but
 * did not enter (`unwalked`) — fetched one level at a time, only for the ones
 * the user has opened, and merged back into ONE listing so the filter, the
 * tab-presence rule and the breadcrumbs see loaded folders exactly like
 * preloaded ones.
 *
 * One query per opened lazy folder, keyed `qk.treeDir(scope, path)`, so:
 * - collapsing drops the observer and stops refetches; re-opening reads the
 *   cache instead of asking again;
 * - a prefix invalidation of `["treeDir", scope]` (turn end, `file_changed`)
 *   refetches every EXPANDED folder and leaves the collapsed ones for their
 *   next expand — TanStack only refetches active queries.
 *
 * A level's own subfolders arrive as `unwalked` too, so the pool of lazy
 * folders grows as levels load; the opened subset of that pool is what gets
 * queried. That pool is read back from the query cache rather than from this
 * render's results, because a folder can only be requested once its parent's
 * level has been seen — one render behind is exactly right.
 */

import { useQueries, useQueryClient } from "@tanstack/react-query";
import { useMemo, useRef } from "react";

import type { FileService, TreeListing } from "../../api/fileService";
import { qk } from "../../api/queryKeys";
import type { FileInfo } from "../../api/types";

export type LazyDirs = {
  /** Entries of every loaded lazy folder, to merge with the preload. */
  files: FileInfo[];
  dirs: string[];
  /** Lazy folders discovered INSIDE loaded levels (they nest). */
  unwalked: string[];
  /** Opened lazy folders whose level has not arrived yet. */
  loading: ReadonlySet<string>;
};

export function useLazyDirs(
  svc: FileService,
  unwalked: readonly string[],
  isOpen: (path: string) => boolean,
): LazyDirs {
  const qc = useQueryClient();
  const scope = svc.scopeId;
  // The pool: the preload's lazy folders plus every lazy folder any loaded
  // level reported. Cache-read, so a level that has arrived can offer its own
  // subfolders on the very next render.
  // Recomputed every render on purpose: a level that just arrived is what
  // re-rendered us, and its subfolders must be requestable on this render.
  const pool = new Set(unwalked);
  for (const [, data] of qc.getQueriesData<TreeListing>({ queryKey: ["treeDir", scope] })) {
    for (const p of data?.unwalked ?? []) pool.add(p);
  }
  const requested = [...pool].filter(isOpen);
  const results = useQueries({
    queries: requested.map((path) => ({
      queryKey: qk.treeDir(scope, path),
      queryFn: () => svc.listTree({ prefix: path, depth: 1 }),
    })),
  });
  // Stable identities: `data` is referentially stable across renders unless it
  // refetched, so the merged listing below only rebuilds when a level actually
  // changed — and the tree's own memo (built from it) keeps paying off.
  const levels = useStable(results.map((r) => r.data));
  const pending = useStable(requested.filter((_, i) => results[i]!.isPending));
  return useMemo(() => {
    const files: FileInfo[] = [];
    const dirs: string[] = [];
    const nested: string[] = [];
    for (const level of levels) {
      if (!level) continue;
      files.push(...level.items);
      dirs.push(...level.dirs);
      nested.push(...level.unwalked);
    }
    return { files, dirs, unwalked: nested, loading: new Set(pending) };
  }, [levels, pending]);
}

/** The same array instance for as long as its elements are the same. */
function useStable<T>(next: T[]): T[] {
  const ref = useRef(next);
  const same =
    ref.current.length === next.length && ref.current.every((v, i) => Object.is(v, next[i]));
  if (!same) ref.current = next;
  return ref.current;
}
