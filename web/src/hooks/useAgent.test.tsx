// @vitest-environment happy-dom
import { act, renderHook as rtlRenderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ReactNode } from "react";

import { api } from "../api";
import { QueryWrap } from "../test/queryWrapper";
import { useAgentInternal } from "./useAgent";
import { WorkspaceSlugProvider } from "./useWorkspaceSlug";

const Wrap = ({ children }: { children: ReactNode }) => (
  <QueryWrap>
    <WorkspaceSlugProvider value="rca">{children}</WorkspaceSlugProvider>
  </QueryWrap>
);
const renderHook = <T,>(cb: () => T) => rtlRenderHook(cb, { wrapper: Wrap });

describe("useAgent — stop / cancel (#49)", () => {
  afterEach(() => vi.restoreAllMocks());

  it("Stop flips the button back immediately, even if the stream is slow to tear down", async () => {
    vi.spyOn(api, "getCurrentUser").mockResolvedValue("tester");
    vi.spyOn(api, "getConversation").mockReturnValue(new Promise(() => {}));
    const cancelSpy = vi.spyOn(api, "cancelMessage").mockResolvedValue();

    // A turn stuck in a long exec: the broadcast subscription never yields a
    // terminal, and sendMessage resolves but the turn keeps running. Pressing
    // Stop must flip the UI on its own.
    vi.spyOn(api, "sendMessage").mockResolvedValue();
    vi.spyOn(api, "subscribeInvestigation").mockImplementation(
      // eslint-disable-next-line require-yield
      async function* () {
        await new Promise(() => {}); // hang forever
      },
    );

    const { result } = renderHook(() => useAgentInternal("inv-1"));

    act(() => {
      void result.current.send("why is zone 3 hot?");
    });
    await waitFor(() => expect(result.current.log.streaming).toBe(true));

    // Hit Stop — the button must drop out of streaming synchronously, not
    // wait for the (hung) stream to unwind.
    act(() => result.current.cancel());

    expect(result.current.log.streaming).toBe(false);
    expect(cancelSpy).toHaveBeenCalledWith("rca", "inv-1");
  });

  it("undo removes turns server-side then re-snapshots the thread (#38)", async () => {
    vi.spyOn(api, "getCurrentUser").mockResolvedValue("tester");
    // Keep the always-on broadcast subscription from hitting the network.
    vi.spyOn(api, "subscribeInvestigation").mockImplementation(
      // eslint-disable-next-line require-yield
      async function* () {
        await new Promise(() => {}); // idle
      },
    );
    const undoSpy = vi.spyOn(api, "undoTurns").mockResolvedValue({ message_count: 1 });
    vi.spyOn(api, "getConversation").mockResolvedValue({
      resource_id: "c",
      investigation_id: "inv-u",
      messages: [{ role: "user", content: "kept" }],
    });

    const { result } = renderHook(() => useAgentInternal("inv-u"));
    await act(async () => {
      await result.current.undo(2);
    });

    expect(undoSpy).toHaveBeenCalledWith("rca", "inv-u", 2);
    expect(
      result.current.log.entries.some(
        (e) => e.kind === "message" && e.message.content === "kept",
      ),
    ).toBe(true);
  });
});
