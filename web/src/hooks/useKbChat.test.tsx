// @vitest-environment happy-dom
import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { mockKbApi, _resetKbMock } from "../api/kbMock";
import type { AgentEntry } from "../pages/investigation/agentLog";
import { useKbChat } from "./useKbChat";

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
});
