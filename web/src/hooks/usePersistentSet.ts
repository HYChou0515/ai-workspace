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
  const [state, setState] = useState<Set<string>>(() => loadSet(key));

  useEffect(() => {
    saveSet(key, state);
  }, [key, state]);

  const toggle = useCallback((id: string) => {
    setState((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
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
  const [state, setState] = useState<string[]>(() => loadList(key));

  useEffect(() => {
    saveList(key, state);
  }, [key, state]);

  const push = useCallback(
    (id: string) => {
      setState((prev) => {
        const without = prev.filter((x) => x !== id);
        return [id, ...without].slice(0, limit);
      });
    },
    [limit],
  );

  const clear = useCallback(() => setState([]), []);
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
