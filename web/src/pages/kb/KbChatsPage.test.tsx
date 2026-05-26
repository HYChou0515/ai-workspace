// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render as rtlRender, screen, waitFor } from "@testing-library/react";

import { QueryWrap } from "../../test/queryWrapper";

// KB views read through TanStack Query — wrap every render with a client.
const render = (ui: Parameters<typeof rtlRender>[0]) =>
  rtlRender(ui, { wrapper: QueryWrap });
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "../../api";
import { mockKbApi, _resetKbMock } from "../../api/kbMock";
import { KbChatsPage } from "./KbChatsPage";

describe("KbChatsPage", () => {
  beforeEach(() => _resetKbMock());
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("lists chats and opens one", async () => {
    const onOpenChat = vi.fn();
    const chat = await mockKbApi.createChat("Void thresholds", ["col-1"]);
    render(<KbChatsPage client={mockKbApi} onOpenChat={onOpenChat} />);

    const row = await screen.findByRole("button", { name: /^Void thresholds/ });
    await userEvent.click(row);
    expect(onOpenChat).toHaveBeenCalledWith(chat.resource_id);
  });

  it("deletes a chat", async () => {
    await mockKbApi.createChat("Doomed", []);
    render(<KbChatsPage client={mockKbApi} />);
    const del = await screen.findByRole("button", { name: /Delete Doomed/ });
    await userEvent.click(del);
    await waitFor(() =>
      expect(screen.queryByRole("button", { name: /Doomed/ })).not.toBeInTheDocument(),
    );
  });

  it("starts a new chat", async () => {
    const onNewChat = vi.fn();
    render(<KbChatsPage client={mockKbApi} onNewChat={onNewChat} />);
    await userEvent.click(screen.getByRole("button", { name: /new chat/i }));
    expect(onNewChat).toHaveBeenCalled();
  });

  it("separates owned chats from ones shared with me (read-only)", async () => {
    // current user is "default-user" (mock); this chat is owned by alice → shared.
    vi.spyOn(api, "getUsers").mockResolvedValue([
      { id: "alice", name: "Alice Chen", section: "Reflow", email: "", photo_url: null },
    ]);
    const client = {
      ...mockKbApi,
      listChats: async () => [
        {
          resource_id: "chat:s1",
          title: "Alice's research",
          collection_ids: [],
          message_count: 3,
          owner: "alice",
          shared_with: ["default-user"],
        },
      ],
    } as typeof mockKbApi;
    render(<KbChatsPage client={client} />);

    // the open button starts with the title (the pin button is "Pin <title>")
    expect(await screen.findByRole("button", { name: /^Alice's research/ })).toBeInTheDocument();
    expect(screen.getByText("Shared with me")).toBeInTheDocument(); // the tab
    expect(await screen.findByText("Alice Chen")).toBeInTheDocument(); // owner resolved async
    // a shared (non-owned) chat has no delete/share controls
    expect(screen.queryByRole("button", { name: /Delete Alice's research/ })).toBeNull();
  });
});
