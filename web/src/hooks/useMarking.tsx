/**
 * Named markings in React (#847 PR 3 P1): one `MarkingStore` per item, provided
 * by `WorkspaceProviders`, read and written by name. A view re-renders only
 * when the marking it is on changes.
 */
import { createContext, type ReactNode, useCallback, useContext, useSyncExternalStore } from "react";

import { type Marking, type MarkingEntry, MarkingStore } from "../lib/markings";

const MarkingContext = createContext<MarkingStore | null>(null);

export function MarkingProvider({ store, children }: { store: MarkingStore; children: ReactNode }) {
  return <MarkingContext.Provider value={store}>{children}</MarkingContext.Provider>;
}

const noop = () => () => {};
const EMPTY: readonly string[] = [];

export type WriteMarking = (marking: Marking | null, source: string | null) => void;

/** `[entry, write]` for marking `name`. `null` is a detached view: it reads
 * nothing and its writes go nowhere. Outside a provider (a standalone preview)
 * the same — a view renders dark rather than throwing. */
export function useMarking(name: string | null): [MarkingEntry | undefined, WriteMarking] {
  const store = useContext(MarkingContext);
  const subscribe = useCallback(
    (cb: () => void) => (store && name ? store.subscribe(name, cb) : noop()),
    [store, name],
  );
  const entry = useSyncExternalStore(subscribe, () =>
    store && name ? store.get(name) : undefined,
  );
  const write = useCallback<WriteMarking>(
    (marking, source) => {
      if (store && name) store.set(name, marking, source);
    },
    [store, name],
  );
  return [entry, write];
}

/** The names holding a non-empty marking, sorted — the send chips (P7). */
export function useMarkingNames(): readonly string[] {
  const store = useContext(MarkingContext);
  const subscribe = useCallback(
    (cb: () => void) => (store ? store.subscribeNames(cb) : noop()),
    [store],
  );
  return useSyncExternalStore(subscribe, () => (store ? store.names() : EMPTY));
}

/** The item's store itself, for code that reads every marking at once (P7). */
export function useMarkingStore(): MarkingStore | null {
  return useContext(MarkingContext);
}
