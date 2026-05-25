// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render as rtlRender, screen, waitFor } from "@testing-library/react";

import { QueryWrap } from "../../test/queryWrapper";

// KB views read through TanStack Query — wrap every render with a client.
const render = (ui: Parameters<typeof rtlRender>[0]) =>
  rtlRender(ui, { wrapper: QueryWrap });
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { mockKbApi, _resetKbMock } from "../../api/kbMock";
import { KbCollectionsPage } from "./KbCollectionsPage";

describe("KbCollectionsPage", () => {
  beforeEach(() => _resetKbMock());
  afterEach(cleanup);

  it("creates a collection and selects it", async () => {
    render(<KbCollectionsPage client={mockKbApi} />);
    await userEvent.type(screen.getByPlaceholderText("New collection name…"), "Process SOPs");
    await userEvent.click(screen.getByRole("button", { name: /add/i }));

    await waitFor(() =>
      expect(screen.getByRole("button", { name: /Process SOPs/ })).toBeInTheDocument(),
    );
    // selected → its (empty) documents pane shows the upload affordances
    expect(screen.getByRole("button", { name: "Upload" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Upload folder" })).toBeInTheDocument();
  });

  it("uploads a document and lists it; clicking opens it", async () => {
    const onOpenDoc = vi.fn();
    const col = await mockKbApi.createCollection("kb");
    render(<KbCollectionsPage client={mockKbApi} onOpenDoc={onOpenDoc} />);

    await waitFor(() => expect(screen.getByRole("button", { name: /kb/ })).toBeInTheDocument());

    const file = new File(["# guide"], "guide.md", { type: "text/markdown" });
    await userEvent.upload(screen.getByLabelText("Documents").querySelector("input[type=file]")!, file);

    const row = await screen.findByRole("button", { name: /guide\.md/ });
    await userEvent.click(row);
    expect(onOpenDoc).toHaveBeenCalledWith(`${col.resource_id}/me/guide.md`);
  });

  it("filters documents in the collection by name", async () => {
    const c = await mockKbApi.createCollection("kb");
    await mockKbApi.uploadDocument(c.resource_id, new File(["x"], "reflow.md"));
    await mockKbApi.uploadDocument(c.resource_id, new File(["x"], "wirebond.md"));
    render(<KbCollectionsPage client={mockKbApi} />);

    await screen.findByRole("button", { name: /reflow\.md/ });
    await userEvent.type(screen.getByPlaceholderText("Filter documents by name…"), "wire");
    expect(screen.queryByRole("button", { name: /reflow\.md/ })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /wirebond\.md/ })).toBeInTheDocument();
  });

  it("shows each collection's cited count and per-document chunks + cited", async () => {
    const client = {
      listCollections: async () => [
        { resource_id: "c1", name: "Reflow SOPs", description: "", cited: 7 },
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
        },
      ],
    } as unknown as Parameters<typeof KbCollectionsPage>[0]["client"];

    render(<KbCollectionsPage client={client} />);

    // the collection row surfaces its cited count
    const colBtn = await screen.findByRole("button", { name: /Reflow SOPs/ });
    expect(colBtn).toHaveTextContent("7");

    // the document row surfaces its chunk count and cited count
    const row = (await screen.findByRole("button", { name: /a\.md/ })).closest("li")!;
    expect(row).toHaveTextContent("12 chunks");
    expect(row).toHaveTextContent("4 cited");
  });

  it("summarizes the library with a KPI strip (count + most-cited)", async () => {
    const client = {
      listCollections: async () => [
        { resource_id: "c1", name: "Reflow SOPs", description: "", cited: 3 },
        { resource_id: "c2", name: "Wirebond SOPs", description: "", cited: 9 },
      ],
      listDocuments: async () => [],
    } as unknown as Parameters<typeof KbCollectionsPage>[0]["client"];

    render(<KbCollectionsPage client={client} />);

    // wait for the list to load (a collection row appears) before reading KPIs
    await screen.findByRole("button", { name: /Wirebond SOPs/ });
    const collectionsKpi = screen.getByText(/^Collections$/i).closest(".kb-kpi")!;
    expect(collectionsKpi).toHaveTextContent("2");
    const citedKpi = screen.getByText(/^Most cited$/i).closest(".kb-kpi")!;
    expect(citedKpi).toHaveTextContent("Wirebond SOPs");
  });

  it("shows an indexing chip that flips to indexed by polling", async () => {
    // a client whose doc starts "indexing" then becomes "ready" on re-poll
    let status = "indexing";
    const client = {
      listCollections: async () => [{ resource_id: "c1", name: "kb", description: "" }],
      listDocuments: async () => {
        const s = status;
        status = "ready"; // next poll returns ready
        return [
          { resource_id: "c1/me/a.md", path: "a.md", content_type: "text/markdown", created_by: "me", status: s },
        ];
      },
    } as unknown as Parameters<typeof KbCollectionsPage>[0]["client"];

    render(<KbCollectionsPage client={client} />);
    expect(await screen.findByText("indexing…")).toBeInTheDocument();
    // the poll effect refetches and the chip flips
    await waitFor(() => expect(screen.getByText("indexed")).toBeInTheDocument(), { timeout: 3000 });
  });
});
