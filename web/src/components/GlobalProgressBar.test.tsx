// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { useEffect } from "react";
import { act, cleanup, render, screen } from "@testing-library/react";
import {
  QueryClient,
  QueryClientProvider,
  useMutation,
  useQuery,
} from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";

import { GlobalProgressBar } from "./GlobalProgressBar";

/** Fresh client per test — no retries, infinite gc, so state never leaks. */
function makeClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: Number.POSITIVE_INFINITY },
      mutations: { retry: false },
    },
  });
}

/** Mounts a query whose fetch never resolves — keeps `useIsFetching` at 1. */
function PendingProbe() {
  useQuery({ queryKey: ["probe"], queryFn: () => new Promise<number>(() => {}) });
  return null;
}

function makeDeferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((r) => (resolve = r));
  return { promise, resolve };
}

/** Mounts a query whose fetch resolves only when the test resolves `promise`. */
function Probe({ promise }: { promise: Promise<number> }) {
  useQuery({ queryKey: ["probe"], queryFn: () => promise });
  return null;
}

/** Fires a mutation on mount that stays pending until `promise` resolves. */
function MutatingProbe({ promise }: { promise: Promise<number> }) {
  const m = useMutation({ mutationFn: () => promise });
  useEffect(() => void m.mutate(), []); // eslint-disable-line react-hooks/exhaustive-deps
  return null;
}

function renderBar(client: QueryClient, ui = <GlobalProgressBar />) {
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

describe("GlobalProgressBar", () => {
  afterEach(cleanup);

  it("renders nothing when no request is in flight", () => {
    const { container } = renderBar(makeClient());
    expect(container).toBeEmptyDOMElement();
  });

  it("shows a progressbar once a request has been in flight past the debounce", () => {
    vi.useFakeTimers();
    try {
      renderBar(
        makeClient(),
        <>
          <GlobalProgressBar />
          <PendingProbe />
        </>,
      );
      // Flush React Query's batched fetch-state notification so the bar sees it,
      // then confirm the debounce still keeps the bar hidden before its window.
      act(() => void vi.runOnlyPendingTimers());
      expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
      act(() => void vi.advanceTimersByTime(150));
      expect(screen.getByRole("progressbar")).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it("does not flash for a request that finishes within the debounce window", async () => {
    vi.useFakeTimers();
    try {
      const d = makeDeferred<number>();
      renderBar(
        makeClient(),
        <>
          <GlobalProgressBar />
          <Probe promise={d.promise} />
        </>,
      );
      act(() => void vi.runOnlyPendingTimers()); // bar learns a request is active
      act(() => void vi.advanceTimersByTime(100)); // still under the 150ms window
      // The request completes before the window elapses.
      await act(async () => {
        d.resolve(1);
        await Promise.resolve();
      });
      act(() => void vi.runOnlyPendingTimers()); // bar learns the request is done
      act(() => void vi.advanceTimersByTime(200)); // well past the window now
      expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it("shows for an in-flight mutation too, not just queries", () => {
    vi.useFakeTimers();
    try {
      const d = makeDeferred<number>();
      renderBar(
        makeClient(),
        <>
          <GlobalProgressBar />
          <MutatingProbe promise={d.promise} />
        </>,
      );
      act(() => void vi.runOnlyPendingTimers()); // flush the mutate() + its notification
      act(() => void vi.advanceTimersByTime(150));
      expect(screen.getByRole("progressbar")).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });
});
