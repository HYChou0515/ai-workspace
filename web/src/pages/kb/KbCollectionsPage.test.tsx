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
import type { KbDocumentsPage } from "../../api/kb";
import { KbCollectionsPage, uploadDocPath } from "./KbCollectionsPage";

type Client = Parameters<typeof KbCollectionsPage>[0]["client"];

/** Wrap a list of documents in the BE's `DocumentsPage` envelope so each
 * inline test client can keep returning a bare array literal. */
function page<T>(items: T[]) {
  return { items, total: items.length, offset: 0, limit: 50, has_more: false };
}

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

describe("uploadDocPath", () => {
  it("preserves a folder pick's relative path", () => {
    expect(uploadDocPath({ name: "a.png", webkitRelativePath: "trip/a.png" }, true)).toBe("trip/a.png");
  });
  it("falls back to the name for a single file in the folder picker (empty path)", () => {
    expect(uploadDocPath({ name: "a.png", webkitRelativePath: "" }, true)).toBe("a.png");
  });
  it("uses the name for a plain (non-folder) upload", () => {
    expect(uploadDocPath({ name: "a.png", webkitRelativePath: "ignored/a.png" }, false)).toBe("a.png");
  });
});

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

  it("uploads a document and the doc tree lists it (#87)", async () => {
    await mockKbApi.createCollection("kb");
    render(<KbCollectionsPage client={mockKbApi} />);

    await userEvent.click(await screen.findByRole("button", { name: "Open kb" }));

    const file = new File(["# guide"], "guide.md", { type: "text/markdown" });
    await userEvent.upload(
      screen.getByLabelText("Collection").querySelector("input[type=file]")!,
      file,
    );

    // The shared file tree (KbDocIde) lists the uploaded doc by basename.
    expect(await screen.findByRole("button", { name: /guide\.md/ })).toBeInTheDocument();
  });

  it("card shows docs/size/cited + owner", async () => {
    const client = {
      listCollections: async () => [
        col({ resource_id: "c1", name: "Reflow SOPs", cited: 7, doc_count: 3, size: 2048 }),
      ],
      listDocuments: async () => page([]),
    } as unknown as Client;

    render(<KbCollectionsPage client={client} />);

    const card = await screen.findByRole("button", { name: "Open Reflow SOPs" });
    expect(card).toHaveTextContent("3 docs");
    expect(card).toHaveTextContent("2 KB");
    expect(card).toHaveTextContent("cited 7×");
  });

  it("pins a collection (persisted), floating it to the top", async () => {
    const client = {
      listCollections: async () => [
        col({ resource_id: "c1", name: "Alpha" }),
        col({ resource_id: "c2", name: "Zeta" }),
      ],
      listDocuments: async () => page([]),
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
      listDocuments: async () => page([]),
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
      listDocuments: async () => page([]),
    } as unknown as Client;
    render(<KbCollectionsPage client={client} />);

    await screen.findByRole("button", { name: "Open Mine SOPs" });
    expect(screen.getByRole("button", { name: "Open Theirs SOPs" })).toBeInTheDocument();

    // "Mine" tab keeps only collections owned by the current user
    await userEvent.click(screen.getByRole("button", { name: /^Mine/ }));
    expect(screen.getByRole("button", { name: "Open Mine SOPs" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Open Theirs SOPs" })).not.toBeInTheDocument();
  });

  it("open page shows a stats banner (docs/size/cited/owner/updated)", async () => {
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
      listDocuments: async () => page([]),
    } as unknown as Client;
    render(<KbCollectionsPage client={client} />);

    await userEvent.click(await screen.findByRole("button", { name: "Open Reflow SOPs" }));
    // "Documents" now appears as both a stats label and a view tab (#106); pick
    // the one inside the stats banner.
    const banner = (await screen.findAllByText("Documents"))
      .map((el) => el.closest(".kb-colpage__stats"))
      .find(Boolean)!;
    expect(banner.querySelector(".kb-stat")).toHaveTextContent("2"); // doc_count
    expect(banner).toHaveTextContent("5×"); // cited
    expect(banner).toHaveTextContent("alice"); // owner
  });

  it("picks an icon → updates the collection via native CRUD", async () => {
    const updateCollection = vi.fn(async () => {});
    const client = {
      listCollections: async () => [col({ resource_id: "c1", name: "kb", icon: "layers" })],
      listDocuments: async () => page([]),
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
      listDocuments: async () => page([]),
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
      listDocuments: async () => page([]),
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
      listDocuments: async () => page([]),
      createCollection,
    } as unknown as Client;
    render(<KbCollectionsPage client={client} />);

    await userEvent.click(await screen.findByRole("button", { name: /new collection/i }));
    await userEvent.type(screen.getByPlaceholderText("New collection name…"), "Reflow SOPs");
    await userEvent.type(screen.getByPlaceholderText(/what lives in this collection/i), "zone notes");
    await userEvent.click(screen.getByRole("button", { name: "Create" }));
    expect(createCollection).toHaveBeenCalledWith("Reflow SOPs", "zone notes", {
      useRag: true,
      useWiki: false,
    });
  });

  it("re-indexes all documents from the settings menu", async () => {
    const reindexCollection = vi.fn(async () => {});
    const client = {
      // doc_count > 0 enables the Re-index menu item (no longer derived from
      // the loaded page — the doc list lives in KbDocIde now).
      listCollections: async () => [col({ resource_id: "c1", name: "kb", doc_count: 1 })],
      listDocuments: async () =>
        page([
          { resource_id: "c1/me/a.md", path: "a.md", content_type: "text/markdown", created_by: "me", status: "error", chunks: 0 },
        ]),
      reindexCollection,
    } as unknown as Client;
    render(<KbCollectionsPage client={client} />);

    await userEvent.click(await screen.findByRole("button", { name: "Open kb" }));
    await userEvent.click(screen.getByRole("button", { name: "Collection settings" }));
    await userEvent.click(screen.getByRole("menuitem", { name: "Re-index all" }));
    expect(reindexCollection).toHaveBeenCalledWith("c1");
  });

  it("shows a Loading… placeholder while the first page is in flight", async () => {
    // Withhold the listDocuments resolution so the query stays in the
    // initial-loading state long enough to assert the placeholder.
    let release!: (page: KbDocumentsPage) => void;
    const pending = new Promise<KbDocumentsPage>((r) => {
      release = r;
    });
    const client = {
      listCollections: async () => [col({ resource_id: "c1", name: "kb" })],
      listDocuments: () => pending,
    } as unknown as Client;
    render(<KbCollectionsPage client={client} />);
    await userEvent.click(await screen.findByRole("button", { name: "Open kb" }));
    // Initial-load message replaces the empty-state copy and the table.
    expect(await screen.findByText("Loading documents…")).toBeInTheDocument();
    expect(
      screen.queryByText("Upload markdown, text, or an archive to index it."),
    ).toBeNull();
    // Once the fetch resolves, the message is replaced by the table.
    release(
      page([
        { resource_id: "c1/me/a.md", path: "a.md", content_type: "text/markdown", created_by: "me", status: "ready" },
      ]),
    );
    await screen.findByText("a.md");
    expect(screen.queryByText("Loading documents…")).toBeNull();
  });

  it("hides the pager when the collection fits on one page", async () => {
    const client = {
      listCollections: async () => [col({ resource_id: "c1", name: "kb" })],
      listDocuments: async () =>
        page([
          { resource_id: "c1/me/a.md", path: "a.md", content_type: "text/markdown", created_by: "me", status: "ready" },
        ]),
    } as unknown as Client;
    render(<KbCollectionsPage client={client} />);
    await userEvent.click(await screen.findByRole("button", { name: "Open kb" }));
    await screen.findByText("a.md");
    // No pager — total < page size and we're on page 0.
    expect(screen.queryByRole("button", { name: "Previous page" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Next page" })).toBeNull();
  });

  it("badges an indexing doc in the tree and clears it on poll", async () => {
    let status = "indexing";
    const client = {
      listCollections: async () => [col({ resource_id: "c1", name: "kb" })],
      listDocuments: async () => {
        const s = status;
        status = "ready"; // next poll returns ready
        return page([
          { resource_id: "c1/me/a.md", path: "a.md", content_type: "text/markdown", created_by: "me", status: s },
        ]);
      },
    } as unknown as Client;

    render(<KbCollectionsPage client={client} />);
    await userEvent.click(await screen.findByRole("button", { name: "Open kb" }));
    // KbDocIde badges the indexing doc in the tree, then the 1.5s poll clears it.
    expect(await screen.findByTitle("Indexing…")).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByTitle("Indexing…")).not.toBeInTheDocument(), {
      timeout: 3000,
    });
  });

  it("upload picker has no `accept` filter (macOS greys out valid files otherwise)", async () => {
    const client = {
      listCollections: async () => [col({ resource_id: "c1", name: "kb" })],
      listDocuments: async () => page([]),
    } as unknown as Client;
    const { container } = render(<KbCollectionsPage client={client} />);
    await userEvent.click(await screen.findByRole("button", { name: "Open kb" }));
    const input = container.querySelector('input[type="file"]:not([webkitdirectory])')!;
    // No extension allow-list: macOS maps extensions to UTIs and disabled valid
    // files (images included). The BE accepts + sniffs every type anyway.
    expect(input.getAttribute("accept")).toBeNull();
  });

  it("uploading a single file via the folder picker keeps its name (empty relative path)", async () => {
    // The exact repro: open "Upload folder", pick ONE image. Its
    // webkitRelativePath is "" — the old code sent that empty path straight
    // through; now it falls back to the file name.
    const uploadDocument = vi.fn(async () => ["c1/me/cat.png"]);
    const client = {
      listCollections: async () => [col({ resource_id: "c1", name: "kb" })],
      listDocuments: async () => page([]),
      uploadDocument,
    } as unknown as Client;
    const { container } = render(<KbCollectionsPage client={client} />);
    await userEvent.click(await screen.findByRole("button", { name: "Open kb" }));

    const folderInput = container.querySelector("input[webkitdirectory]") as HTMLInputElement;
    const img = new File([new Uint8Array([0x89, 0x50])], "cat.png", { type: "image/png" });
    await userEvent.upload(folderInput, img);

    await waitFor(() =>
      expect(uploadDocument).toHaveBeenCalledWith("c1", img, "cat.png"),
    );
  });

  it("edits a collection's retrieval modes from the settings menu (#50)", async () => {
    const updateCollection = vi.fn(async () => {});
    const client = {
      ...mockKbApi,
      listCollections: async () => [
        col({ resource_id: "c1", name: "SOPs", use_rag: true, use_wiki: false }),
      ],
      listDocuments: async () => page([]),
      updateCollection,
    } as unknown as Client;
    render(<KbCollectionsPage client={client} />);

    await userEvent.click(await screen.findByRole("button", { name: "Open SOPs" }));
    await userEvent.click(await screen.findByRole("button", { name: "Collection settings" }));
    await userEvent.click(await screen.findByRole("menuitem", { name: /Retrieval modes/i }));
    await userEvent.click(await screen.findByRole("switch", { name: "Knowledge wiki" }));

    expect(updateCollection).toHaveBeenCalledWith("c1", { use_rag: true, use_wiki: true });
  });
});
