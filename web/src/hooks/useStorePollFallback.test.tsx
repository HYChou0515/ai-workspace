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
