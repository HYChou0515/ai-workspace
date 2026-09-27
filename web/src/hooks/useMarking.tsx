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

/** `opts.ifEmpty`: write only if the marking holds nothing at that moment — for
 * a view seeding its own default, which must never overwrite a selection.
 * Returns whether a store took the write: false for a detached view and
 * outside a provider, where it goes nowhere (a view whose write went nowhere
 * holds its own selection -- nothing else can replace it), and false when
 * `ifEmpty` found the marking occupied (#847/#848 PR 5 P41 row 27: the
 * store's own answer, `MarkingStore.set`). The same write again is held
 * already: true. */
export type WriteMarking = (
  marking: Marking | null,
  source: string | null,
  opts?: { ifEmpty?: boolean },
) => boolean;

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
    (marking, source, opts) => {
      if (!store || !name) return false;
      return store.set(name, marking, source, { ifEmpty: opts?.ifEmpty });
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

const NONE: ReadonlyMap<string, MarkingEntry> = new Map();

/** Every marking of the item, sorted by name — the composer's send chips (P7).
 * Re-renders on any write, which is right for the composer and wrong for a
 * view: a view uses `useMarking(name)`. */
export function useAllMarkings(): ReadonlyMap<string, MarkingEntry> {
  const store = useContext(MarkingContext);
  const subscribe = useCallback(
    (cb: () => void) => (store ? store.subscribeAll(cb) : noop()),
    [store],
  );
  return useSyncExternalStore(subscribe, () => (store ? store.snapshot() : NONE));
}

/** The item's store itself, for code that reads every marking at once (P7). */
export function useMarkingStore(): MarkingStore | null {
  return useContext(MarkingContext);
}
