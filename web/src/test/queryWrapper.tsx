import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { ReactElement, ReactNode } from "react";

/**
 * Test helpers for components/hooks that read through TanStack Query.
 *
 * Each test gets a FRESH client (no retries, infinite gc) so the cache never
 * leaks between tests and a rejected query fails fast instead of retrying.
 */
export function makeTestQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: Number.POSITIVE_INFINITY },
      mutations: { retry: false },
    },
  });
}

export function QueryWrap({
  children,
  client,
}: {
  children: ReactNode;
  client?: QueryClient;
}) {
  const c = client ?? makeTestQueryClient();
  return <QueryClientProvider client={c}>{children}</QueryClientProvider>;
}

/** `render()` with a fresh QueryClient provider wrapped around `ui`. */
export function renderWithQuery(
  ui: ReactElement,
  client: QueryClient = makeTestQueryClient(),
) {
  return {
    client,
    ...render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>),
  };
}
