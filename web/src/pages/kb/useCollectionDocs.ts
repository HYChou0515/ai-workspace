/**
 * useCollectionDocs — the #395 data layer for a collection's document list.
 *
 * The doc IDE's tree needs EVERY path, so the list is a fetch-all — but the
 * old shape (200-doc pages re-fetched wholesale every 1.5s while anything
 * indexed) was the "doc page is slow" bug. Now:
 *
 *   - the list arrives in ONE request (the BE row is metas-only and the cap
 *     allows 5000), fetched once;
 *   - while anything is indexing, a few-hundred-byte status summary is polled
 *     instead (`documentsStatus`): progress bars advance by merging its `runs`
 *     into the rows client-side, and the LIST is refetched only when the
 *     summary's change stamp (total/counts/latest_ms) actually moves;
 *   - consumers that only need the banner counts (the collection page's
 *     Cards/Wiki tabs) read `indexingCount` — the full-list poll is gone.
 */

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { type KbApi, type KbDocument, type KbDocumentsStatus, kbApi } from "../../api/kb";
import { qk } from "../../api/queryKeys";

/** One-request fetch of the whole collection (the tree needs every path).
 * The loop is a defensive fallback for collections past the BE's 5000 cap. */
export async function fetchAllDocs(
  client: Pick<KbApi, "listDocuments">,
  collectionId: string,
): Promise<KbDocument[]> {
  const out: KbDocument[] = [];
  const limit = 2000;
  for (let offset = 0; ; offset += limit) {
    const page = await client.listDocuments(collectionId, { offset, limit });
    out.push(...page.items);
    if (!page.has_more || page.items.length === 0) break;
  }
  return out;
}

/** The "did the list change?" digest of a status summary — counts keyed in a
 * stable order so two identical summaries always stringify identically. */
/** How long to keep polling after work was QUEUED but hasn't surfaced yet
 * (#569). Long enough for a worker on another pod to claim the job and flip the
 * first doc; short enough that a dropped run doesn't poll forever. */
const WATCH_FOR_QUEUED_MS = 30_000;

function stampOf(s: KbDocumentsStatus): string {
  const counts = Object.entries(s.counts)
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([k, v]) => `${k}=${v}`)
    .join(",");
  return `${s.total}|${s.latest_ms}|${counts}`;
}

export function useCollectionDocs(
  collectionId: string,
  client: KbApi = kbApi,
  opts: { enabled?: boolean } = {},
) {
  const enabled = opts.enabled ?? true;
  const qc = useQueryClient();
  const docsQuery = useQuery({
    queryKey: qk.kb.documents(collectionId),
    queryFn: () => fetchAllDocs(client, collectionId),
    enabled,
  });
  const baseDocs = docsQuery.data;
  // A listed row can say "indexing" before the first status tick lands (e.g.
  // right after an upload invalidated the list) — poll on either signal.
  const docsSayIndexing = useMemo(
    () => (baseDocs ?? []).some((d) => d.status === "indexing"),
    [baseDocs],
  );
  // #569: work can be QUEUED without anything looking busy yet. "Re-read all"
  // hands the walk to a worker, so when the request answers not one doc has
  // flipped to `indexing` — and `refetchInterval` is evaluated against the data
  // already in hand, so invalidating only buys ONE refetch: it sees a quiet
  // collection, returns false, and the poll never starts. The progress strip
  // then stays dead until the user navigates away. A caller that just queued
  // work arms this window instead; the poll runs through it and hands over to
  // the real counts as soon as the worker flips the first doc.
  const [watchUntil, setWatchUntil] = useState(0);
  const watchForQueuedWork = useCallback(
    () => setWatchUntil(Date.now() + WATCH_FOR_QUEUED_MS),
    [],
  );
  useEffect(() => setWatchUntil(0), [collectionId]);
  const statusQuery = useQuery({
    queryKey: qk.kb.documentsStatus(collectionId),
    queryFn: () => client.documentsStatus(collectionId),
    enabled,
    refetchInterval: (q) => {
      const s = q.state.data as KbDocumentsStatus | undefined;
      const busy = (s?.counts["indexing"] ?? 0) > 0 || docsSayIndexing;
      return busy || Date.now() < watchUntil ? 1500 : false;
    },
  });
  const status = statusQuery.data;

  // Refetch the list ONLY when the summary actually moved — a poll tick whose
  // stamp is unchanged proves the list is identical, so the tick costs a few
  // hundred bytes instead of the whole collection.
  const stamp = status ? stampOf(status) : null;
  const lastStamp = useRef<string | null>(null);
  useEffect(() => {
    lastStamp.current = null;
  }, [collectionId]);
  useEffect(() => {
    if (stamp == null) return;
    if (lastStamp.current != null && lastStamp.current !== stamp) {
      void qc.invalidateQueries({ queryKey: qk.kb.documents(collectionId) });
    }
    lastStamp.current = stamp;
  }, [stamp, qc, collectionId]);

  // Advance the progress bars from the poll without waiting for a list fetch.
  const docs = useMemo(() => {
    const base = baseDocs ?? [];
    const runs = status?.runs;
    if (!runs) return base;
    return base.map((d) => {
      const r = d.status === "indexing" ? runs[d.resource_id] : undefined;
      return r ? { ...d, units_done: r.units_done, units_total: r.units_total } : d;
    });
  }, [baseDocs, status?.runs]);

  const indexingCount =
    status?.counts["indexing"] ?? (baseDocs ?? []).filter((d) => d.status === "indexing").length;
  const shouldPoll = (status?.counts["indexing"] ?? 0) > 0 || docsSayIndexing;

  return { docs, docsQuery, status, indexingCount, shouldPoll, watchForQueuedWork };
}
