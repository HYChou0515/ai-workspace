import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { ReactElement, ReactNode } from "react";

import { DialogProvider } from "../components/Dialog";

/**
 * Test helpers for components/hooks that read through TanStack Query.
 *
 * Each test gets a FRESH client (no retries, infinite gc) so the cache never
 * leaks between tests and a rejected query fails fast instead of retrying.
 *
 * These mirror the provider stack in `main.tsx`, which is why the confirm
 * dialog is here too (#779): it moved to the app root, so a component under
 * test reaches `useDialog()` exactly the way it does in the running app. Keep
 * the two in step — a provider the app has and this wrapper lacks turns into a
 * wall of "must be used inside <Provider>" the moment a component starts using
 * it.
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
  return (
    <QueryClientProvider client={c}>
      <DialogProvider>{children}</DialogProvider>
    </QueryClientProvider>
  );
}

/** `render()` with the app-root providers wrapped around `ui`. */
export function renderWithQuery(
  ui: ReactElement,
  client: QueryClient = makeTestQueryClient(),
) {
  return {
    client,
    ...render(
      <QueryClientProvider client={client}>
        <DialogProvider>{ui}</DialogProvider>
      </QueryClientProvider>,
    ),
  };
}
