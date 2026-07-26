/**
 * Notify an open editor that the item's files changed underneath it — a peer's
 * save or an agent's write (docs/plan-ai-sheet.md Phase 4).
 *
 * Subscribes to the item's broadcast `/stream`, the same source
 * `useEntityLiveSync` uses, and fires on any `file_changed` without parsing the
 * path: the event is cheap, a re-read is cheap, and a path heuristic is the kind
 * of thing that silently stops matching. The caller decides what to do — which
 * matters, because the right answer differs depending on whether there are
 * unsaved edits to protect.
 */

import { useEffect, useRef } from "react";

import { api } from "../api";

export function useOutsideFileChange(slug: string, itemId: string, onChanged: () => void, enabled = true): void {
  // Held in a ref so the caller can close over fresh state (e.g. "am I dirty?")
  // without re-subscribing on every render.
  const handler = useRef(onChanged);
  handler.current = onChanged;

  useEffect(() => {
    if (!enabled || !itemId) return;
    const controller = new AbortController();
    void (async () => {
      try {
        for await (const ev of api.subscribeInvestigation(slug, itemId, controller.signal)) {
          if (ev.type === "file_changed") handler.current();
        }
      } catch (err: unknown) {
        // Torn down on unmount / item switch via controller.abort() — swallow it.
        if ((err as { name?: string } | null)?.name === "AbortError") return;
      }
    })();
    return () => controller.abort();
  }, [slug, itemId, enabled]);
}
