// @vitest-environment happy-dom
import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { ItemChatApi, ItemChatSummary } from "../api/itemChats";
import { QueryWrap } from "../test/queryWrapper";
import { useItemChats } from "./useItemChats";

const DEFAULT_CHAT: ItemChatSummary = {
  chat_id: "conversation:c1",
  title: "",
  run_id: null,
  created_ms: null,
  message_count: 2,
  is_default: true,
  name_hint: "",
  status: null,
  last_activity_ms: null,
};

function fakeClient(over: Partial<ItemChatApi> = {}): ItemChatApi {
  return {
    listChats: vi.fn().mockResolvedValue([DEFAULT_CHAT]),
    createChat: vi.fn(),
    renameChat: vi.fn().mockResolvedValue(DEFAULT_CHAT),
    deleteChat: vi.fn().mockResolvedValue(undefined),
    getChat: vi.fn(),
    sendMessage: vi.fn(),
    subscribe: vi.fn(),
    cancelMessage: vi.fn(),
    ...over,
  } as ItemChatApi;
}

const render = (client: ItemChatApi) =>
  renderHook(() => useItemChats("topic-hub", "it", client), { wrapper: QueryWrap });

describe("useItemChats", () => {
  it("lists the item's chats (default flagged)", async () => {
    const { result } = render(fakeClient());
    await waitFor(() => expect(result.current.chats.length).toBe(1));
    expect(result.current.chats[0].is_default).toBe(true);
  });

  it("createFreeChat posts a new free chat", async () => {
    const created: ItemChatSummary = { ...DEFAULT_CHAT, chat_id: "conversation:c2", title: "side", is_default: false };
    const client = fakeClient({
      listChats: vi.fn().mockResolvedValue([]),
      createChat: vi.fn().mockResolvedValue(created),
    });
    const { result } = render(client);
    await waitFor(() => expect(result.current.isLoading).toBe(false));
    let out: ItemChatSummary | undefined;
    await act(async () => {
      out = await result.current.createFreeChat("side");
    });
    expect(client.createChat).toHaveBeenCalledWith("topic-hub", "it", "side");
    expect(out?.chat_id).toBe("conversation:c2");
  });

  it("renameChat patches a chat's title", async () => {
    const client = fakeClient();
    const { result } = render(client);
    await waitFor(() => expect(result.current.isLoading).toBe(false));
    await act(async () => {
      await result.current.renameChat("conversation:c1", "Yield study");
    });
    expect(client.renameChat).toHaveBeenCalledWith("topic-hub", "it", "conversation:c1", "Yield study");
  });

  it("deleteChat removes a chat", async () => {
    const client = fakeClient();
    const { result } = render(client);
    await waitFor(() => expect(result.current.isLoading).toBe(false));
    await act(async () => {
      await result.current.deleteChat("conversation:c1");
    });
    expect(client.deleteChat).toHaveBeenCalledWith("topic-hub", "it", "conversation:c1");
  });
});
