/**
 * "Someone else edited a file in this workspace", for consumers the chat stream
 * cannot reach with a prop.
 *
 * The `file_changed` broadcast (#43) already arrives on the chat's SSE
 * subscription, where it invalidates the file-tree query. That is enough for a
 * tree that re-reads on demand, but not for a WUI: a page holding edited state
 * has to be TOLD, or it saves over the other person's work with neither of them
 * noticing. It lives several layers below the subscription and takes no props
 * from it, hence a module-level bus rather than more plumbing.
 *
 * Keyed by workspace scope id, because two items can be open at once and an edit
 * in one is not news in the other.
 */

type Listener = (path: string) => void;

const listeners = new Map<string, Set<Listener>>();

/** Subscribe to edits in one workspace. Returns the unsubscribe. */
export function subscribeFileChanged(scopeId: string, fn: Listener): () => void {
  let set = listeners.get(scopeId);
  if (!set) {
    set = new Set();
    listeners.set(scopeId, set);
  }
  set.add(fn);
  return () => {
    set.delete(fn);
    if (set.size === 0) listeners.delete(scopeId);
  };
}

/** Announce an edit. Called from wherever the broadcast is already handled. */
export function publishFileChanged(scopeId: string, path: string): void {
  for (const fn of listeners.get(scopeId) ?? []) {
    try {
      fn(path);
    } catch {
      // One listener throwing must not stop the others hearing about it — a WUI
      // is arbitrary agent-written code, and it is the likeliest to throw.
    }
  }
}
