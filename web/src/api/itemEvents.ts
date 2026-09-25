/**
 * One connection to an item's broadcast `/stream` per page, shared by every
 * listener (#856 P9, found by the live check).
 *
 * Each open `.ai.yaml` view (`useEntityLiveSync`), sheet (`useOutsideFileChange`)
 * and the presence bar used to hold its OWN connection. The browser allows six
 * per host over HTTP/1.1: a layout of four charts plus the agent's stream and
 * presence filled them, and the next request — sending a message — queued behind
 * connections that never close. Now listeners ref-count one subscription; the
 * last one to leave closes it.
 *
 * `useAgent` keeps its own connection on purpose: it resumes with `since` after
 * a drop and reports connection state, which a plain listener has no use for.
 */
import type { AgentEvent } from "../events";
import { api } from "./index";

type Listener = (ev: AgentEvent) => void;
type Hub = { listeners: Set<Listener>; controller: AbortController };

const hubs = new Map<string, Hub>();

export function subscribeItemEvents(slug: string, itemId: string, listener: Listener): () => void {
  const key = `${slug}\n${itemId}`;
  let hub = hubs.get(key);
  if (!hub) {
    const opened: Hub = { listeners: new Set(), controller: new AbortController() };
    hub = opened;
    hubs.set(key, opened);
    void (async () => {
      try {
        for await (const ev of api.subscribeInvestigation(slug, itemId, opened.controller.signal)) {
          for (const l of [...opened.listeners]) l(ev);
        }
      } catch {
        // Aborted by the last listener leaving, or the stream failed — as with
        // the per-listener connections this replaces, nothing resumes it.
      } finally {
        if (hubs.get(key) === opened) hubs.delete(key);
      }
    })();
  }
  const current = hub;
  current.listeners.add(listener);
  return () => {
    current.listeners.delete(listener);
    if (current.listeners.size === 0) {
      if (hubs.get(key) === current) hubs.delete(key);
      current.controller.abort();
    }
  };
}
