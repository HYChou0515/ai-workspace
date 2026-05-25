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
