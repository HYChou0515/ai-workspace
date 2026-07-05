// @vitest-environment happy-dom
import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "../api";
import type { AgentEvent } from "../events";
import { useItemPresence } from "./useItemPresence";

afterEach(() => vi.restoreAllMocks());

function streamOf(events: AgentEvent[]) {
  return async function* (_slug: string, _id: string, signal?: AbortSignal) {
    for (const ev of events) yield ev;
    await new Promise<void>((resolve) => signal?.addEventListener("abort", () => resolve()));
  };
}

describe("useItemPresence (#455 P4)", () => {
  it("returns the roster from a presence broadcast", async () => {
    vi.spyOn(api, "subscribeInvestigation").mockImplementation(
      streamOf([{ type: "presence", users: ["alice", "bob"] }]) as never,
    );
    const { result } = renderHook(() => useItemPresence("pm", "A"));
    await waitFor(() => expect(result.current).toEqual(["alice", "bob"]));
  });

  it("ignores non-presence events", async () => {
    vi.spyOn(api, "subscribeInvestigation").mockImplementation(
      streamOf([{ type: "file_changed", path: "/x.md", by: "a", kind: "written" }]) as never,
    );
    const { result } = renderHook(() => useItemPresence("pm", "A"));
    await new Promise((r) => setTimeout(r, 10));
    expect(result.current).toEqual([]);
  });

  it("does not subscribe until the item id is known", () => {
    const sub = vi.spyOn(api, "subscribeInvestigation").mockImplementation(streamOf([]) as never);
    renderHook(() => useItemPresence("pm", ""));
    expect(sub).not.toHaveBeenCalled();
  });
});
