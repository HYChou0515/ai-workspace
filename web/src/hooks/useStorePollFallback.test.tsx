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
    //
    // Unmounted at the end, and the warn silenced, because this file has no
    // auto-cleanup: a still-mounted poller keeps rejecting every `pollMs`
    // forever, and each rejection logs. Vitest ships console output to its
    // worker over an rpc that closes when the FILE finishes, so a poller left
    // running is a log in flight at teardown — which is an unhandled rejection
    // and fails the run with every test green. Locally it got away with it
    // four times; CI caught it first try.
    const onError = vi.fn();
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const fetchThread = vi.fn().mockRejectedValue(new Error("store unreachable"));
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

    await waitFor(() => expect(onError).toHaveBeenCalled());

    unmount();
    warn.mockRestore();
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
