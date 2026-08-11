import { MutationCache, QueryClient } from "@tanstack/react-query";

import { reportWriteFailure } from "../lib/writeFailures";

/** Opt OUT of the global failure notice, for a mutation that already renders its
 * own error where the user is looking. Opt-out rather than opt-in on purpose: a
 * default of "silent" is how 135 mutations came to swallow their failures, and
 * the next one added would inherit the same hole. */
declare module "@tanstack/react-query" {
  interface Register {
    mutationMeta: { silentError?: boolean };
  }
}

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
 *
 * The `mutationCache` handler is the app's "no silent write failure" rule, kept
 * here because this is the only place every mutation passes through. Each call
 * site reading its own `mutation.error` was the alternative, and it is the one
 * that failed: `useUpdateItemField` never read it, so the item PATCH's 403 —
 * env vars, tool/skill prefs, the details form — closed the panel and looked
 * like a save. QUERIES are deliberately not covered: a read has empty and error
 * states already, and a background refetch failing is not worth interrupting.
 */
export function makeQueryClient(): QueryClient {
  return new QueryClient({
    mutationCache: new MutationCache({
      onError: (error, _vars, _ctx, mutation) => {
        if (mutation.meta?.silentError) return;
        reportWriteFailure(error);
      },
    }),
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
