// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render as rtlRender, screen, waitFor } from "@testing-library/react";

import { QueryWrap } from "../../test/queryWrapper";

// KB views read through TanStack Query — wrap every render with a client.
const render = (ui: Parameters<typeof rtlRender>[0]) =>
  rtlRender(ui, { wrapper: QueryWrap });
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Navigate, Outlet, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { _resetKbMock, mockKbApi } from "../../api/kbMock";
import type { KbDocumentsPage } from "../../api/kb";
import { uploadDocPath } from "./collectionFormat";
import { CardsTab, DocumentsTab, KbCollectionPage, WikiTab } from "./KbCollectionPage";
import { KbCollectionsGrid } from "./KbCollectionsGrid";
import type { KbOutletCtx } from "./KbHome";

type Client = Parameters<typeof KbCollectionsGrid>[0]["client"];

/** Mount the collections grid + open-collection page + its tab routes under a
 * minimal shell that supplies the Outlet context the page reads (the real
 * shell — KbHome — is exercised in KbHome/kbRoutes tests). Opening a card
 * navigates to /kb/collections/:cid/documents (#93). */
function renderKb(client: Client, start = "/kb/collections") {
  return render(
    <MemoryRouter initialEntries={[start]}>
      <Routes>
        <Route
          element={<Outlet context={{ openDoc: () => {}, openCite: () => {} } satisfies KbOutletCtx} />}
        >
          <Route path="/kb/collections" element={<KbCollectionsGrid client={client} />} />
          <Route path="/kb/collections/:cid" element={<KbCollectionPage client={client} />}>
            <Route index element={<Navigate to="documents" replace />} />
            <Route path="documents" element={<DocumentsTab />} />
            <Route path="cards" element={<CardsTab />} />
            <Route path="wiki" element={<WikiTab />} />
          </Route>
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

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

function makeDeferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((r) => (resolve = r));
  return { promise, resolve };
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

  it("shows a loading placeholder while collections are still fetching — not the empty copy", () => {
    const client = { listCollections: () => new Promise(() => {}) } as unknown as Client;
    renderKb(client);
    expect(screen.getByTestId("kb-cols-loading")).toBeInTheDocument();
    expect(screen.queryByText(/No collections yet/)).not.toBeInTheDocument();
  });

  it("shows the empty copy only once loading resolves with no collections", async () => {
    const client = { listCollections: async () => [] } as unknown as Client;
    renderKb(client);
    expect(await screen.findByText(/No collections yet/)).toBeInTheDocument();
    expect(screen.queryByTestId("kb-cols-loading")).not.toBeInTheDocument();
  });

  it("creates a collection card; opening it shows the upload affordances", async () => {
    renderKb(mockKbApi);
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
    renderKb(mockKbApi);

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

    renderKb(client);

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
    renderKb(client);

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

    renderKb(client);

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
    renderKb(client);

    await screen.findByRole("button", { name: "Open Mine SOPs" });
    expect(screen.getByRole("button", { name: "Open Theirs SOPs" })).toBeInTheDocument();

    // "Mine" tab keeps only collections owned by the current user
    await userEvent.click(screen.getByRole("button", { name: /^Mine/ }));
    expect(screen.getByRole("button", { name: "Open Mine SOPs" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Open Theirs SOPs" })).not.toBeInTheDocument();
  });

  it("reads the grid filter from the URL (?view=mine deep-link)", async () => {
    // useCurrentUser() resolves to the mock's "default-user" → that's "Mine".
    const client = {
      listCollections: async () => [
        col({ resource_id: "c1", name: "Mine SOPs", owner: "default-user" }),
        col({ resource_id: "c2", name: "Theirs SOPs", owner: "alice" }),
      ],
      listDocuments: async () => page([]),
    } as unknown as Client;
    renderKb(client, "/kb/collections?view=mine");

    // the Mine filter is applied straight from the URL, no click needed
    expect(await screen.findByRole("button", { name: "Open Mine SOPs" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Open Theirs SOPs" })).not.toBeInTheDocument();
  });

  it("reads the owner + name-query filters from the URL", async () => {
    const client = {
      listCollections: async () => [
        col({ resource_id: "c1", name: "Reflow SOPs", owner: "alice" }),
        col({ resource_id: "c2", name: "Wirebond SOPs", owner: "bob" }),
      ],
      listDocuments: async () => page([]),
    } as unknown as Client;
    renderKb(client, "/kb/collections?owner=alice&q=reflow");

    expect(await screen.findByRole("button", { name: "Open Reflow SOPs" })).toBeInTheDocument();
    // owner=alice excludes bob's Wirebond; q=reflow would exclude it too
    expect(screen.queryByRole("button", { name: "Open Wirebond SOPs" })).not.toBeInTheDocument();
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
    renderKb(client);

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
    renderKb(client);

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
    renderKb(client);

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
    renderKb(client);

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

  it("disables the confirm Delete button while the collection delete is in flight (no double-submit)", async () => {
    const d = makeDeferred<void>();
    const client = {
      listCollections: async () => [col({ resource_id: "c1", name: "kb" })],
      listDocuments: async () => page([]),
      deleteCollection: () => d.promise,
    } as unknown as Client;
    renderKb(client);

    await userEvent.click(await screen.findByRole("button", { name: "Open kb" }));
    await userEvent.click(screen.getByRole("button", { name: "Collection settings" }));
    await userEvent.click(screen.getByRole("menuitem", { name: "Delete collection" }));
    const del = screen.getByRole("button", { name: "Delete" });
    await userEvent.click(del);
    await waitFor(() => expect(del).toBeDisabled());
    d.resolve(); // settle the in-flight mutation so nothing dangles
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
    renderKb(client);

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
    renderKb(client);

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
    renderKb(client);
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
    renderKb(client);
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

    renderKb(client);
    await userEvent.click(await screen.findByRole("button", { name: "Open kb" }));
    // KbDocIde badges the indexing doc in the tree, then the 1.5s poll clears it.
    expect(await screen.findByTitle("處理中…")).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByTitle("處理中…")).not.toBeInTheDocument(), {
      timeout: 3000,
    });
  });

  it("upload picker has no `accept` filter (macOS greys out valid files otherwise)", async () => {
    const client = {
      listCollections: async () => [col({ resource_id: "c1", name: "kb" })],
      listDocuments: async () => page([]),
    } as unknown as Client;
    const { container } = renderKb(client);
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
    const { container } = renderKb(client);
    await userEvent.click(await screen.findByRole("button", { name: "Open kb" }));

    const folderInput = container.querySelector("input[webkitdirectory]") as HTMLInputElement;
    const img = new File([new Uint8Array([0x89, 0x50])], "cat.png", { type: "image/png" });
    await userEvent.upload(folderInput, img);

    await waitFor(() =>
      expect(uploadDocument).toHaveBeenCalledWith("c1", img, "cat.png"),
    );
  });

  it("describes a collection in plain language on the landing header (#173)", async () => {
    const client = {
      listCollections: async () => [col({ resource_id: "c1", name: "kb" })],
      listDocuments: async () => page([]),
    } as unknown as Client;
    renderKb(client);

    expect(
      await screen.findByText(/每個集合是一組文件，AI 回答時可參考/),
    ).toBeInTheDocument();
    // the old "unit of search" jargon is gone
    expect(screen.queryByText(/unit of search/i)).not.toBeInTheDocument();
  });

  it("shows an in-place orientation strip with every tab's blurb expanded by default (#173)", async () => {
    localStorage.removeItem("kb:col-overview-collapsed");
    const client = {
      listCollections: async () => [col({ resource_id: "c1", name: "kb" })],
      listDocuments: async () => page([]),
    } as unknown as Client;
    renderKb(client);

    await userEvent.click(await screen.findByRole("button", { name: "Open kb" }));
    // All tab blurbs are visible at once — a first-timer never has to click
    // each tab to learn what it is.
    expect(screen.getByText(/你上傳的檔案。AI 搜尋會讀這些來回答/)).toBeInTheDocument();
    expect(
      screen.getByText(/你親手寫的詞彙表——AI 遇到這些詞會照你的定義使用/),
    ).toBeInTheDocument();
    // No wiki on this collection → no wiki blurb.
    expect(screen.queryByText(/AI 自動整理、互相連結的全集摘要/)).not.toBeInTheDocument();
  });

  it("lists the Wiki blurb in the orientation strip only when the collection has a wiki (#173)", async () => {
    localStorage.removeItem("kb:col-overview-collapsed");
    const client = {
      ...mockKbApi,
      listCollections: async () => [col({ resource_id: "c1", name: "kb", use_wiki: true })],
      listDocuments: async () => page([]),
    } as unknown as Client;
    renderKb(client);

    await userEvent.click(await screen.findByRole("button", { name: "Open kb" }));
    expect(screen.getByText(/AI 自動整理、互相連結的全集摘要/)).toBeInTheDocument();
  });

  it("collapses the orientation strip and remembers it (#173)", async () => {
    localStorage.removeItem("kb:col-overview-collapsed");
    const client = {
      listCollections: async () => [col({ resource_id: "c1", name: "kb" })],
      listDocuments: async () => page([]),
    } as unknown as Client;
    renderKb(client);

    await userEvent.click(await screen.findByRole("button", { name: "Open kb" }));
    // Collapse via the strip's header toggle.
    await userEvent.click(screen.getByRole("button", { name: /這個集合裡有什麼/ }));
    // Blurbs hidden; only the re-expand affordance remains.
    expect(screen.queryByText(/你上傳的檔案。AI 搜尋會讀這些來回答/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /這些分頁是什麼/ })).toBeInTheDocument();
    // …and the choice is persisted so it stays collapsed next visit.
    expect(localStorage.getItem("kb:col-overview-collapsed")).toBe("true");
  });

  it("shows a collection-level index-status strip while a doc is indexing (#162)", async () => {
    const client = {
      listCollections: async () => [col({ resource_id: "c1", name: "kb" })],
      listDocuments: async () =>
        page([
          { resource_id: "c1/me/a.md", path: "a.md", content_type: "text/markdown", created_by: "me", status: "indexing" },
        ]),
    } as unknown as Client;
    renderKb(client);

    await userEvent.click(await screen.findByRole("button", { name: "Open kb" }));
    const strip = await screen.findByTestId("kb-index-status");
    // #171: de-jargoned — "處理中" not "Indexing".
    expect(strip).toHaveTextContent(/處理 1 份中/);
    expect(strip).not.toHaveTextContent(/Indexing/i);
  });

  it("reports a failed doc in the index-status strip (#162)", async () => {
    const client = {
      listCollections: async () => [col({ resource_id: "c1", name: "kb" })],
      listDocuments: async () =>
        page([
          { resource_id: "c1/me/a.md", path: "a.md", content_type: "text/markdown", created_by: "me", status: "error" },
        ]),
    } as unknown as Client;
    renderKb(client);

    await userEvent.click(await screen.findByRole("button", { name: "Open kb" }));
    const strip = await screen.findByTestId("kb-index-status");
    expect(strip).toHaveTextContent(/1 份處理失敗/);
  });

  it("hides the index-status strip once every doc is ready (#162)", async () => {
    const client = {
      listCollections: async () => [col({ resource_id: "c1", name: "kb" })],
      listDocuments: async () =>
        page([
          { resource_id: "c1/me/a.md", path: "a.md", content_type: "text/markdown", created_by: "me", status: "ready" },
        ]),
    } as unknown as Client;
    renderKb(client);

    await userEvent.click(await screen.findByRole("button", { name: "Open kb" }));
    // the doc tree renders the ready doc; the strip never appears
    await screen.findByRole("button", { name: /a\.md/ });
    expect(screen.queryByTestId("kb-index-status")).not.toBeInTheDocument();
  });

  it("shows an uploading state in the index-status strip while files upload (#162)", async () => {
    const d = makeDeferred<string[]>();
    const client = {
      listCollections: async () => [col({ resource_id: "c1", name: "kb" })],
      listDocuments: async () => page([]),
      uploadDocument: () => d.promise,
    } as unknown as Client;
    const { container } = renderKb(client);

    await userEvent.click(await screen.findByRole("button", { name: "Open kb" }));
    const file = new File(["x"], "x.md", { type: "text/markdown" });
    await userEvent.upload(
      container.querySelector('input[type="file"]:not([webkitdirectory])') as HTMLInputElement,
      file,
    );
    const strip = await screen.findByTestId("kb-index-status");
    expect(strip).toHaveTextContent(/上傳中/);
    d.resolve(["c1/me/x.md"]); // settle the in-flight upload
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
    renderKb(client);

    await userEvent.click(await screen.findByRole("button", { name: "Open SOPs" }));
    await userEvent.click(await screen.findByRole("button", { name: "Collection settings" }));
    // #171: "Retrieval modes" → de-jargoned "答案如何查詢" on the menu + panel header.
    await userEvent.click(await screen.findByRole("menuitem", { name: /答案如何查詢/ }));
    expect(screen.getByText("答案如何查詢")).toBeInTheDocument();
    await userEvent.click(await screen.findByRole("switch", { name: "知識百科" }));

    expect(updateCollection).toHaveBeenCalledWith("c1", { use_rag: true, use_wiki: true });
  });

  it("downloads a collection: prepares the export then triggers the stream", async () => {
    await mockKbApi.createCollection("Reports");
    const prepSpy = vi.spyOn(mockKbApi, "prepareCollectionDownload");
    const clickSpy = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(() => {});
    renderKb(mockKbApi);

    await userEvent.click(await screen.findByRole("button", { name: "Open Reports" }));
    await userEvent.click(await screen.findByRole("button", { name: "Collection settings" }));
    await userEvent.click(await screen.findByRole("menuitem", { name: /download collection/i }));

    const colId = (await mockKbApi.listCollections())[0]!.resource_id;
    await waitFor(() => expect(prepSpy).toHaveBeenCalledWith(colId));
    // the prepared zip is fetched via a native anchor download (streamed to disk)
    await waitFor(() => expect(clickSpy).toHaveBeenCalled());

    clickSpy.mockRestore();
    prepSpy.mockRestore();
  });

  it("imports a zip as a new collection from the landing page and opens it", async () => {
    renderKb(mockKbApi);

    const input = screen.getByLabelText("Import collection from file") as HTMLInputElement;
    const file = new File(["zipbytes"], "Archive.zip", { type: "application/zip" });
    await userEvent.upload(input, file);

    // the imported collection opens to its page (the settings button only exists there)
    expect(await screen.findByRole("button", { name: "Collection settings" })).toBeInTheDocument();
    // and it is named after the uploaded file (manifest-less fallback)
    const names = (await mockKbApi.listCollections()).map((c) => c.name);
    expect(names).toContain("Archive");
  });

  it("imports a zip into the open collection after choosing overwrite", async () => {
    await mockKbApi.createCollection("kb");
    const intoSpy = vi.spyOn(mockKbApi, "importCollectionInto");
    renderKb(mockKbApi);

    await userEvent.click(await screen.findByRole("button", { name: "Open kb" }));
    const input = screen.getByLabelText("Import into this collection") as HTMLInputElement;
    const file = new File(["zip"], "kb.zip", { type: "application/zip" });
    await userEvent.upload(input, file);

    // a confirm dialog appears so the user picks how path collisions resolve
    await userEvent.click(await screen.findByRole("button", { name: /overwrite/i }));

    const colId = (await mockKbApi.listCollections()).find((c) => c.name === "kb")!.resource_id;
    await waitFor(() => expect(intoSpy).toHaveBeenCalledWith(colId, file, "overwrite"));
    intoSpy.mockRestore();
  });

  it("imports into the open collection with skip mode when chosen", async () => {
    await mockKbApi.createCollection("kb");
    const intoSpy = vi.spyOn(mockKbApi, "importCollectionInto");
    renderKb(mockKbApi);

    await userEvent.click(await screen.findByRole("button", { name: "Open kb" }));
    const input = screen.getByLabelText("Import into this collection") as HTMLInputElement;
    const file = new File(["zip"], "kb.zip", { type: "application/zip" });
    await userEvent.upload(input, file);

    await userEvent.click(await screen.findByRole("button", { name: /skip existing/i }));

    const colId = (await mockKbApi.listCollections()).find((c) => c.name === "kb")!.resource_id;
    await waitFor(() => expect(intoSpy).toHaveBeenCalledWith(colId, file, "skip"));
    intoSpy.mockRestore();
  });
});
