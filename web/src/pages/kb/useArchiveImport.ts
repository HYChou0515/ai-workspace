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

/** How often to ask. Fast enough that a small archive looks immediate, slow
 * enough that a 4000-document one does not spend the whole import polling. */
const POLL_MS = 800;

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
    onSuccess: (run) => {
      // The collection row already exists — list it now, empty and filling,
      // rather than after the documents land.
      void qc.invalidateQueries({ queryKey: qk.kb.collections });
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
    refetchInterval: (q) => (q.state.data?.finished ? false : POLL_MS),
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
