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
import { type Marking, markingFrom, markingRows, type MarkingStore } from "./markings";

/** On the channel a marking is `keys` + `rows` (#861 formats); `rows: null`
 * clears. */
type Wire =
  | { kind: "hello" }
  | { kind: "set"; name: string; keys: string[]; rows: string[][] | null; source: string | null };

const isStrings = (v: unknown): v is string[] => Array.isArray(v) && v.every((x) => typeof x === "string");

function asWire(data: unknown): Wire | null {
  if (!data || typeof data !== "object") return null;
  const d = data as Record<string, unknown>;
  if (d.kind === "hello") return { kind: "hello" };
  if (d.kind !== "set" || typeof d.name !== "string" || !d.name) return null;
  if (d.source !== null && typeof d.source !== "string") return null;
  if (!isStrings(d.keys)) return null;
  const rows = d.rows;
  if (rows !== null && !(Array.isArray(rows) && rows.every(isStrings))) return null;
  return { kind: "set", name: d.name, keys: d.keys, rows: rows as string[][] | null, source: d.source as string | null };
}

function setMessage(name: string, marking: Marking | undefined, source: string | null): Wire {
  return { kind: "set", name, keys: marking ? [...marking.keys] : [], rows: marking ? markingRows(marking) : null, source };
}

function fromWire(msg: Extract<Wire, { kind: "set" }>): Marking | null {
  return msg.rows ? markingFrom(msg.keys, msg.rows) : null;
}

function sameAsHeld(store: MarkingStore, msg: Extract<Wire, { kind: "set" }>): boolean {
  const held = store.get(msg.name);
  const theirs = fromWire(msg);
  if (!held || !theirs) return !held && !theirs;
  if (held.source !== msg.source) return false;
  const mine = held.marking;
  return (
    mine.keys.length === theirs.keys.length &&
    mine.keys.every((k, i) => k === theirs.keys[i]) &&
    mine.tuples.size === theirs.tuples.size &&
    [...theirs.tuples].every((t) => mine.tuples.has(t))
  );
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
    post(setMessage(name, entry?.marking, entry?.source ?? null));
  });
  channel.onmessage = (ev: MessageEvent<unknown>) => {
    const msg = asWire(ev.data);
    // Anything else on the channel (another build of the app, a stray post)
    // is not ours to apply — and throwing here would be an unhandled error.
    if (!msg) return;
    if (msg.kind === "hello") {
      for (const [name, entry] of store.snapshot()) {
        post(setMessage(name, entry.marking, entry.source));
      }
      return;
    }
    // A reply to a `hello` can repeat what this tab already holds (the hello
    // crossed a write in flight); applying it again would re-render every view
    // on that marking for nothing.
    if (sameAsHeld(store, msg)) return;
    store.set(msg.name, fromWire(msg), msg.source, { fromPeer: true });
  };
  post({ kind: "hello" });
  return () => {
    offWrites();
    channel.close();
  };
}
