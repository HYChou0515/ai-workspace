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
    undoTurns: vi.fn().mockResolvedValue(undefined),
    mention: vi.fn().mockResolvedValue(undefined),
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
    // KB enhancements (search depth + wiki flag) ride along on every turn, like
    // useAgent — the call carries an `enhancements` key (value may be undefined
    // when no selection is stored, as in this test's clean localStorage).
    const sentBody = (client.sendMessage as ReturnType<typeof vi.fn>).mock.calls[0][0];
    expect("enhancements" in sentBody).toBe(true);
    expect(result.current.log.streaming).toBe(true);
  });

  it("undo drops turns on THIS chat and re-snapshots the log", async () => {
    const undone: ItemChat = {
      ...CHAT,
      messages: [{ role: "user", content: "kept" }],
    };
    const getChat = vi
      .fn()
      .mockResolvedValueOnce({ ...CHAT, messages: [{ role: "user", content: "old" }] })
      .mockResolvedValue(undone); // re-snapshot after undo
    const client = fakeClient({ getChat });
    const { result } = render(client);
    await waitFor(() => expect(result.current.log.entries.length).toBe(1));
    await act(async () => {
      await result.current.undo(1);
    });
    expect(client.undoTurns).toHaveBeenCalledWith("topic-hub", "it", "conversation:c1", 1);
    await waitFor(() =>
      expect(
        result.current.log.entries.some(
          (e) => e.kind === "message" && e.message.content === "kept",
        ),
      ).toBe(true),
    );
  });

  it("undo is a no-op for a non-positive count", async () => {
    const client = fakeClient();
    const { result } = render(client);
    await act(async () => {
      await result.current.undo(0);
    });
    expect(client.undoTurns).not.toHaveBeenCalled();
  });

  it("mention notifies the item (not the chat) and adds an optimistic entry", async () => {
    // Seed a prior message + wait for hydration so the mention's optimistic entry
    // isn't clobbered by the on-mount re-snapshot (mirrors the real app order).
    const client = fakeClient({
      getChat: vi.fn().mockResolvedValue({ ...CHAT, messages: [{ role: "user", content: "prior" }] }),
    });
    const { result } = render(client);
    await waitFor(() => expect(result.current.log.entries.length).toBe(1));
    await act(async () => {
      await result.current.mention(["bob"], "look at this");
    });
    expect(client.mention).toHaveBeenCalledWith("topic-hub", "it", ["bob"], "look at this");
    expect(
      result.current.log.entries.some((e) => e.kind === "mention"),
    ).toBe(true);
  });

  it("mention is a no-op with no users", async () => {
    const client = fakeClient();
    const { result } = render(client);
    await act(async () => {
      await result.current.mention([], "note");
    });
    expect(client.mention).not.toHaveBeenCalled();
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
