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
import type { KbChatSummary } from "../../api/kb";
import { mockKbApi, _resetKbMock } from "../../api/kbMock";
import { KbChatsPage } from "./KbChatsPage";

/** A client whose chat list never resolves — keeps the page in its loading state. */
const pendingChats = () =>
  ({ ...mockKbApi, listChats: () => new Promise<KbChatSummary[]>(() => {}) }) as typeof mockKbApi;

function makeDeferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((r) => (resolve = r));
  return { promise, resolve };
}

describe("KbChatsPage", () => {
  beforeEach(() => _resetKbMock());
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("shows a loading placeholder while the chat list is still fetching — not the empty copy", () => {
    render(<KbChatsPage client={pendingChats()} />);
    expect(screen.getByTestId("kb-chats-loading")).toBeInTheDocument();
    expect(screen.queryByText(/No conversations yet/)).not.toBeInTheDocument();
  });

  it("shows the empty copy only once loading resolves with no chats", async () => {
    render(<KbChatsPage client={mockKbApi} />);
    expect(await screen.findByText(/No conversations yet/)).toBeInTheDocument();
    expect(screen.queryByTestId("kb-chats-loading")).not.toBeInTheDocument();
  });

  it("lists chats and opens one", async () => {
    const onOpenChat = vi.fn();
    const chat = await mockKbApi.createChat("Void thresholds", ["col-1"]);
    render(<KbChatsPage client={mockKbApi} onOpenChat={onOpenChat} />);

    const row = await screen.findByRole("button", { name: /^Void thresholds/ });
    await userEvent.click(row);
    expect(onOpenChat).toHaveBeenCalledWith(chat.resource_id);
  });

  it("disables only the in-flight row's delete button, leaving other rows clickable", async () => {
    await mockKbApi.createChat("Doomed", []);
    await mockKbApi.createChat("Safe", []);
    const d = makeDeferred<void>();
    const client = { ...mockKbApi, deleteChat: () => d.promise } as typeof mockKbApi;
    render(<KbChatsPage client={client} />);
    const del = await screen.findByRole("button", { name: /Delete Doomed/ });
    await userEvent.click(del);
    await waitFor(() => expect(del).toBeDisabled());
    // a sibling row stays enabled — only the deleting row guards against double-submit
    expect(screen.getByRole("button", { name: /Delete Safe/ })).toBeEnabled();
    d.resolve(); // let the in-flight mutation settle so nothing dangles
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
