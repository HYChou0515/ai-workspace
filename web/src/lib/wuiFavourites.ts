/**
 * The viewer's favourites on the WUI overview
 * (`docs/plan-wui-overview-icon-favourites.md`).
 *
 * A SET of page keys, in localStorage — the author asked for localStorage, so
 * this is per browser and never reaches the server. Per signed-in user inside
 * it, the way `onboarding.ts` keeps its dismissals: two people on one machine
 * do not share a star. The key is the row's own identity, item plus path —
 * the two halves of the deploy id — so a page Removed and Deployed again is
 * the same key and comes back starred. Nothing prunes: a key the listing no
 * longer returns is simply not drawn, and keeping it costs a few bytes the
 * viewer put there on purpose.
 *
 * Every read and write is in try/catch, as every `rca.*` helper is: a storage
 * that throws (a private window, a locked-down browser) makes the star not
 * sticky, not the page broken.
 */
import { useCallback, useState } from "react";

const KEY = "rca.wuiFavourites";

type Store = Record<string, string[]>;

/** The identity of one Deployed page, for storage: item id then path (the path
 * starts with `/`, so the boundary is unambiguous). */
export function favouriteKey(page: { item_id: string; path: string }): string {
  return `${page.item_id}${page.path}`;
}

// `encodeURIComponent` so a user id holding `:` or `%` cannot collide with
// another's — the `onboarding.ts` rule, kept even though this store has no
// scope suffix, so the two stores spell a user the same way.
function userKey(userId: string): string {
  return encodeURIComponent(userId);
}

function read(): Store {
  try {
    const raw = localStorage.getItem(KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : null;
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? (parsed as Store) : {};
  } catch {
    return {};
  }
}

function write(s: Store): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(s));
  } catch {
    /* localStorage unavailable — the star just isn't sticky */
  }
}

/** This user's favourite keys, in the order they were starred. */
export function readFavourites(userId: string): string[] {
  const list = read()[userKey(userId)];
  return Array.isArray(list) ? list.filter((k): k is string => typeof k === "string") : [];
}

/** Star or unstar one page for this user; returns the new list. */
export function toggleFavourite(userId: string, key: string): string[] {
  const s = read();
  const before = readFavourites(userId);
  const after = before.includes(key) ? before.filter((k) => k !== key) : [...before, key];
  s[userKey(userId)] = after;
  write(s);
  return after;
}

/** React state over one user's set: `has` for the row, `toggle` for the star.
 * The state is DERIVED from the user (re-read when the id changes), so a page
 * that learns who is signed in after its first render does not keep showing
 * nobody's stars — the same reason `wuiAutoBuild` derives from its scope. */
export function useWuiFavourites(userId: string): {
  has: (key: string) => boolean;
  toggle: (key: string) => void;
} {
  const [state, setState] = useState<{ user: string; keys: string[] }>(() => ({
    user: userId,
    keys: readFavourites(userId),
  }));
  // Derived during render, not in an effect: an effect would let one render
  // show the previous user's stars.
  const keys = state.user === userId ? state.keys : readFavourites(userId);
  if (state.user !== userId) setState({ user: userId, keys });
  const has = useCallback((key: string) => keys.includes(key), [keys]);
  const toggle = useCallback(
    (key: string) => setState({ user: userId, keys: toggleFavourite(userId, key) }),
    [userId],
  );
  return { has, toggle };
}
