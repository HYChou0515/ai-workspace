/**
 * A tiny publish/subscribe keyed by workspace scope.
 *
 * The workspace shell puts the chat and the file pane side by side: neither is
 * an ancestor of the other, so a renderer that needs to reach the chat (or a
 * chat event that needs to reach a renderer) has no prop to travel along, and
 * threading a callback through the shell for each one adds a parameter to every
 * layer in between. `openFile` earned its context because the SHELL owns it;
 * these are sibling-to-sibling, which a context cannot express without hoisting
 * state that belongs where it is.
 *
 * Scoped by id because two items can be open at once and an event in one is not
 * news in the other.
 */

export type ScopedBus<T> = {
  publish(scopeId: string, value: T): void;
  /** Returns the unsubscribe. */
  subscribe(scopeId: string, fn: (value: T) => void): () => void;
};

export function createScopedBus<T>(): ScopedBus<T> {
  const listeners = new Map<string, Set<(value: T) => void>>();
  return {
    subscribe(scopeId, fn) {
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
    },
    publish(scopeId, value) {
      for (const fn of listeners.get(scopeId) ?? []) {
        try {
          fn(value);
        } catch {
          // One listener throwing must not stop the others hearing about it.
          // On this bus a listener may be, or may be driven by, arbitrary
          // agent-written code — the likeliest thing in the app to throw.
        }
      }
    },
  };
}
