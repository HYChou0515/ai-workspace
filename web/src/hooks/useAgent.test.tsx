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

  it("auto-reconnects and re-hydrates after the SSE stream drops (#493)", async () => {
    vi.spyOn(api, "getCurrentUser").mockResolvedValue("tester");
    let subCalls = 0;
    const subSpy = vi.spyOn(api, "subscribeInvestigation").mockImplementation(
      // eslint-disable-next-line require-yield
      async function* () {
        subCalls += 1;
        if (subCalls === 1) throw new Error("stream failed: 504"); // first stream drops
        await new Promise(() => {}); // reconnected stream stays open
      },
    );
    const base = { resource_id: "c", investigation_id: "inv-r" };
    const recovered = {
      ...base,
      messages: [
        { role: "user" as const, content: "q" },
        { role: "assistant" as const, content: "recovered after reconnect" },
      ],
    };
    vi.spyOn(api, "getConversation")
      // initial hydration (useQuery) — nothing recovered yet
      .mockResolvedValueOnce({ ...base, messages: [{ role: "user", content: "q" }] })
      // reconnect re-hydrate surfaces the turn that finished during the gap
      .mockResolvedValue(recovered);

    const { result } = renderHook(() => useAgentInternal("inv-r"));

    // The dropped stream backs off, re-subscribes, and re-hydrates the thread.
    await waitFor(() => expect(subSpy.mock.calls.length).toBeGreaterThanOrEqual(2), {
      timeout: 4000,
    });
    await waitFor(() =>
      expect(
        result.current.log.entries.some(
          (e) => e.kind === "message" && e.message.content.includes("recovered after reconnect"),
        ),
      ).toBe(true),
    );
  });

  it("a 504 on send does not fail the turn — it stays streaming (#493)", async () => {
    vi.spyOn(api, "getCurrentUser").mockResolvedValue("tester");
    // A hydrated thread (ends on a user msg → not "done"), so the seed runs before
    // send and the store-poll won't clear streaming during the assertion window.
    vi.spyOn(api, "getConversation").mockResolvedValue({
      resource_id: "c",
      investigation_id: "inv-g",
      messages: [{ role: "user", content: "earlier" }],
    });
    vi.spyOn(api, "subscribeInvestigation").mockImplementation(
      // eslint-disable-next-line require-yield
      async function* () {
        await new Promise(() => {}); // idle
      },
    );
    // A gateway timeout: the POST was cut but the turn may be running server-side.
    const gwErr = Object.assign(new Error("messages failed: 504"), { status: 504 });
    vi.spyOn(api, "sendMessage").mockRejectedValue(gwErr);

    const { result } = renderHook(() => useAgentInternal("inv-g"));
    await waitFor(() => expect(result.current.log.entries.length).toBeGreaterThan(0)); // hydrated
    await act(async () => {
      await result.current.send("q");
    });
    // Still "working…" (the stream / store-poll will surface the reply), NOT errored.
    expect(result.current.log.streaming).toBe(true);
    expect(result.current.log.error).toBeNull();
  });

  it("a non-gateway send error surfaces as a turn error (#493)", async () => {
    vi.spyOn(api, "getCurrentUser").mockResolvedValue("tester");
    vi.spyOn(api, "getConversation").mockResolvedValue({
      resource_id: "c",
      investigation_id: "inv-b",
      messages: [{ role: "user", content: "earlier" }],
    });
    vi.spyOn(api, "subscribeInvestigation").mockImplementation(
      // eslint-disable-next-line require-yield
      async function* () {
        await new Promise(() => {}); // idle
      },
    );
    const badReq = Object.assign(new Error("messages failed: 400"), { status: 400 });
    vi.spyOn(api, "sendMessage").mockRejectedValue(badReq);

    const { result } = renderHook(() => useAgentInternal("inv-b"));
    await waitFor(() => expect(result.current.log.entries.length).toBeGreaterThan(0)); // hydrated
    await act(async () => {
      await result.current.send("q");
    });
    expect(result.current.log.streaming).toBe(false);
    expect(result.current.log.error).toContain("400");
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
