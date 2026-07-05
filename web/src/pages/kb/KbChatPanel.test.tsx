// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render as rtlRender, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
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

  it("renders the collection pills as interactive chips, not passive tags (#466)", async () => {
    render(<KbChatPanel chatId={null} client={panelClient(EIGHT)} />);
    await screen.findByText("Coll 1");
    // A toggle pill must opt into the interactive `kb-chip--btn` chrome so it
    // doesn't look identical to a static metadata label (the ① affordance bug).
    expect(pill("Coll 1").className).toContain("kb-chip--btn");
    expect(screen.getByTestId("kb-collections-more").className).toContain("kb-chip--btn");
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

// #397: the "回報有誤" button + dialog on a wiki-backed assistant answer.
describe("KbChatPanel wiki correction (#397)", () => {
  const msg = (role: "user" | "assistant", content: string) => ({
    role,
    content,
    reasoning: null,
    tool_name: null,
    tool_args: null,
    tool_call_id: null,
    created_at: 0,
    citations: [],
  });
  const chatWith = (messages: ReturnType<typeof msg>[]) => ({
    resource_id: "c1",
    title: "",
    collection_ids: ["c1"],
    owner: "default-user",
    shared_with: [],
    messages,
  });

  it("shows 回報有誤 on an assistant answer when the collection has a wiki, and opens the dialog", async () => {
    const client = panelClient([coll({ resource_id: "c1", name: "C1", use_wiki: true })], [], {
      getChat: async () => chatWith([msg("user", "When founded?"), msg("assistant", "In 1989.")]),
    });
    render(
      <KbChatPanel chatId="c1" collectionIds={["c1"]} hideCollectionPicker client={client} />,
    );
    const btn = await screen.findByRole("button", { name: /回報有誤/ });
    await userEvent.click(btn);
    // the drafting dialog opens
    expect(await screen.findByRole("dialog", { name: /回報 wiki 有誤/ })).toBeInTheDocument();
  });

  it("hides 回報有誤 when the collection has no wiki (Q13)", async () => {
    const client = panelClient([coll({ resource_id: "c1", name: "C1", use_wiki: false })], [], {
      getChat: async () => chatWith([msg("user", "q"), msg("assistant", "a")]),
    });
    render(
      <KbChatPanel chatId="c1" collectionIds={["c1"]} hideCollectionPicker client={client} />,
    );
    await screen.findByText("a"); // the assistant answer rendered
    expect(screen.queryByRole("button", { name: /回報有誤/ })).not.toBeInTheDocument();
  });
});
