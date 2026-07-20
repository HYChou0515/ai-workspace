// @vitest-environment happy-dom
import { act, renderHook as rtlRenderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { mockKbApi, _resetKbMock } from "../api/kbMock";
import type { AgentEntry } from "../pages/investigation/agentLog";
import { QueryWrap } from "../test/queryWrapper";
import { useKbChat } from "./useKbChat";

// useKbChat hydrates through TanStack Query — give every hook a client.
const renderHook = <T,>(cb: () => T) =>
  rtlRenderHook(cb, { wrapper: QueryWrap });

const assistantText = (entries: AgentEntry[]): string =>
  entries
    .filter((e) => e.kind === "message" && e.message.role === "assistant")
    .map((e) => (e.kind === "message" ? e.message.content : ""))
    .join("");

describe("useKbChat", () => {
  beforeEach(() => _resetKbMock());

  it("creates a thread on first send, streams, then loads the cited answer", async () => {
    const onChatCreated = vi.fn();
    const { result } = renderHook(() =>
      useKbChat({ collectionIds: ["col-1"], client: mockKbApi, onChatCreated }),
    );
    expect(result.current.chatId).toBeNull();

    await act(async () => {
      await result.current.send("why voids?");
    });

    expect(result.current.chatId).not.toBeNull();
    expect(onChatCreated).toHaveBeenCalledWith(result.current.chatId);
    expect(result.current.log.streaming).toBe(false);
    // the snapshot has a user message, a kb_search tool call, and the answer
    const kinds = result.current.log.entries.map((e) => e.kind);
    expect(kinds).toContain("tool_call");
    expect(assistantText(result.current.log.entries)).toContain("[1]");
    const answer = result.current.log.entries.find(
      (e) => e.kind === "message" && e.message.role === "assistant",
    );
    expect(answer?.kind === "message" && answer.message.citations?.[0]?.filename).toBe("reflow.md");
  });

  it("forwards an attached image to streamMessage (#513 P10)", async () => {
    const spy = vi.spyOn(mockKbApi, "streamMessage");
    const { result } = renderHook(() => useKbChat({ collectionIds: ["col-1"], client: mockKbApi }));
    const image = { data: "AQID", mime: "image/png" };

    await act(async () => {
      await result.current.send("what is this?", image);
    });

    expect(spy).toHaveBeenCalledWith(expect.objectContaining({ content: "what is this?", image }));
    spy.mockRestore();
  });

  it("sends an image-only message (no text) (#513 P10)", async () => {
    const spy = vi.spyOn(mockKbApi, "streamMessage");
    const { result } = renderHook(() => useKbChat({ collectionIds: ["col-1"], client: mockKbApi }));
    const image = { data: "AQID", mime: "image/png" };

    await act(async () => {
      await result.current.send("   ", image); // whitespace text but a real image
    });

    expect(spy).toHaveBeenCalledWith(expect.objectContaining({ image }));
    spy.mockRestore();
  });

  it("ignores empty input", async () => {
    const { result } = renderHook(() => useKbChat({ collectionIds: [], client: mockKbApi }));
    await act(async () => {
      await result.current.send("   ");
    });
    expect(result.current.chatId).toBeNull();
    expect(result.current.log.entries).toEqual([]);
  });

  it("hydrates an existing thread's history", async () => {
    const chat = await mockKbApi.createChat("t", ["col-1"]);
    for await (const _ of mockKbApi.streamMessage({ chatId: chat.resource_id, content: "q" }));

    const { result } = renderHook(() =>
      useKbChat({ collectionIds: ["col-1"], chatId: chat.resource_id, client: mockKbApi }),
    );
    await waitFor(() => expect(result.current.log.entries.length).toBeGreaterThan(0));
    expect(result.current.log.entries[0].kind).toBe("message");
  });

  it("reset clears the thread back to a fresh one", async () => {
    const { result } = renderHook(() => useKbChat({ collectionIds: ["col-1"], client: mockKbApi }));
    await act(async () => {
      await result.current.send("hello");
    });
    act(() => result.current.reset());
    expect(result.current.chatId).toBeNull();
    expect(result.current.log.entries).toEqual([]);
  });

  it("cancel tells the BE to tear down the in-flight turn", async () => {
    const spy = vi.spyOn(mockKbApi, "cancelMessage");
    const { result } = renderHook(() => useKbChat({ collectionIds: ["col-1"], client: mockKbApi }));
    await act(async () => {
      await result.current.send("hello"); // creates the thread → a turn to cancel
    });
    act(() => result.current.cancel());
    expect(spy).toHaveBeenCalledWith(result.current.chatId);
    spy.mockRestore();
  });

  it("cancel with no active thread only aborts locally (no BE call)", () => {
    const spy = vi.spyOn(mockKbApi, "cancelMessage");
    const { result } = renderHook(() => useKbChat({ collectionIds: [], client: mockKbApi }));
    act(() => result.current.cancel());
    expect(spy).not.toHaveBeenCalled();
    spy.mockRestore();
  });
});

describe("useKbChat — send failure", () => {
  beforeEach(() => _resetKbMock());

  // A failing stream must land in the log as a turn error. Swallowing it leaves
  // the composer unlocked with no explanation, which is indistinguishable from
  // "the model had nothing to say".
  it("surfaces a stream failure as a turn error and unlocks the composer", async () => {
    const client = {
      ...mockKbApi,
      createChat: vi.fn().mockResolvedValue({ resource_id: "kb-1" }),
      streamMessage: async function* () {
        throw new Error("stream failed: 503");
      },
    } as unknown as typeof mockKbApi;

    const { result } = renderHook(() => useKbChat({ collectionIds: ["c1"], client }));
    await act(async () => {
      await result.current.send("q");
    });

    expect(result.current.log.error).toContain("503");
    expect(result.current.log.streaming).toBe(false);
  });

  // An abort is the user pressing Stop or navigating away — not a failure.
  it("treats an abort as a cancellation, not an error", async () => {
    const client = {
      ...mockKbApi,
      createChat: vi.fn().mockResolvedValue({ resource_id: "kb-2" }),
      streamMessage: async function* () {
        throw Object.assign(new Error("aborted"), { name: "AbortError" });
      },
    } as unknown as typeof mockKbApi;

    const { result } = renderHook(() => useKbChat({ collectionIds: ["c1"], client }));
    await act(async () => {
      await result.current.send("q");
    });

    expect(result.current.log.error).toBeNull();
  });
});
