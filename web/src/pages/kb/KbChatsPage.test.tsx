// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render as rtlRender, screen, waitFor, within } from "@testing-library/react";

import { QueryWrap } from "../../test/queryWrapper";
import { DialogProvider } from "../../components/Dialog";

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

  it("explains what Chats are with a one-line lead (#162)", async () => {
    const client = { ...mockKbApi, listChats: async () => [] } as typeof mockKbApi;
    render(<KbChatsPage client={client} />);
    expect(
      await screen.findByText(
        "Ask questions across your collections. Every answer cites the documents it came from.",
      ),
    ).toBeInTheDocument();
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

  it("confirms before deleting, and only deletes once confirmed (#456)", async () => {
    await mockKbApi.createChat("Doomed", []);
    const deleteChat = vi.fn((id: string) => mockKbApi.deleteChat(id));
    const client = { ...mockKbApi, deleteChat } as typeof mockKbApi;
    render(
      <DialogProvider>
        <KbChatsPage client={client} />
      </DialogProvider>,
    );
    await userEvent.click(await screen.findByRole("button", { name: /Delete Doomed/ }));
    // a confirm dialog appears and NOTHING is deleted yet
    const dialog = await screen.findByRole("dialog");
    expect(deleteChat).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: /Delete Doomed/ })).toBeInTheDocument();
    // confirming the destructive action deletes the conversation
    await userEvent.click(within(dialog).getByRole("button", { name: /^Delete$/ }));
    await waitFor(() => expect(deleteChat).toHaveBeenCalled());
    await waitFor(() =>
      expect(screen.queryByRole("button", { name: /Delete Doomed/ })).not.toBeInTheDocument(),
    );
  });

  it("does not delete when the confirm is cancelled (#456)", async () => {
    await mockKbApi.createChat("Doomed", []);
    const deleteChat = vi.fn((id: string) => mockKbApi.deleteChat(id));
    const client = { ...mockKbApi, deleteChat } as typeof mockKbApi;
    render(
      <DialogProvider>
        <KbChatsPage client={client} />
      </DialogProvider>,
    );
    await userEvent.click(await screen.findByRole("button", { name: /Delete Doomed/ }));
    await userEvent.click(
      within(await screen.findByRole("dialog")).getByRole("button", { name: /Cancel/ }),
    );
    expect(deleteChat).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: /Delete Doomed/ })).toBeInTheDocument();
  });

  it("starts a new chat", async () => {
    const onNewChat = vi.fn();
    render(<KbChatsPage client={mockKbApi} onNewChat={onNewChat} />);
    await userEvent.click(screen.getByRole("button", { name: /new chat/i }));
    expect(onNewChat).toHaveBeenCalled();
  });

  it("labels an unnamed chat by its first user message (#357)", async () => {
    const client = {
      ...mockKbApi,
      listChats: async () => [
        {
          resource_id: "chat:u1",
          title: "", // unnamed
          collection_ids: [],
          message_count: 2,
          owner: "default-user",
          shared_with: [],
          name_hint: "why is my reflow oven drifting",
          updated_ms: 1000,
        },
      ],
    } as typeof mockKbApi;
    render(<KbChatsPage client={client} />);
    expect(
      await screen.findByRole("button", { name: /^why is my reflow oven drifting/ }),
    ).toBeInTheDocument();
  });

  it("sorts chats by recency — most recently updated first (#357)", async () => {
    // Titles chosen so alphabetical order (Apple < Zebra) DISAGREES with recency
    // order (Zebra is newer) — proving the sort is by updated_ms, not by title.
    const client = {
      ...mockKbApi,
      listChats: async () => [
        { resource_id: "chat:old", title: "Apple", collection_ids: [], message_count: 1, owner: "default-user", shared_with: [], updated_ms: 1000 },
        { resource_id: "chat:new", title: "Zebra", collection_ids: [], message_count: 1, owner: "default-user", shared_with: [], updated_ms: 9000 },
      ],
    } as typeof mockKbApi;
    render(<KbChatsPage client={client} />);
    await screen.findByRole("button", { name: /^Zebra/ });
    const titles = screen.getAllByRole("button", { name: /^(Zebra|Apple)/ }).map((b) => b.textContent);
    expect(titles[0]).toMatch(/^Zebra/); // newest first
    expect(titles[1]).toMatch(/^Apple/);
  });

  it("renames a chat inline via the pencil, updating the row (#357)", async () => {
    await mockKbApi.createChat("Draft title", []);
    const renameSpy = vi.spyOn(mockKbApi, "renameChat");
    render(<KbChatsPage client={mockKbApi} />);

    await userEvent.click(await screen.findByRole("button", { name: /Rename Draft title/ }));
    const input = await screen.findByRole("textbox");
    await userEvent.clear(input);
    await userEvent.type(input, "Final title{Enter}");

    await waitFor(() =>
      expect(renameSpy).toHaveBeenCalledWith(expect.any(String), "Final title"),
    );
    expect(await screen.findByRole("button", { name: /^Final title/ })).toBeInTheDocument();
  });

  it("cancels an inline rename on Escape without calling the API (#357)", async () => {
    await mockKbApi.createChat("Keep me", []);
    const renameSpy = vi.spyOn(mockKbApi, "renameChat");
    render(<KbChatsPage client={mockKbApi} />);

    await userEvent.click(await screen.findByRole("button", { name: /Rename Keep me/ }));
    const input = await screen.findByRole("textbox");
    await userEvent.clear(input);
    await userEvent.type(input, "Discarded{Escape}");

    // the edit is abandoned: no API call, original label intact
    expect(renameSpy).not.toHaveBeenCalled();
    expect(await screen.findByRole("button", { name: /^Keep me/ })).toBeInTheDocument();
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
