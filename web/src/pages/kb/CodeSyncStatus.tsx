/**
 * #355: the code-collection sync strip on the collection page. Shown only for a
 * code collection (one with a `git_url`). Polls the wiki/build status (reusing
 * the #162 strip pattern + poll infra) and surfaces the whole sync lifecycle in
 * one line:
 *
 *  - building  → "Cloning repository…" / "Reading files…" / "Building wiki…"
 *  - failed    → "Sync failed: <reason>" + Retry
 *  - idle      → "Synced to <sha> · <when>" (or "Not synced yet") + Sync now
 *
 * "Sync now" / "Retry" POST /sync (an async enqueue); the poll picks the live
 * phase up as the job runs.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { type KbApi, type KbCollection } from "../../api/kb";
import { qk } from "../../api/queryKeys";
import { Icon } from "../../components/Icon";
import { fmtDate } from "./collectionFormat";

/** Friendly labels for the sync-specific phases (#355); anything else during a
 * build is the downstream code-wiki build, shown generically. */
const PHASE_LABEL: Record<string, string> = {
  cloning: "Cloning repository…",
  ingesting: "Reading files…",
};

export function CodeSyncStatus({
  collection,
  client,
}: {
  collection: KbCollection;
  client: KbApi;
}) {
  const qc = useQueryClient();
  const cid = collection.resource_id;

  const statusQuery = useQuery({
    queryKey: qk.kb.wikiStatus(cid),
    queryFn: () => client.getWikiStatus(cid),
    // Poll only while a build is in flight; stop once it settles.
    refetchInterval: (q) => (q.state.data?.building ? 1500 : false),
  });
  const status = statusQuery.data;
  const building = status?.building ?? false;
  const lastError = building ? null : (status?.last_error ?? null);

  const syncMut = useMutation({
    mutationFn: () => client.syncCollection(cid),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.kb.wikiStatus(cid) });
      void qc.invalidateQueries({ queryKey: qk.kb.collections });
    },
  });

  // A code collection is the only place this renders; the parent gates on git_url.
  if (!collection.git_url) return null;

  const syncedLabel = collection.git_last_sha
    ? `Synced to ${collection.git_last_sha.slice(0, 7)}${
        collection.git_last_pulled_at ? ` · ${fmtDate(collection.git_last_pulled_at)}` : ""
      }`
    : "Not synced yet";

  return (
    <div
      className={`kb-sync-status${lastError ? " is-error" : ""}`}
      data-testid="kb-sync-status"
      role="status"
    >
      {building ? (
        <span className="kb-sync-status__line">
          <Icon name="refresh" size={13} color="var(--accent-h)" />
          {PHASE_LABEL[status?.phase ?? ""] ?? "Building wiki…"}
        </span>
      ) : lastError ? (
        <>
          <span className="kb-sync-status__line">
            <Icon name="x" size={13} color="var(--err)" />
            Sync failed: {lastError}
          </span>
          <button
            type="button"
            className="kb-sync-status__btn"
            disabled={syncMut.isPending}
            onClick={() => syncMut.mutate()}
          >
            Retry
          </button>
        </>
      ) : (
        <>
          <span className="kb-sync-status__line">
            <Icon name="git" size={13} />
            {syncedLabel}
          </span>
          <button
            type="button"
            className="kb-sync-status__btn"
            disabled={syncMut.isPending}
            onClick={() => syncMut.mutate()}
          >
            Sync now
          </button>
        </>
      )}
    </div>
  );
}
