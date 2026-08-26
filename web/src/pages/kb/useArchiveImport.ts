/**
 * #715: drive one archive import that a WORKER is doing.
 *
 * The synchronous route wrote every document before it answered, so a large
 * archive died on a gateway timeout — with no resume, and with the caller
 * unable to see how far it got. The asynchronous route answers `202` with a run
 * id instead, which means the UI now owns two things the old flow never had:
 * the collection exists BEFORE the documents do, and there is progress to show.
 *
 * Polling stops the moment the run reports `finished`. `finished` is not the
 * same as "worked": a half-applied import finishes with `written < members` and
 * a reason per document in `errors`, which is exactly what the old path could
 * not tell anyone.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useState } from "react";

import type { ArchiveImport, KbApi } from "../../api/kb";
import { qk } from "../../api/queryKeys";

/** How often to ask, as the run gets older.
 *
 * A flat fast interval is wrong in both directions: an import runs for MINUTES,
 * so 800 ms is ~75 requests a minute for a number a person reads once every
 * few seconds — and nobody watches a counter for ten minutes anyway. But the
 * first few seconds are exactly when someone is still looking at the click they
 * just made, so starting slow makes the whole thing feel broken.
 *
 * So: responsive at first, then back off to a heartbeat. */
const POLL_FLOOR_MS = 1_000;
const POLL_CEILING_MS = 8_000;

function pollAfter(reads: number): number {
  return Math.min(POLL_CEILING_MS, POLL_FLOOR_MS * 2 ** Math.floor(reads / 3));
}

export interface ArchiveImportState {
  /** The run in flight, or the last one that finished. `null` before any. */
  run: ArchiveImport | null;
  /** True while a run is staged or being drained — the point at which the UI
   * shows progress and disables a second pick. */
  busy: boolean;
  /** Start an import that creates a NEW collection. `onStarted` fires once, with
   * the run — the caller needs the collection id to open it, and it does not
   * exist until the 202 comes back. */
  startNew: (file: File, onStarted?: (run: ArchiveImport) => void) => void;
  /** Start an import that merges INTO an existing collection. */
  startInto: (collectionId: string, file: File, mode: "overwrite" | "skip") => void;
  /** Dismiss a finished run's summary. */
  clear: () => void;
}

/**
 * Call this ONCE, in the KB shell.
 *
 * The import starts on one page and finishes on another, so whoever holds the
 * run id must outlive the navigation between them. Component state in either
 * page does not: the grid unmounts the moment it navigates. Putting the id in
 * the URL does not either — it makes "preserve this query param" a contract
 * every navigation in the KB surface has to honour, and the index redirect
 * broke it within the hour. So the state lives in the shell, which is mounted
 * for the whole surface, and reaches the pages through the outlet context.
 */
export function useArchiveImport(
  client: KbApi,
  opts: { onFinished?: (run: ArchiveImport) => void } = {},
): ArchiveImportState {
  const qc = useQueryClient();
  const [importId, setImportId] = useState<string | null>(null);
  const [started, setStarted] = useState<ArchiveImport | null>(null);
  const { onFinished } = opts;
  const [onStartedRef] = useState<{ fn?: (run: ArchiveImport) => void }>({});

  const begin = (run: ArchiveImport) => {
    setStarted(run);
    setImportId(run.import_id);
    onStartedRef.fn?.(run);
    onStartedRef.fn = undefined;
  };

  const newMut = useMutation({
    mutationFn: (file: File) => client.startImportNew(file),
    onSuccess: async (run) => {
      // AWAIT the refetch before handing the run over, because the caller's next
      // move is to open the collection and the collection page BOUNCES an id its
      // list does not contain. An invalidate alone does not make it contained: a
      // background refetch leaves `isPending` false, so the page reads stale data,
      // finds nothing, and redirects straight back to the grid — the import
      // appears to have done nothing at all.
      //
      // A failed refetch must not strand the run either, so navigation proceeds
      // regardless; the worst case is the bounce that used to happen always.
      await qc.invalidateQueries({ queryKey: qk.kb.collections }).catch(() => {});
      begin(run);
    },
  });

  const intoMut = useMutation({
    mutationFn: (v: { collectionId: string; file: File; mode: "overwrite" | "skip" }) =>
      client.startImportInto(v.collectionId, v.file, v.mode),
    onSuccess: begin,
  });

  const { data: polled } = useQuery({
    queryKey: qk.kb.archiveImport(importId ?? ""),
    queryFn: async () => {
      const run = await client.getImport(importId as string);
      if (run.finished) {
        // The documents only exist once the worker says so; refetching earlier
        // would show an empty collection and cache it.
        void qc.invalidateQueries({ queryKey: qk.kb.documents(run.collection_id) });
        void qc.invalidateQueries({ queryKey: qk.kb.collections });
        onFinished?.(run);
      }
      return run;
    },
    enabled: importId !== null,
    // Stop asking once it is done — a run that finished never changes again.
    refetchInterval: (q) =>
      q.state.data?.finished ? false : pollAfter(q.state.dataUpdateCount ?? 0),
  });

  // `started` covers the gap before the first poll answers, so the progress
  // appears on the same tick the upload is accepted rather than a beat later.
  const run = polled ?? (started?.import_id === importId ? started : null);
  const clear = useCallback(() => {
    setImportId(null);
    setStarted(null);
  }, []);

  return {
    run,
    busy: newMut.isPending || intoMut.isPending || (run !== null && !run.finished),
    startNew: (file, onStarted) => {
      onStartedRef.fn = onStarted;
      newMut.mutate(file);
    },
    startInto: (collectionId, file, mode) => intoMut.mutate({ collectionId, file, mode }),
    clear,
  };
}
