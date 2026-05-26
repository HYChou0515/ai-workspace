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
    await userEvent.type(screen.getByPlaceholderText("New collection name…"), "Process SOPs");
    await userEvent.click(screen.getByRole("button", { name: /new collection/i }));

    // the new collection appears as a card in the grid
    const card = await screen.findByRole("button", { name: "Open Process SOPs" });
    // opening it switches to the documents view (upload affordances appear)
    await userEvent.click(card);
    expect(screen.getByRole("button", { name: "Upload" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Upload folder" })).toBeInTheDocument();
  });

  it("uploads a document and lists it; clicking opens it", async () => {
    const onOpenDoc = vi.fn();
    const c = await mockKbApi.createCollection("kb");
    render(<KbCollectionsPage client={mockKbApi} onOpenDoc={onOpenDoc} />);

    await userEvent.click(await screen.findByRole("button", { name: "Open kb" }));

    const file = new File(["# guide"], "guide.md", { type: "text/markdown" });
    await userEvent.upload(
      screen.getByLabelText("Documents").querySelector("input[type=file]")!,
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
    await userEvent.type(screen.getByPlaceholderText("Filter documents by name…"), "wire");
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
    const row = (await screen.findByRole("button", { name: /a\.md/ })).closest("li")!;
    expect(row).toHaveTextContent("12 chunks");
    expect(row).toHaveTextContent("4 cited");
    expect(row).toHaveTextContent("2 KB");
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

  it("summarizes the library with a KPI strip (count + most-cited)", async () => {
    const client = {
      listCollections: async () => [
        col({ resource_id: "c1", name: "Reflow SOPs", cited: 3 }),
        col({ resource_id: "c2", name: "Wirebond SOPs", cited: 9 }),
      ],
      listDocuments: async () => [],
    } as unknown as Client;

    render(<KbCollectionsPage client={client} />);

    await screen.findByRole("button", { name: "Open Wirebond SOPs" });
    const collectionsKpi = screen.getByText(/^Collections$/i).closest(".kb-kpi")!;
    expect(collectionsKpi).toHaveTextContent("2");
    const citedKpi = screen.getByText(/^Most cited$/i).closest(".kb-kpi")!;
    expect(citedKpi).toHaveTextContent("Wirebond SOPs");
  });

  it("shows an indexing chip that flips to indexed by polling", async () => {
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
    await waitFor(() => expect(screen.getByText("indexed")).toBeInTheDocument(), { timeout: 3000 });
  });
});
