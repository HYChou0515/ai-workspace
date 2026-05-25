import { QueryClient } from "@tanstack/react-query";

/**
 * App-wide TanStack Query client. One instance owns the cache, dedupes
 * in-flight requests by query key, and serves the same data to every
 * consumer of a key.
 *
 * Defaults chosen for a tool-style app (not a live dashboard):
 *  - `refetchOnWindowFocus: false` — tabbing back must not blow away local
 *    UI state by refetching everything.
 *  - `staleTime: 30s` — within the window, mounts read cache instead of
 *    refetching. Near-static reads (agent configs, templates, current user)
 *    override this to `Infinity` at the call site.
 *  - `retry: 1` — one retry; the backend is local/internal, not flaky cloud.
 */
export function makeQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        retry: 1,
        refetchOnWindowFocus: false,
        staleTime: 30_000,
      },
    },
  });
}

export const queryClient = makeQueryClient();
