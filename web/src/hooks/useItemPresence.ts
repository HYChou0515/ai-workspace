/**
 * #455 P4 — the live roster of who else has this item open. Listens to the
 * item's broadcast `/stream` and tracks the latest `presence` event the backend
 * emits whenever a viewer joins or leaves — through the item's one shared
 * connection (`subscribeItemEvents`, #856 P9), so the roster is live wherever
 * it's mounted; the backend dedupes a user's multiple tabs into one entry.
 *
 * Per-pod + ephemeral, consistent with the SSE broadcast — a viewer on another
 * pod isn't counted (cross-pod convergence is out of scope, #202 / #349).
 */

import { useEffect, useState } from "react";

import { subscribeItemEvents } from "../api/itemEvents";

export function useItemPresence(slug: string, itemId: string): string[] {
  const [roster, setRoster] = useState<string[]>([]);
  useEffect(() => {
    setRoster([]); // drop any prior item's roster while the new one loads
    if (!itemId) return;
    return subscribeItemEvents(slug, itemId, (ev) => {
      if (ev.type === "presence") setRoster(ev.users);
    });
  }, [slug, itemId]);
  return roster;
}
