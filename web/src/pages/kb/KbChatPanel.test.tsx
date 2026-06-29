// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render as rtlRender, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { KbApi, KbChatSummary, KbCollection } from "../../api/kb";
import { mockKbApi } from "../../api/kbMock";
import { QueryWrap } from "../../test/queryWrapper";
import { KbChatPanel } from "./KbChatPanel";

const render = (ui: Parameters<typeof rtlRender>[0]) => rtlRender(ui, { wrapper: QueryWrap });

afterEach(cleanup);

const coll = (over: Partial<KbCollection>): KbCollection => ({
  resource_id: "c1",
  name: "C1",
  description: "",
  icon: "layers",
  cited: 0,
  doc_count: 0,
  size: 0,
  tokens: 0,
  updated_at: 0,
  owner: "default-user",
  use_rag: true,
  use_wiki: false,
  wiki_maintainer_guidance: "",
  wiki_reader_guidance: "",
  ...over,
});

// 8 collections, distinct cited so the cold-start ranking is c1>c2>…>c8.
const EIGHT: KbCollection[] = Array.from({ length: 8 }, (_, i) =>
  coll({ resource_id: `c${i + 1}`, name: `Coll ${i + 1}`, cited: 8 - i }),
);

function panelClient(
  collections: KbCollection[],
  chats: KbChatSummary[] = [],
  over: Partial<KbApi> = {},
): KbApi {
  return {
    ...mockKbApi,
    listCollections: async () => collections,
    listChats: async () => chats,
    ...over,
  };
}

// A pill is on when its toggle button is aria-pressed.
const pill = (name: string) => screen.getByRole("button", { name: new RegExp(name) });

describe("KbChatPanel collection picker (#271)", () => {
  it("shows the top-6 ranked collections as pills, all selected by default", async () => {
    render(<KbChatPanel chatId={null} client={panelClient(EIGHT)} />);
    await screen.findByText("Coll 1");
    for (const n of [1, 2, 3, 4, 5, 6]) {
      expect(pill(`Coll ${n}`)).toHaveAttribute("aria-pressed", "true");
    }
    // The 7th/8th (lower-ranked) are NOT pills — they live behind the modal.
    expect(screen.queryByText("Coll 7")).not.toBeInTheDocument();
    expect(screen.queryByText("Coll 8")).not.toBeInTheDocument();
  });

  it("shows no 'more' button when there are 6 or fewer collections", async () => {
    render(<KbChatPanel chatId={null} client={panelClient(EIGHT.slice(0, 5))} />);
    await screen.findByText("Coll 1");
    expect(screen.queryByTestId("kb-collections-more")).not.toBeInTheDocument();
  });

  it("opens the modal from the 'more' button, revealing the hidden collections", async () => {
    render(<KbChatPanel chatId={null} client={panelClient(EIGHT)} />);
    await screen.findByText("Coll 1");
    expect(screen.getByTestId("kb-collections-more")).toHaveTextContent("6");
    fireEvent.click(screen.getByTestId("kb-collections-more"));
    await screen.findByTestId("kb-collections-dialog");
    // The modal lists the full set, including the ones not shown as pills.
    expect(screen.getByTestId("collection-row-c7")).toBeInTheDocument();
    expect(screen.getByTestId("collection-row-c8")).toBeInTheDocument();
  });

  it("updates the 'more' count when a hidden collection is checked in the modal", async () => {
    render(<KbChatPanel chatId={null} client={panelClient(EIGHT)} />);
    await screen.findByText("Coll 1");
    fireEvent.click(screen.getByTestId("kb-collections-more"));
    await screen.findByTestId("kb-collections-dialog");
    expect(screen.getByTestId("collection-check-c7")).not.toBeChecked();
    fireEvent.click(screen.getByTestId("collection-check-c7"));
    expect(screen.getByTestId("kb-collections-more")).toHaveTextContent("7");
    expect(screen.getByTestId("collection-check-c7")).toBeChecked();
  });

  it("hides the collection picker when locked to a fixed collection set (#230)", async () => {
    const client = panelClient(EIGHT);
    // Control render proves the pills DO appear once collections load…
    const { rerender } = render(<KbChatPanel chatId={null} client={client} />);
    await screen.findByText("Coll 1");
    expect(screen.getByText("Search in")).toBeInTheDocument();
    // …so when we lock to a fixed set, their absence is meaningful, not a race.
    rerender(
      <KbChatPanel chatId={null} collectionIds={["help-1"]} hideCollectionPicker client={client} />,
    );
    await waitFor(() => expect(screen.queryByText("Search in")).not.toBeInTheDocument());
    expect(screen.queryByText("Coll 1")).not.toBeInTheDocument();
    expect(screen.queryByTestId("kb-collections-more")).not.toBeInTheDocument();
  });

  it("creates the chat scoped to the fixed collection set on the first message (#230)", async () => {
    const createChat = vi.fn(async (_title: string, ids: string[]) => ({
      resource_id: "c-new",
      title: "",
      collection_ids: ids,
      message_count: 0,
      owner: "default-user",
      shared_with: [],
    }));
    const client = panelClient(EIGHT, [], {
      createChat,
      streamMessage: async function* () {},
      getChat: async () => ({
        resource_id: "c-new",
        title: "",
        collection_ids: [],
        owner: "default-user",
        shared_with: [],
        messages: [],
      }),
    });
    render(
      <KbChatPanel
        chatId={null}
        collectionIds={["help-1"]}
        hideCollectionPicker
        client={client}
      />,
    );
    fireEvent.change(screen.getByPlaceholderText("Ask the knowledge base…"), {
      target: { value: "hello" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Send/ }));
    await waitFor(() => expect(createChat).toHaveBeenCalled());
    expect(createChat.mock.calls[0][1]).toEqual(["help-1"]);
  });

  it("creates the chat with the selected collection set on the first message", async () => {
    const createChat = vi.fn(async (_title: string, ids: string[]) => ({
      resource_id: "c-new",
      title: "",
      collection_ids: ids,
      message_count: 0,
      owner: "default-user",
      shared_with: [],
    }));
    const client = panelClient(EIGHT, [], {
      createChat,
      streamMessage: async function* () {},
      getChat: async () => ({
        resource_id: "c-new",
        title: "",
        collection_ids: [],
        owner: "default-user",
        shared_with: [],
        messages: [],
      }),
    });
    render(<KbChatPanel chatId={null} client={client} />);
    await screen.findByText("Coll 1");
    fireEvent.change(screen.getByPlaceholderText("Ask the knowledge base…"), {
      target: { value: "hello" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Send/ }));
    await waitFor(() => expect(createChat).toHaveBeenCalled());
    const ids = createChat.mock.calls[0][1];
    expect([...ids].sort()).toEqual(["c1", "c2", "c3", "c4", "c5", "c6"]);
  });
});
