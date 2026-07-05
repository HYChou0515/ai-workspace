/**
 * #455 P4 — the live roster of who else has this item open. Subscribes to the
 * item's broadcast `/stream` and tracks the latest `presence` event the backend
 * emits whenever a viewer joins or leaves. Its own subscription (like `useAgent`
 * / `useEntityLiveSync`) so the roster is live wherever it's mounted; the backend
 * dedupes a user's multiple tabs into one entry.
 *
 * Per-pod + ephemeral, consistent with the SSE broadcast — a viewer on another
 * pod isn't counted (cross-pod convergence is out of scope, #202 / #349).
 */

import { useEffect, useState } from "react";

import { api } from "../api";

export function useItemPresence(slug: string, itemId: string): string[] {
  const [roster, setRoster] = useState<string[]>([]);
  useEffect(() => {
    setRoster([]); // drop any prior item's roster while the new one loads
    if (!itemId) return;
    const controller = new AbortController();
    void (async () => {
      try {
        for await (const ev of api.subscribeInvestigation(slug, itemId, controller.signal)) {
          if (ev.type === "presence") setRoster(ev.users);
        }
      } catch (err: unknown) {
        // Torn down on unmount / item switch via controller.abort() — swallow it.
        if ((err as { name?: string } | null)?.name === "AbortError") return;
      }
    })();
    return () => controller.abort();
  }, [slug, itemId]);
  return roster;
}
