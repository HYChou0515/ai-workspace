// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render as rtlRender, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { KbApi, KbChatSummary, KbCollection, SendKbMessageArgs } from "../../api/kb";
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
  is_global: false,
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

// Global collections (system-wide baseline scope): pre-checked with a "Global"
// badge; the create payload splits into collection_ids (checked non-globals) +
// excluded_collection_ids (un-checked globals).
describe("KbChatPanel global collections", () => {
  const captureCreate = () =>
    vi.fn(async (_title: string, ids: string[], excluded: string[] = []) => ({
      resource_id: "c-new",
      title: "",
      collection_ids: ids,
      excluded_collection_ids: excluded,
      message_count: 0,
      owner: "default-user",
      shared_with: [],
    }));

  const sendClient = (collections: KbCollection[], createChat: ReturnType<typeof captureCreate>) =>
    panelClient(collections, [], {
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

  const GLOBAL = coll({ resource_id: "g1", name: "Baseline", is_global: true, cited: 100 });
  const NORMAL = [
    coll({ resource_id: "c1", name: "Coll 1", cited: 9 }),
    coll({ resource_id: "c2", name: "Coll 2", cited: 8 }),
  ];

  const sendHello = () => {
    fireEvent.change(screen.getByPlaceholderText("Ask the knowledge base…"), {
      target: { value: "hello" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Send/ }));
  };

  it("pre-checks a global and keeps it in scope (not excluded) on create", async () => {
    const createChat = captureCreate();
    render(<KbChatPanel chatId={null} client={sendClient([GLOBAL, ...NORMAL], createChat)} />);
    await screen.findByText("Coll 1");
    // The global collection starts checked (in the baseline scope).
    expect(pill("Baseline")).toHaveAttribute("aria-pressed", "true");
    sendHello();
    await waitFor(() => expect(createChat).toHaveBeenCalled());
    const [, ids, excluded] = createChat.mock.calls[0];
    // collection_ids = the checked NON-global collections only.
    expect([...ids].sort()).toEqual(["c1", "c2"]);
    // Nothing excluded — the global stays in scope.
    expect(excluded).toEqual([]);
  });

  it("un-checking a global excludes it (→ excluded_collection_ids) on create", async () => {
    const createChat = captureCreate();
    render(<KbChatPanel chatId={null} client={sendClient([GLOBAL, ...NORMAL], createChat)} />);
    await screen.findByText("Coll 1");
    fireEvent.click(pill("Baseline")); // un-check the global
    expect(pill("Baseline")).toHaveAttribute("aria-pressed", "false");
    sendHello();
    await waitFor(() => expect(createChat).toHaveBeenCalled());
    const [, ids, excluded] = createChat.mock.calls[0];
    expect([...ids].sort()).toEqual(["c1", "c2"]);
    expect(excluded).toEqual(["g1"]);
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

describe("KbChatPanel image attach (#513 P10)", () => {
  // A typed streamMessage spy so `.mock.calls[0][0]` carries SendKbMessageArgs.
  const streamSpy = () => vi.fn((_args: SendKbMessageArgs) => (async function* () {})());

  const stageImage = async (bytes: number[], name: string, mime = "image/png") => {
    const file = new File([new Uint8Array(bytes)], name, { type: mime });
    await act(async () => {
      fireEvent.change(screen.getByTestId("kb-image-input"), { target: { files: [file] } });
    });
  };

  it("stages an attached image and forwards it (base64) on send", async () => {
    const stream = streamSpy();
    const client = panelClient([coll({})], [], { streamMessage: stream });
    render(<KbChatPanel chatId={null} collectionIds={["c1"]} client={client} />);

    await stageImage([1, 2, 3], "defect.png");
    expect(await screen.findByText("defect.png")).toBeInTheDocument(); // a preview chip appears

    await userEvent.type(screen.getByPlaceholderText(/Ask the knowledge base/i), "what is this?");
    await userEvent.click(screen.getByRole("button", { name: /Send/i }));

    await waitFor(() => expect(stream).toHaveBeenCalled());
    const arg = stream.mock.calls[0][0];
    expect(arg.content).toBe("what is this?");
    expect(arg.image).toEqual({ data: btoa(String.fromCharCode(1, 2, 3)), mime: "image/png" });
  });

  it("clears the composer's staged image after sending", async () => {
    const stream = streamSpy();
    const client = panelClient([coll({})], [], { streamMessage: stream });
    render(<KbChatPanel chatId={null} collectionIds={["c1"]} client={client} />);

    await stageImage([1], "one.png");
    await userEvent.type(screen.getByPlaceholderText(/Ask the knowledge base/i), "q");
    await userEvent.click(screen.getByRole("button", { name: /Send/i }));

    await waitFor(() => expect(stream).toHaveBeenCalled());
    expect(screen.queryByText("one.png")).not.toBeInTheDocument(); // chip cleared for the next turn
  });

  it("removes a staged image on request", async () => {
    const client = panelClient([coll({})], [], { streamMessage: vi.fn(async function* () {}) });
    render(<KbChatPanel chatId={null} collectionIds={["c1"]} client={client} />);

    await stageImage([9], "d.png");
    expect(await screen.findByText("d.png")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Remove image/i }));
    expect(screen.queryByText("d.png")).not.toBeInTheDocument();
  });
});
