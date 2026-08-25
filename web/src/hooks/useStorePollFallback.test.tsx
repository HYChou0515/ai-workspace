// @vitest-environment happy-dom
import { renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { useStorePollFallback } from "./useStorePollFallback";

describe("useStorePollFallback", () => {
  it("polls the shared store while active and silent, delivering fetched items", async () => {
    const onSnapshot = vi.fn();
    const fetchThread = vi.fn().mockResolvedValue([{ role: "user", content: "q" }]);
    renderHook(() =>
      useStorePollFallback({
        active: true,
        isLive: () => false,
        fetchThread,
        onSnapshot,
        pollMs: 5,
      }),
    );
    await waitFor(() => expect(onSnapshot).toHaveBeenCalled());
    expect(onSnapshot).toHaveBeenCalledWith([{ role: "user", content: "q" }]);
  });

  it("skips the poll while a live event is recent (does not clobber the live stream)", async () => {
    const onSnapshot = vi.fn();
    const fetchThread = vi.fn().mockResolvedValue([]);
    renderHook(() =>
      useStorePollFallback({
        active: true,
        isLive: () => true, // live stream is delivering — stay out of its way
        fetchThread,
        onSnapshot,
        pollMs: 5,
      }),
    );
    await new Promise((r) => setTimeout(r, 40));
    expect(fetchThread).not.toHaveBeenCalled();
    expect(onSnapshot).not.toHaveBeenCalled();
  });

  it("does not poll when no turn is in flight", async () => {
    const onSnapshot = vi.fn();
    const fetchThread = vi.fn().mockResolvedValue([]);
    renderHook(() =>
      useStorePollFallback({ active: false, isLive: () => false, fetchThread, onSnapshot, pollMs: 5 }),
    );
    await new Promise((r) => setTimeout(r, 40));
    expect(fetchThread).not.toHaveBeenCalled();
  });

  it("a poll that fails after the caller is gone reports to nobody", async () => {
    // The success path already checked `cancelled`; the failure path did not,
    // so a read that rejected after unmount still reported to a component that
    // no longer exists. In a browser that is a stray setState; under happy-dom
    // the environment has been torn down by then and React reaches for
    // `window`. Either way it surfaced the same way: an unhandled rejection
    // that failed the whole vitest run while every test passed.
    const onError = vi.fn();
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    let reject: (e: unknown) => void = () => {};
    const fetchThread = vi.fn(() => new Promise((_res, rej) => (reject = rej)));
    const { unmount } = renderHook(() =>
      useStorePollFallback({
        active: true,
        isLive: () => false,
        fetchThread,
        onSnapshot: vi.fn(),
        onError,
        pollMs: 5,
      }),
    );
    await waitFor(() => expect(fetchThread).toHaveBeenCalled());

    unmount();
    reject(new Error("store unreachable"));
    await new Promise((r) => setTimeout(r, 20));

    expect(onError).not.toHaveBeenCalled();
    // Nor the log: vitest ships console output to its worker over an rpc that
    // is closed by now, so a late warn is itself an unhandled rejection.
    expect(warn).not.toHaveBeenCalled();
    warn.mockRestore();
  });

  it("reports a failed poll while the caller is still there", async () => {
    // The guard must not silence the case the callback exists for.
    const onError = vi.fn();
    const fetchThread = vi.fn().mockRejectedValue(new Error("store unreachable"));
    renderHook(() =>
      useStorePollFallback({
        active: true,
        isLive: () => false,
        fetchThread,
        onSnapshot: vi.fn(),
        onError,
        pollMs: 5,
      }),
    );

    await waitFor(() => expect(onError).toHaveBeenCalled());
  });

  it("stops polling once the turn is no longer active", async () => {
    const onSnapshot = vi.fn();
    const fetchThread = vi.fn().mockResolvedValue([]);
    const { rerender } = renderHook(
      ({ active }) =>
        useStorePollFallback({ active, isLive: () => false, fetchThread, onSnapshot, pollMs: 5 }),
      { initialProps: { active: true } },
    );
    await waitFor(() => expect(fetchThread).toHaveBeenCalled());
    rerender({ active: false });
    const calls = fetchThread.mock.calls.length;
    await new Promise((r) => setTimeout(r, 40));
    expect(fetchThread.mock.calls.length).toBe(calls);
  });
});
