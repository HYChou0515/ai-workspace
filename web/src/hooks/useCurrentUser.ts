import { useQuery } from "@tanstack/react-query";

import { api } from "../api";
import { qk } from "../api/queryKeys";

export type CurrentUserState = {
  id: string;
  /** True once the query SETTLED (resolved or failed). While false, `id` is
   * the "default-user" placeholder — an identity, not THE identity. */
  ready: boolean;
};

/**
 * The signed-in user's id plus whether it has actually been established.
 *
 * `useItemAccess`'s loading contract needs to tell "we don't know who you are
 * yet" apart from "you are the placeholder nobody": computing permission verbs
 * from the placeholder locked owners/admins out of a cold deep-link's first
 * paint. A failed fetch counts as ready — the app then degrades to the same
 * placeholder fallback as before.
 */
export function useCurrentUserState(): CurrentUserState {
  const { data, isPending } = useQuery({
    queryKey: qk.currentUser,
    queryFn: () => api.getCurrentUser(),
    staleTime: Number.POSITIVE_INFINITY,
  });
  return { id: data ?? "default-user", ready: !isPending };
}

/**
 * The signed-in user's id, fetched once via `api.getCurrentUser()` and cached
 * app-wide by TanStack Query.
 *
 * Identity barely changes, so the query is `staleTime: Infinity` — every
 * consumer reads the same cached value and only one fetch ever fires. Returns
 * "default-user" until the first fetch resolves so owner/avatar rendering and
 * the "owned by me" filter never flash empty. When real auth lands only
 * `api.getCurrentUser` changes — callers stay the same.
 */
export function useCurrentUser(): string {
  return useCurrentUserState().id;
}
