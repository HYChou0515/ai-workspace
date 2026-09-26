/**
 * Notify an open editor that the item's files changed underneath it — a peer's
 * save or an agent's write (docs/plan-ai-sheet.md Phase 4).
 *
 * Listens to the item's broadcast `/stream` through its one shared connection
 * (`subscribeItemEvents`, #856 P9 — the same one `useEntityLiveSync` uses), and
 * fires on any `file_changed` without parsing the path: the event is cheap, a
 * re-read is cheap, and a path heuristic is the kind of thing that silently stops
 * matching. The caller decides what to do — which matters, because the right
 * answer differs depending on whether there are unsaved edits to protect.
 */

import { useEffect, useRef } from "react";

import { subscribeItemEvents } from "../api/itemEvents";

export function useOutsideFileChange(slug: string, itemId: string, onChanged: () => void, enabled = true): void {
  // Held in a ref so the caller can close over fresh state (e.g. "am I dirty?")
  // without re-subscribing on every render.
  const handler = useRef(onChanged);
  handler.current = onChanged;

  useEffect(() => {
    if (!enabled || !itemId) return;
    return subscribeItemEvents(slug, itemId, (ev) => {
      if (ev.type === "file_changed") handler.current();
    });
  }, [slug, itemId, enabled]);
}
