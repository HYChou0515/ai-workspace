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

  it("deletes the document after confirmation, then closes (#466: 'Delete', not 'Remove')", async () => {
    const deleteDocument = vi.fn(async () => {});
    const onClose = vi.fn();
    const client = fakeClient(
      { "col-1/u/a.md": mkDoc({ filename: "a.md", markdown: "# A" }) },
      { deleteDocument },
    );
    render(<KbDocViewer documentId="col-1/u/a.md" onClose={onClose} client={client} />);
    await screen.findByText("a.md");
    // A permanent delete (deleteDocument) reads "Delete", matching the collection /
    // chat / file delete controls — not "Remove" (which the app uses for taking an
    // item out of a set).
    await userEvent.click(screen.getByRole("button", { name: "Delete document" }));
    await userEvent.click(screen.getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(deleteDocument).toHaveBeenCalledWith("col-1/u/a.md"));
    expect(onClose).toHaveBeenCalled();
  });
});

describe("KbDocViewer binary documents (issue #39 bug)", () => {
  afterEach(cleanup);

  it("renders an image doc as an <img> from its blob, not decoded bytes", async () => {
    const client = fakeClient({
      "col-1/u/shot.png": mkDoc({
        filename: "shot.png",
        markdown: "", // BE ships no body for binary docs
        content_type: "image/png",
        file_id: "blob-png-1",
      }),
    });
    render(<KbDocViewer documentId="col-1/u/shot.png" onClose={() => {}} client={client} />);
    const img = await screen.findByRole("img", { name: "shot.png" });
    expect(img).toHaveAttribute("src", "/api/blobs/blob-png-1");
  });

  it("shows the VLM-parsed text below the image when the doc carries one (#114)", async () => {
    const client = fakeClient({
      "col-1/u/diagram.png": mkDoc({
        filename: "diagram.png",
        markdown: "# Diagram\n\nReflow oven zone three drifted.",
        content_type: "image/png",
        file_id: "blob-png-2",
      }),
    });
    render(<KbDocViewer documentId="col-1/u/diagram.png" onClose={() => {}} client={client} />);
    // the image is still rendered from the blob
    const img = await screen.findByRole("img", { name: "diagram.png" });
    expect(img).toHaveAttribute("src", "/api/blobs/blob-png-2");
    // AND the parsed text the chat actually saw is shown alongside it
    expect(await screen.findByText(/Reflow oven zone three drifted/)).toBeInTheDocument();
  });

  it("renders a download notice for undisplayable binary docs (pptx)", async () => {
    const client = fakeClient({
      "col-1/u/deck.pptx": mkDoc({
        filename: "deck.pptx",
        markdown: "",
        content_type:
          "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        file_id: "blob-pptx-1",
      }),
    });
    render(<KbDocViewer documentId="col-1/u/deck.pptx" onClose={() => {}} client={client} />);
    // User-facing copy stays jargon-free — no format/mime internals.
    expect(await screen.findByText(/preview isn't available/i)).toBeInTheDocument();
    // No mojibake article body.
    expect(screen.queryByRole("img")).toBeNull();
  });
});

describe("KbDocViewer browser-native documents (issue #39)", () => {
  afterEach(cleanup);

  it("renders a PDF doc in an iframe pointed at its blob", async () => {
    const client = fakeClient({
      "col-1/u/deck2.pdf": mkDoc({
        filename: "deck2.pdf",
        markdown: "",
        content_type: "application/pdf",
        file_id: "blob-pdf-2",
      }),
    });
    render(<KbDocViewer documentId="col-1/u/deck2.pdf" onClose={() => {}} client={client} />);
    const frame = await screen.findByTitle("deck2.pdf");
    expect(frame.tagName).toBe("IFRAME");
    expect(frame).toHaveAttribute("src", "/api/blobs/blob-pdf-2");
  });

  it("renders an HTML doc in a sandboxed iframe (no scripts)", async () => {
    const client = fakeClient({
      "col-1/u/page.html": mkDoc({
        filename: "page.html",
        markdown: "",
        content_type: "text/html",
        file_id: "blob-html-1",
      }),
    });
    render(<KbDocViewer documentId="col-1/u/page.html" onClose={() => {}} client={client} />);
    const frame = await screen.findByTitle("page.html");
    expect(frame.tagName).toBe("IFRAME");
    expect(frame).toHaveAttribute("src", "/api/blobs/blob-html-1");
    // sandbox with NO allow-scripts — uploaded HTML must not run JS.
    expect(frame).toHaveAttribute("sandbox", "");
  });
});

describe("KbDocViewer parser previews (pptx → converted PDF)", () => {
  afterEach(cleanup);

  it("iframes the preview blob when the doc carries one", async () => {
    const client = fakeClient({
      "col-1/u/deck.pptx": mkDoc({
        filename: "deck.pptx",
        markdown: "",
        content_type:
          "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        file_id: "blob-pptx-1",
        preview_file_id: "blob-preview-pdf-1",
      }),
    });
    render(<KbDocViewer documentId="col-1/u/deck.pptx" onClose={() => {}} client={client} />);
    const frame = await screen.findByTitle("deck.pptx");
    expect(frame.tagName).toBe("IFRAME");
    // The PREVIEW blob (converted PDF), not the original pptx bytes.
    expect(frame).toHaveAttribute("src", "/api/blobs/blob-preview-pdf-1");
  });
});

describe("KbDocViewer — replay entry (#51 P6)", () => {
  afterEach(cleanup);

  it("offers a replay action for documents whose processing involved AI", async () => {
    const client = fakeClient({
      "col-1/u/inv-1.chat.json": mkDoc({
        filename: "inv-1.chat.json",
        content_type: "application/json",
        markdown: "```json\n{}\n```",
      }),
    });
    render(
      <KbDocViewer documentId="col-1/u/inv-1.chat.json" onClose={() => {}} client={client} />,
    );
    const btn = await screen.findByRole("button", { name: /test ai/i });
    await userEvent.click(btn);
    // The replay dialog opens (its probe runs against the real client —
    // here it just shows the dialog frame).
    expect(await screen.findByRole("dialog", { name: /replay/i })).toBeInTheDocument();
  });

  it("hides the action for documents with no AI step", async () => {
    const client = fakeClient({
      "col-1/u/notes.md": mkDoc({ filename: "notes.md", markdown: "# notes" }),
    });
    render(<KbDocViewer documentId="col-1/u/notes.md" onClose={() => {}} client={client} />);
    await screen.findByText("notes.md");
    expect(screen.queryByRole("button", { name: /test ai/i })).not.toBeInTheDocument();
  });
});
