/**
 * #455 P2 — live-sync an open entity view (board / table / gantt / health) with a
 * collaborator's or an AI agent's writes. Subscribes to the item's broadcast
 * `/stream` and, on a `file_changed` event (which the backend now raises for every
 * entity record write, human or agent), invalidates the item's entity queries so
 * the open view refetches the peer's change.
 *
 * It invalidates the whole `["entities", slug, itemId]` prefix (catalog + every
 * type's list + health) on ANY file_changed rather than parsing the path: a schema
 * edit should refresh the catalog, a record write the lists + health, and the
 * queries are cheap — so an occasional refetch from an unrelated file save is a
 * fine trade for never missing a change or shipping a fragile path heuristic.
 *
 * Scoped to when an entity view is mounted (its own subscription, like `useAgent`)
 * so liveness holds even when the chat panel isn't open. Broadcast is per-pod;
 * cross-pod convergence is out of scope (#202 / #349), same as the rest of SSE.
 */

import { useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";

import { api } from "../api";

export function useEntityLiveSync(slug: string, itemId: string, enabled = true): void {
  const qc = useQueryClient();
  useEffect(() => {
    if (!enabled || !itemId) return;
    const controller = new AbortController();
    void (async () => {
      try {
        for await (const ev of api.subscribeInvestigation(slug, itemId, controller.signal)) {
          if (ev.type === "file_changed") {
            void qc.invalidateQueries({ queryKey: ["entities", slug, itemId] });
          }
        }
      } catch (err: unknown) {
        // Torn down on unmount / item switch via controller.abort() — swallow it.
        if ((err as { name?: string } | null)?.name === "AbortError") return;
      }
    })();
    return () => controller.abort();
  }, [slug, itemId, enabled, qc]);
}
