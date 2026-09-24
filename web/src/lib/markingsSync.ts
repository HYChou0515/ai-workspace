/**
 * One item's markings, shared by every tab that has the item open (#847).
 *
 * Chat mode opens a shown view on the editor-area page in a NEW TAB, while the
 * composer that sends markings lives in the chat tab. Each tab builds its own
 * `MarkingStore`, so without this a selection made on that page could never go
 * with a message (Q10). A `BroadcastChannel` per item carries each local write
 * to the other tabs; a tab that opens later asks the others for what they hold.
 * Last write wins — two people's tabs never share a channel (it is per browser).
 */
import type { Marking, MarkingStore } from "./markings";

type Wire =
  | { kind: "hello" }
  | { kind: "set"; name: string; columns: Record<string, string[]> | null; source: string | null };

function toWire(marking: Marking | undefined): Record<string, string[]> | null {
  if (!marking) return null;
  return Object.fromEntries(Object.entries(marking).map(([c, v]) => [c, [...v]]));
}

function fromWire(columns: Record<string, string[]> | null): Marking | null {
  if (!columns) return null;
  return Object.fromEntries(Object.entries(columns).map(([c, v]) => [c, new Set(v)]));
}

function sameAsHeld(store: MarkingStore, msg: Extract<Wire, { kind: "set" }>): boolean {
  const held = store.get(msg.name);
  if (!held || !msg.columns) return !held && !msg.columns;
  if (held.source !== msg.source) return false;
  const cols = Object.keys(msg.columns);
  if (cols.length !== Object.keys(held.marking).length) return false;
  return cols.every((c) => {
    const mine = held.marking[c];
    const theirs = msg.columns![c]!;
    return mine !== undefined && mine.size === theirs.length && theirs.every((v) => mine.has(v));
  });
}

/** Keep `store` in step with the other tabs holding item `itemKey`. Returns the
 * unsubscribe. A no-op where `BroadcastChannel` does not exist. */
export function syncMarkingsAcrossTabs(store: MarkingStore, itemKey: string): () => void {
  if (typeof BroadcastChannel === "undefined") return () => {};
  const channel = new BroadcastChannel(`aiws-markings:${itemKey}`);
  const post = (msg: Wire) => channel.postMessage(msg);
  const offWrites = store.subscribeWrites((name, entry, fromPeer) => {
    // A peer's write is already everywhere; sending it back would echo forever.
    if (fromPeer) return;
    post({
      kind: "set",
      name,
      columns: toWire(entry?.marking),
      source: entry?.source ?? null,
    });
  });
  channel.onmessage = (ev: MessageEvent<Wire>) => {
    const msg = ev.data;
    if (msg.kind === "hello") {
      for (const [name, entry] of store.snapshot()) {
        post({ kind: "set", name, columns: toWire(entry.marking), source: entry.source });
      }
      return;
    }
    // A reply to a `hello` can repeat what this tab already holds (the hello
    // crossed a write in flight); applying it again would re-render every view
    // on that marking for nothing.
    if (sameAsHeld(store, msg)) return;
    store.set(msg.name, fromWire(msg.columns), msg.source, { fromPeer: true });
  };
  post({ kind: "hello" });
  return () => {
    offWrites();
    channel.close();
  };
}
