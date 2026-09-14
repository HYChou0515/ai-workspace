import { useCallback, useEffect, useState } from "react";

/**
 * A Set<string> persisted to localStorage. Used for pinned investigation
 * ids and the recently-viewed deque. SSR/test-safe: falls back to
 * in-memory state when window is unavailable.
 */
export function usePersistentSet(
  key: string,
): {
  has: (id: string) => boolean;
  toggle: (id: string) => void;
  values: string[];
} {
  // The state remembers WHICH key it was loaded from. A component that stays
  // mounted while its key changes (the file tree across items) used to keep
  // the old key's set and save it under the new key on the next effect — so
  // item A's opened folders became item B's. The pair is replaced during the
  // render that sees a new key, before any effect can save the stale one.
  const [entry, setEntry] = useState<{ key: string; set: Set<string> }>(() => ({
    key,
    set: loadSet(key),
  }));
  if (entry.key !== key) setEntry({ key, set: loadSet(key) });
  const state = entry.key === key ? entry.set : loadSet(key);

  useEffect(() => {
    if (entry.key === key) saveSet(key, entry.set);
  }, [key, entry]);

  const toggle = useCallback((id: string) => {
    setEntry((prev) => {
      const next = new Set(prev.set);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return { key: prev.key, set: next };
    });
  }, []);

  const has = useCallback((id: string) => state.has(id), [state]);
  const values = [...state];
  return { has, toggle, values };
}

function loadSet(key: string): Set<string> {
  if (typeof window === "undefined") return new Set();
  try {
    const raw = window.localStorage.getItem(key);
    if (!raw) return new Set();
    const arr = JSON.parse(raw) as unknown;
    if (Array.isArray(arr)) return new Set(arr.filter((x): x is string => typeof x === "string"));
  } catch {
    /* ignore parse errors — start fresh */
  }
  return new Set();
}

function saveSet(key: string, set: Set<string>): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(key, JSON.stringify([...set]));
  } catch {
    /* quota / private mode — swallow */
  }
}

/**
 * A bounded list of recently-touched strings. Most-recent first.
 * `push(id)` moves the id to the front; size capped at `limit`.
 */
export function usePersistentDeque(
  key: string,
  limit = 10,
): {
  values: string[];
  push: (id: string) => void;
  clear: () => void;
} {
  // Same key-aware pair as `usePersistentSet`, for the same reason.
  const [entry, setEntry] = useState<{ key: string; list: string[] }>(() => ({
    key,
    list: loadList(key),
  }));
  if (entry.key !== key) setEntry({ key, list: loadList(key) });
  const state = entry.key === key ? entry.list : loadList(key);

  useEffect(() => {
    if (entry.key === key) saveList(key, entry.list);
  }, [key, entry]);

  const push = useCallback(
    (id: string) => {
      setEntry((prev) => {
        const without = prev.list.filter((x) => x !== id);
        return { key: prev.key, list: [id, ...without].slice(0, limit) };
      });
    },
    [limit],
  );

  const clear = useCallback(() => setEntry((prev) => ({ key: prev.key, list: [] })), []);
  return { values: state, push, clear };
}

function loadList(key: string): string[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.localStorage.getItem(key);
    if (!raw) return [];
    const arr = JSON.parse(raw) as unknown;
    if (Array.isArray(arr)) return arr.filter((x): x is string => typeof x === "string");
  } catch {
    /* ignore */
  }
  return [];
}

function saveList(key: string, list: string[]): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(key, JSON.stringify(list));
  } catch {
    /* quota — swallow */
  }
}
