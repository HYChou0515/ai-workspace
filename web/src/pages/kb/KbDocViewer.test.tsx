// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render as rtlRender, screen, waitFor } from "@testing-library/react";

import { QueryWrap } from "../../test/queryWrapper";

// KB views read through TanStack Query — wrap every render with a client.
const render = (ui: Parameters<typeof rtlRender>[0]) =>
  rtlRender(ui, { wrapper: QueryWrap });
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { KbApi, KbRenderedDoc } from "../../api/kb";
import { KbDocViewer } from "./KbDocViewer";

/** A rendered doc with all the metadata fields the drawer header needs. */
function mkDoc(over: Partial<KbRenderedDoc> & Pick<KbRenderedDoc, "filename" | "markdown">): KbRenderedDoc {
  return {
    document_id: "col-1/u/doc.md",
    collection_id: "col-1",
    file_id: "blob-1",
    content_type: "text/markdown",
    size: 2048,
    chunks: 6,
    cited: 0,
    created_by: "u",
    updated_at: Date.UTC(2026, 4, 20),
    status: "ready",
    ...over,
  };
}

function fakeClient(docs: Record<string, KbRenderedDoc>, over: Partial<KbApi> = {}): KbApi {
  return {
    listCollections: async () => [],
    renderDocument: async (id: string) => {
      const d = docs[id];
      if (!d) throw new Error(`not found: ${id}`);
      return d;
    },
    ...over,
  } as unknown as KbApi;
}

describe("KbDocViewer", () => {
  afterEach(cleanup);

  it("renders the document body and the cited passage callout", async () => {
    const client = fakeClient({
      "col-1/u/reflow.md": mkDoc({
        filename: "reflow.md",
        markdown: "# Reflow\n\nZone three drifted under load.",
      }),
    });
    const { container } = render(
      <KbDocViewer
        documentId="col-1/u/reflow.md"
        snippet="Zone three drifted under load"
        onClose={() => {}}
        client={client}
      />,
    );
    await waitFor(() => expect(screen.getByText("Cited passage")).toBeInTheDocument());
    // the cited passage is highlighted in place within the rendered body
    await waitFor(() => {
      const mark = container.querySelector("mark.kb-hl");
      expect(mark?.textContent).toBe("Zone three drifted under load");
    });
  });

  it("follows a kb:// link to the target document in-place", async () => {
    const client = fakeClient({
      "col-1/u/a.md": mkDoc({
        filename: "a.md",
        markdown: "See [the other doc](kb://doc/col-1/u/b.md).",
      }),
      "col-1/u/b.md": mkDoc({ filename: "b.md", markdown: "# B\n\nThe linked document." }),
    });
    render(<KbDocViewer documentId="col-1/u/a.md" onClose={() => {}} client={client} />);

    const link = await screen.findByRole("button", { name: "the other doc" });
    await userEvent.click(link);

    await waitFor(() => expect(screen.getByText(/The linked document/)).toBeInTheDocument());
  });

  it("shows the metadata strip and a download link to the blob", async () => {
    const client = fakeClient({
      "col-1/u/reflow.md": mkDoc({
        filename: "reflow.md",
        markdown: "# Reflow",
        size: 2048,
        chunks: 6,
        cited: 3,
        file_id: "blob-xyz",
      }),
    });
    render(<KbDocViewer documentId="col-1/u/reflow.md" onClose={() => {}} client={client} />);

    const meta = await screen.findByText(/cited 3×/);
    const strip = meta.closest(".kb-docviewer__meta")!;
    expect(strip).toHaveTextContent("2 KB");
    expect(strip).toHaveTextContent("6 chunks");
    const download = screen.getByRole("link", { name: /Download/ });
    expect(download).toHaveAttribute("href", expect.stringContaining("/blobs/blob-xyz"));
  });

  it("re-indexes the document via the action bar", async () => {
    const reindexDocument = vi.fn(async () => {});
    const client = fakeClient(
      { "col-1/u/a.md": mkDoc({ filename: "a.md", markdown: "# A" }) },
      { reindexDocument },
    );
    render(<KbDocViewer documentId="col-1/u/a.md" onClose={() => {}} client={client} />);
    await screen.findByText("a.md");
    await userEvent.click(screen.getByRole("button", { name: "Re-index" }));
    expect(reindexDocument).toHaveBeenCalledWith("col-1/u/a.md");
  });

  it("removes the document after confirmation, then closes", async () => {
    const deleteDocument = vi.fn(async () => {});
    const onClose = vi.fn();
    const client = fakeClient(
      { "col-1/u/a.md": mkDoc({ filename: "a.md", markdown: "# A" }) },
      { deleteDocument },
    );
    render(<KbDocViewer documentId="col-1/u/a.md" onClose={onClose} client={client} />);
    await screen.findByText("a.md");
    await userEvent.click(screen.getByRole("button", { name: "Remove document" }));
    await userEvent.click(screen.getByRole("button", { name: "Remove" }));
    await waitFor(() => expect(deleteDocument).toHaveBeenCalledWith("col-1/u/a.md"));
    expect(onClose).toHaveBeenCalled();
  });
});
