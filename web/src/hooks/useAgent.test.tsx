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

  it("recovers a stuck investigation when the broadcast is cross-pod silent (#202)", async () => {
    vi.spyOn(api, "getCurrentUser").mockResolvedValue("tester");
    vi.spyOn(api, "sendMessage").mockResolvedValue();
    // The viewer's /stream landed on a pod not running the turn → silent.
    vi.spyOn(api, "subscribeInvestigation").mockImplementation(
      // eslint-disable-next-line require-yield
      async function* () {
        await new Promise(() => {});
      },
    );
    const prior = {
      resource_id: "c",
      investigation_id: "inv-x",
      messages: [{ role: "assistant" as const, content: "earlier" }],
    };
    const running = {
      ...prior,
      messages: [...prior.messages, { role: "user" as const, content: "q" }],
    };
    const completed = {
      ...prior,
      messages: [
        ...running.messages,
        { role: "assistant" as const, content: "answer from the other pod" },
      ],
    };
    vi.spyOn(api, "getConversation")
      .mockResolvedValueOnce(prior)
      .mockResolvedValueOnce(running)
      .mockResolvedValue(completed);

    const { result } = renderHook(() => useAgentInternal("inv-x", 5));
    await waitFor(() => expect(result.current.log.entries.length).toBe(1));
    await act(async () => {
      await result.current.send("q");
    });
    await waitFor(() =>
      expect(
        result.current.log.entries.some(
          (e) => e.kind === "message" && e.message.content.includes("other pod"),
        ),
      ).toBe(true),
    );
    expect(result.current.log.streaming).toBe(false);
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
