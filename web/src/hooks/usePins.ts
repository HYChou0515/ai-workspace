/**
 * Per-App, client-only item state the backend doesn't model (#dashboard):
 * `usePinned` (toggle-able pins) and `useRecentlyViewed` (most-recent-first,
 * capped). Both persist in localStorage keyed by the App slug, so they survive
 * reloads but stay local to the browser.
 */
import { useCallback, useState } from "react";

const RECENT_CAP = 8;

function read(key: string): string[] {
  try {
    const raw = localStorage.getItem(key);
    const arr = raw ? JSON.parse(raw) : [];
    return Array.isArray(arr) ? arr.filter((x): x is string => typeof x === "string") : [];
  } catch {
    return [];
  }
}

function write(key: string, ids: string[]): void {
  try {
    localStorage.setItem(key, JSON.stringify(ids));
  } catch {
    /* quota / disabled — pins are best-effort */
  }
}

export function usePinned(slug: string): {
  pinned: ReadonlySet<string>;
  isPinned: (id: string) => boolean;
  toggle: (id: string) => void;
} {
  const key = `app:${slug}:pinned`;
  const [ids, setIds] = useState<string[]>(() => read(key));
  const toggle = useCallback(
    (id: string) => {
      setIds((prev) => {
        const next = prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id];
        write(key, next);
        return next;
      });
    },
    [key],
  );
  const pinned = new Set(ids);
  return { pinned, isPinned: (id) => pinned.has(id), toggle };
}

export function useRecentlyViewed(slug: string): {
  recent: string[];
  record: (id: string) => void;
} {
  const key = `app:${slug}:recent`;
  const [recent, setRecent] = useState<string[]>(() => read(key));
  const record = useCallback(
    (id: string) => {
      setRecent((prev) => {
        const next = [id, ...prev.filter((x) => x !== id)].slice(0, RECENT_CAP);
        write(key, next);
        return next;
      });
    },
    [key],
  );
  return { recent, record };
}
