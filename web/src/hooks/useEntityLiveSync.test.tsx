// @vitest-environment happy-dom
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "../api";
import type { AgentEvent } from "../events";
import { QueryWrap, makeTestQueryClient } from "../test/queryWrapper";
import { useEntityLiveSync } from "./useEntityLiveSync";

afterEach(() => vi.restoreAllMocks());

/** A stream that yields `events`, then blocks until the subscription aborts —
 * mirroring a real long-lived SSE (so teardown goes through the abort path). */
function streamOf(events: AgentEvent[]) {
  return async function* (_slug: string, _id: string, signal?: AbortSignal) {
    for (const ev of events) yield ev;
    await new Promise<void>((resolve) => signal?.addEventListener("abort", () => resolve()));
  };
}

const fileChanged: AgentEvent = { type: "file_changed", path: "/issues/1.md", by: "bob", kind: "written" };

function render(slug: string, itemId: string, events: AgentEvent[]) {
  vi.spyOn(api, "subscribeInvestigation").mockImplementation(streamOf(events) as never);
  const qc = makeTestQueryClient();
  const spy = vi.spyOn(qc, "invalidateQueries");
  const view = renderHook(() => useEntityLiveSync(slug, itemId), {
    wrapper: ({ children }: { children: ReactNode }) => <QueryWrap client={qc}>{children}</QueryWrap>,
  });
  return { spy, ...view };
}

describe("useEntityLiveSync (#455 P2)", () => {
  it("invalidates the item's entities on a file_changed broadcast", async () => {
    const { spy } = render("pm", "pm-project/1", [fileChanged]);
    await waitFor(() =>
      expect(spy).toHaveBeenCalledWith({ queryKey: ["entities", "pm", "pm-project/1"] }),
    );
  });

  it("ignores non-file_changed events (a chat turn must not thrash the board)", async () => {
    const { spy } = render("pm", "pm-project/1", [{ type: "message_delta", text: "hi" }]);
    // Give the stream a tick to deliver; the entities key must never be invalidated.
    await new Promise((r) => setTimeout(r, 10));
    expect(spy).not.toHaveBeenCalledWith({ queryKey: ["entities", "pm", "pm-project/1"] });
  });

  it("does not open a subscription until the item id is known", () => {
    const sub = vi.spyOn(api, "subscribeInvestigation").mockImplementation(streamOf([]) as never);
    const qc = makeTestQueryClient();
    renderHook(() => useEntityLiveSync("pm", ""), {
      wrapper: ({ children }: { children: ReactNode }) => <QueryWrap client={qc}>{children}</QueryWrap>,
    });
    expect(sub).not.toHaveBeenCalled();
  });
});
