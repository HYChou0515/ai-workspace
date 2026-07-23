import { useQuery } from "@tanstack/react-query";

import { api } from "../api";
import { qk } from "../api/queryKeys";

export type IsSuperuserState = {
  isSuperuser: boolean;
  /** #608 — the caller's group ids, so the gate helpers can resolve a
   * `group:<id>` grant to the current viewer. Empty until settled / no groups. */
  groups: string[];
  /** True once `GET /me` SETTLED (resolved or failed). While false,
   * `isSuperuser` is the safe-side placeholder `false`. */
  ready: boolean;
};

/**
 * Superuser status + group ids plus whether identity has actually been
 * established — the identity half of `useItemAccess`'s loading contract (see
 * `useCurrentUserState`). A failed fetch counts as ready: the app then stays at
 * `false` / no groups, as before.
 */
export function useIsSuperuserState(): IsSuperuserState {
  const { data, isPending } = useQuery({
    queryKey: qk.me,
    queryFn: () => api.getMe(),
    staleTime: Number.POSITIVE_INFINITY,
  });
  return {
    isSuperuser: data?.is_superuser ?? false,
    groups: data?.groups ?? [],
    ready: !isPending,
  };
}

/**
 * Whether the signed-in user is a superuser, read once from `GET /me` and cached
 * app-wide by TanStack Query (mirrors `useCurrentUser`).
 *
 * Superuser status barely changes, so the query is `staleTime: Infinity`.
 * Returns `false` until the first fetch resolves — a superuser-only control
 * stays hidden until identity settles, never flashing in for a normal user.
 */
export function useIsSuperuser(): boolean {
  return useIsSuperuserState().isSuperuser;
}
