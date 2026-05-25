import { useQuery } from "@tanstack/react-query";

import { api } from "../api";
import { qk } from "../api/queryKeys";

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
  const { data } = useQuery({
    queryKey: qk.currentUser,
    queryFn: () => api.getCurrentUser(),
    staleTime: Number.POSITIVE_INFINITY,
  });
  return data ?? "default-user";
}
