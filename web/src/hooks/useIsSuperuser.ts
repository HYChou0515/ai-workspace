import { useQuery } from "@tanstack/react-query";

import { api } from "../api";
import { qk } from "../api/queryKeys";

/**
 * Whether the signed-in user is a superuser, read once from `GET /me` and cached
 * app-wide by TanStack Query (mirrors `useCurrentUser`).
 *
 * Superuser status barely changes, so the query is `staleTime: Infinity`.
 * Returns `false` until the first fetch resolves — a superuser-only control
 * stays hidden until identity settles, never flashing in for a normal user.
 */
export function useIsSuperuser(): boolean {
  const { data } = useQuery({
    queryKey: qk.me,
    queryFn: () => api.getMe(),
    staleTime: Number.POSITIVE_INFINITY,
  });
  return data?.is_superuser ?? false;
}
