// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render as rtlRender, screen, waitFor } from "@testing-library/react";

import { QueryWrap } from "../../test/queryWrapper";

// KB views read through TanStack Query — wrap every render with a client.
const render = (ui: Parameters<typeof rtlRender>[0]) =>
  rtlRender(ui, { wrapper: QueryWrap });
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { _resetKbMock, mockKbApi } from "../../api/kbMock";
import { KbCollectionsPage } from "./KbCollectionsPage";

type Client = Parameters<typeof KbCollectionsPage>[0]["client"];

/** A collection object with all card fields (inline test clients). */
function col(over: Record<string, unknown>) {
  return {
    icon: "layers",
    description: "",
    cited: 0,
    doc_count: 0,
    size: 0,
    updated_at: Date.UTC(2026, 4, 20),
    owner: "alice",
    ...over,
  };
}

describe("KbCollectionsPage", () => {
  beforeEach(() => _resetKbMock());
  afterEach(cleanup);

  it("creates a collection card; opening it shows the upload affordances", async () => {
    render(<KbCollectionsPage client={mockKbApi} />);
    // "New collection" opens a modal; name is entered there, then created
    await userEvent.click(screen.getByRole("button", { name: /new collection/i }));
    await userEvent.type(screen.getByPlaceholderText("New collection name…"), "Process SOPs");
    await userEvent.click(screen.getByRole("button", { name: "Create" }));

    // the new collection appears as a card in the grid
    const card = await screen.findByRole("button", { name: "Open Process SOPs" });
    // opening it switches to the collection page; Upload is a dropdown menu
    await userEvent.click(card);
    await userEvent.click(screen.getByRole("button", { name: "Upload" }));
    expect(screen.getByRole("menuitem", { name: "Upload files" })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: "Upload folder" })).toBeInTheDocument();
  });

  it("uploads a document and lists it; clicking opens it", async () => {
    const onOpenDoc = vi.fn();
    const c = await mockKbApi.createCollection("kb");
    render(<KbCollectionsPage client={mockKbApi} onOpenDoc={onOpenDoc} />);

    await userEvent.click(await screen.findByRole("button", { name: "Open kb" }));

    const file = new File(["# guide"], "guide.md", { type: "text/markdown" });
    await userEvent.upload(
      screen.getByLabelText("Collection").querySelector("input[type=file]")!,
      file,
    );

    const row = await screen.findByRole("button", { name: /guide\.md/ });
    await userEvent.click(row);
    expect(onOpenDoc).toHaveBeenCalledWith(`${c.resource_id}/me/guide.md`);
  });

  it("filters documents in the open collection by name", async () => {
    const c = await mockKbApi.createCollection("kb");
    await mockKbApi.uploadDocument(c.resource_id, new File(["x"], "reflow.md"));
    await mockKbApi.uploadDocument(c.resource_id, new File(["x"], "wirebond.md"));
    render(<KbCollectionsPage client={mockKbApi} />);

    await userEvent.click(await screen.findByRole("button", { name: "Open kb" }));
    await screen.findByRole("button", { name: /reflow\.md/ });
    await userEvent.type(screen.getByPlaceholderText("Search in this collection…"), "wire");
    expect(screen.queryByRole("button", { name: /reflow\.md/ })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /wirebond\.md/ })).toBeInTheDocument();
  });

  it("card shows docs/size/cited + owner; opening shows per-doc chunks + cited", async () => {
    const client = {
      listCollections: async () => [
        col({ resource_id: "c1", name: "Reflow SOPs", cited: 7, doc_count: 3, size: 2048 }),
      ],
      listDocuments: async () => [
        {
          resource_id: "c1/me/a.md",
          path: "a.md",
          content_type: "text/markdown",
          created_by: "alice",
          status: "ready",
          chunks: 12,
          cited: 4,
          size: 2048,
          updated_at: Date.UTC(2026, 4, 20),
        },
      ],
    } as unknown as Client;

    render(<KbCollectionsPage client={client} />);

    const card = await screen.findByRole("button", { name: "Open Reflow SOPs" });
    expect(card).toHaveTextContent("3 docs");
    expect(card).toHaveTextContent("2 KB");
    expect(card).toHaveTextContent("cited 7×");

    await userEvent.click(card);
    const row = (await screen.findByRole("button", { name: /a\.md/ })).closest(
      ".kb-doctable__row",
    )!;
    expect(row).toHaveTextContent("12"); // chunks column
    expect(row).toHaveTextContent("4"); // cited column
    expect(row).toHaveTextContent("2 KB"); // size column
  });

  it("pins a collection (persisted), floating it to the top", async () => {
    const client = {
      listCollections: async () => [
        col({ resource_id: "c1", name: "Alpha" }),
        col({ resource_id: "c2", name: "Zeta" }),
      ],
      listDocuments: async () => [],
    } as unknown as Client;
    render(<KbCollectionsPage client={client} />);

    await screen.findByRole("button", { name: "Open Alpha" });
    // alphabetical: Alpha before Zeta
    let cards = screen.getAllByRole("button", { name: /^Open / });
    expect(cards.map((c) => c.getAttribute("aria-label"))).toEqual(["Open Alpha", "Open Zeta"]);

    // pin Zeta → it floats to the top
    await userEvent.click(screen.getByRole("button", { name: "Pin Zeta" }));
    cards = screen.getAllByRole("button", { name: /^Open / });
    expect(cards.map((c) => c.getAttribute("aria-label"))).toEqual(["Open Zeta", "Open Alpha"]);
  });

  it("summarizes the library in the header (count line + most-cited metric)", async () => {
    const client = {
      listCollections: async () => [
        col({ resource_id: "c1", name: "Reflow SOPs", cited: 3, doc_count: 4 }),
        col({ resource_id: "c2", name: "Wirebond SOPs", cited: 9, doc_count: 6 }),
      ],
      listDocuments: async () => [],
    } as unknown as Client;

    render(<KbCollectionsPage client={client} />);

    await screen.findByRole("button", { name: "Open Wirebond SOPs" });
    // header count line: "2 collections · 10 documents"
    const title = document.querySelector(".kb-libhead__title")!;
    expect(title).toHaveTextContent("2 collections");
    expect(title).toHaveTextContent("10 documents");
    // most-cited metric names the top collection
    const citedMetric = screen.getByText(/^Most cited$/i).closest(".kb-metric")!;
    expect(citedMetric).toHaveTextContent("Wirebond SOPs");
  });

  it("filters the grid by the All / Mine / Pinned tabs", async () => {
    // useCurrentUser() resolves to the mock's "default-user" → that's "Mine".
    const client = {
      listCollections: async () => [
        col({ resource_id: "c1", name: "Mine SOPs", owner: "default-user" }),
        col({ resource_id: "c2", name: "Theirs SOPs", owner: "alice" }),
      ],
      listDocuments: async () => [],
    } as unknown as Client;
    render(<KbCollectionsPage client={client} />);

    await screen.findByRole("button", { name: "Open Mine SOPs" });
    expect(screen.getByRole("button", { name: "Open Theirs SOPs" })).toBeInTheDocument();

    // "Mine" tab keeps only collections owned by the current user
    await userEvent.click(screen.getByRole("button", { name: /^Mine/ }));
    expect(screen.getByRole("button", { name: "Open Mine SOPs" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Open Theirs SOPs" })).not.toBeInTheDocument();
  });

  it("open page shows a stats banner (docs/size/chunks/cited/owner/updated)", async () => {
    const client = {
      listCollections: async () => [
        col({
          resource_id: "c1",
          name: "Reflow SOPs",
          cited: 5,
          doc_count: 2,
          size: 3072,
          owner: "alice",
          updated_at: Date.UTC(2026, 4, 20),
        }),
      ],
      listDocuments: async () => [
        { resource_id: "c1/me/a.md", path: "a.md", content_type: "text/markdown", created_by: "me", status: "ready", chunks: 8 },
        { resource_id: "c1/me/b.md", path: "b.md", content_type: "text/markdown", created_by: "me", status: "ready", chunks: 4 },
      ],
    } as unknown as Client;
    render(<KbCollectionsPage client={client} />);

    await userEvent.click(await screen.findByRole("button", { name: "Open Reflow SOPs" }));
    const banner = (await screen.findByText("Documents")).closest(".kb-colpage__stats")!;
    expect(banner.querySelector(".kb-stat")).toHaveTextContent("2"); // doc_count
    expect(banner).toHaveTextContent("12"); // chunks total = 8 + 4
    expect(banner).toHaveTextContent("5×"); // cited
    expect(banner).toHaveTextContent("alice"); // owner
  });

  it("picks an icon → updates the collection via native CRUD", async () => {
    const updateCollection = vi.fn(async () => {});
    const client = {
      listCollections: async () => [col({ resource_id: "c1", name: "kb", icon: "layers" })],
      listDocuments: async () => [],
      updateCollection,
    } as unknown as Client;
    render(<KbCollectionsPage client={client} />);

    await userEvent.click(await screen.findByRole("button", { name: "Open kb" }));
    await userEvent.click(screen.getByRole("button", { name: "Change icon" }));
    await userEvent.click(screen.getByRole("button", { name: "Icon flame" }));
    expect(updateCollection).toHaveBeenCalledWith("c1", { icon: "flame" });
  });

  it("renames the collection via the inline title editor", async () => {
    const updateCollection = vi.fn(async () => {});
    const client = {
      listCollections: async () => [col({ resource_id: "c1", name: "kb" })],
      listDocuments: async () => [],
      updateCollection,
    } as unknown as Client;
    render(<KbCollectionsPage client={client} />);

    await userEvent.click(await screen.findByRole("button", { name: "Open kb" }));
    await userEvent.click(screen.getByRole("heading", { name: "kb" }));
    const input = screen.getByDisplayValue("kb");
    await userEvent.clear(input);
    await userEvent.type(input, "Wirebond{Enter}");
    expect(updateCollection).toHaveBeenCalledWith("c1", { name: "Wirebond" });
  });

  it("deletes the collection from the settings menu after confirmation", async () => {
    const deleteCollection = vi.fn(async () => {});
    const client = {
      listCollections: async () => [col({ resource_id: "c1", name: "kb" })],
      listDocuments: async () => [],
      deleteCollection,
    } as unknown as Client;
    render(<KbCollectionsPage client={client} />);

    await userEvent.click(await screen.findByRole("button", { name: "Open kb" }));
    // delete lives inside the settings menu, not exposed as a bare button
    await userEvent.click(screen.getByRole("button", { name: "Collection settings" }));
    await userEvent.click(screen.getByRole("menuitem", { name: "Delete collection" }));
    // confirm step — the destructive "Delete" button
    await userEvent.click(screen.getByRole("button", { name: "Delete" }));
    expect(deleteCollection).toHaveBeenCalledWith("c1");
    // returns to the grid landing
    expect(await screen.findByRole("button", { name: /new collection/i })).toBeInTheDocument();
  });

  it("creates a collection with a description through the modal", async () => {
    const createCollection = vi.fn(async (name: string, description?: string) =>
      col({ resource_id: "c9", name, description }),
    );
    const client = {
      listCollections: async () => [],
      listDocuments: async () => [],
      createCollection,
    } as unknown as Client;
    render(<KbCollectionsPage client={client} />);

    await userEvent.click(await screen.findByRole("button", { name: /new collection/i }));
    await userEvent.type(screen.getByPlaceholderText("New collection name…"), "Reflow SOPs");
    await userEvent.type(screen.getByPlaceholderText(/what lives in this collection/i), "zone notes");
    await userEvent.click(screen.getByRole("button", { name: "Create" }));
    expect(createCollection).toHaveBeenCalledWith("Reflow SOPs", "zone notes");
  });

  it("re-indexes all documents from the settings menu", async () => {
    const reindexCollection = vi.fn(async () => {});
    const client = {
      listCollections: async () => [col({ resource_id: "c1", name: "kb" })],
      listDocuments: async () => [
        { resource_id: "c1/me/a.md", path: "a.md", content_type: "text/markdown", created_by: "me", status: "error", chunks: 0 },
      ],
      reindexCollection,
    } as unknown as Client;
    render(<KbCollectionsPage client={client} />);

    await userEvent.click(await screen.findByRole("button", { name: "Open kb" }));
    await userEvent.click(screen.getByRole("button", { name: "Collection settings" }));
    await userEvent.click(screen.getByRole("menuitem", { name: "Re-index all" }));
    expect(reindexCollection).toHaveBeenCalledWith("c1");
  });

  it("shows an indexing chip that clears once the doc is indexed (polling)", async () => {
    let status = "indexing";
    const client = {
      listCollections: async () => [col({ resource_id: "c1", name: "kb" })],
      listDocuments: async () => {
        const s = status;
        status = "ready"; // next poll returns ready
        return [
          { resource_id: "c1/me/a.md", path: "a.md", content_type: "text/markdown", created_by: "me", status: s },
        ];
      },
    } as unknown as Client;

    render(<KbCollectionsPage client={client} />);
    await userEvent.click(await screen.findByRole("button", { name: "Open kb" }));
    expect(await screen.findByText("indexing…")).toBeInTheDocument();
    // a ready doc shows no status badge (matches the design's clean table)
    await waitFor(() => expect(screen.queryByText("indexing…")).not.toBeInTheDocument(), {
      timeout: 3000,
    });
  });
});
