// @vitest-environment happy-dom
import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { AgentEvent } from "../events";
import type { ItemChat, ItemChatApi } from "../api/itemChats";
import { QueryWrap } from "../test/queryWrapper";
import { useItemChat } from "./useItemChat";

const CHAT: ItemChat = { chatId: "conversation:c1", title: "Free", runId: null, messages: [] };

function fakeClient(over: Partial<ItemChatApi> = {}): ItemChatApi {
  return {
    listChats: vi.fn(),
    createChat: vi.fn(),
    getChat: vi.fn().mockResolvedValue(CHAT),
    sendMessage: vi.fn().mockResolvedValue(undefined),
    // hangs forever unless overridden — the steady-state subscription
    subscribe: async function* () {
      await new Promise<void>(() => {});
    },
    cancelMessage: vi.fn().mockResolvedValue(undefined),
    ...over,
  } as ItemChatApi;
}

const render = (client: ItemChatApi) =>
  renderHook(
    () => useItemChat({ slug: "topic-hub", itemId: "it", chatId: "conversation:c1", client }),
    { wrapper: QueryWrap },
  );

describe("useItemChat", () => {
  it("hydrates the log from the chat's persisted messages", async () => {
    const client = fakeClient({
      getChat: vi.fn().mockResolvedValue({
        ...CHAT,
        messages: [{ role: "user", content: "earlier" }],
      }),
    });
    const { result } = render(client);
    await waitFor(() => expect(result.current.log.entries.length).toBe(1));
  });

  it("send flips streaming on and enqueues to THIS chat", async () => {
    // Seed a prior message so we can wait for hydration to settle before sending
    // (mirrors the real app: the thread hydrates on mount, before the user types).
    const client = fakeClient({
      getChat: vi.fn().mockResolvedValue({ ...CHAT, messages: [{ role: "user", content: "prior" }] }),
    });
    const { result } = render(client);
    await waitFor(() => expect(result.current.log.entries.length).toBe(1));
    await act(async () => {
      await result.current.send("question");
    });
    expect(client.sendMessage).toHaveBeenCalledWith(
      expect.objectContaining({
        slug: "topic-hub",
        itemId: "it",
        chatId: "conversation:c1",
        content: "question",
      }),
    );
    expect(result.current.log.streaming).toBe(true);
  });

  it("cancel tells the backend to stop THIS chat and clears streaming immediately", async () => {
    const client = fakeClient({
      getChat: vi.fn().mockResolvedValue({ ...CHAT, messages: [{ role: "user", content: "prior" }] }),
    });
    const { result } = render(client);
    await waitFor(() => expect(result.current.log.entries.length).toBe(1));
    await act(async () => {
      await result.current.send("q");
    });
    act(() => result.current.cancel());
    expect(client.cancelMessage).toHaveBeenCalledWith("topic-hub", "it", "conversation:c1");
    expect(result.current.log.streaming).toBe(false);
  });

  it("folds the chat's stream events into the log and re-snapshots on done", async () => {
    const events: AgentEvent[] = [
      { type: "message_delta", text: "Hello from the workflow chat." },
      { type: "done" },
    ];
    const persisted: ItemChat = {
      ...CHAT,
      messages: [{ role: "assistant", content: "Hello from the workflow chat." }],
    };
    const getChat = vi
      .fn()
      .mockResolvedValueOnce(CHAT) // initial hydrate (empty)
      .mockResolvedValue(persisted); // re-snapshot after `done`
    const client = fakeClient({
      getChat,
      subscribe: async function* () {
        for (const ev of events) yield ev;
      },
    });
    const { result } = render(client);
    await waitFor(() =>
      expect(
        result.current.log.entries.some(
          (e) => e.kind === "message" && e.message.content.includes("workflow chat"),
        ),
      ).toBe(true),
    );
  });
});
