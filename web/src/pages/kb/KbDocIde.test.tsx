// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactElement } from "react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { KbApi, KbDocument } from "../../api/kb";
import { renderWithQuery as renderQ } from "../../test/queryWrapper";
import { docHref } from "./kbLinks";

/** Surfaces the URL so a test can assert navigation. */
function LocationProbe() {
  const loc = useLocation();
  return <div data-testid="loc">{loc.pathname + loc.search}</div>;
}

// KbDocIde is now URL-driven (#93): the open doc is the `documents/*` splat.
// Mount it under that route so opening a tree node navigates and the same route
// re-renders with the doc open — existing test bodies keep calling
// `renderWithQuery(<KbDocIde …/>)` unchanged.
function renderWithQuery(ui: ReactElement, start = "/kb/collections/c1/documents") {
  return renderQ(
    <MemoryRouter initialEntries={[start]}>
      <Routes>
        <Route path="/kb/collections/:cid/documents/*" element={ui} />
      </Routes>
      <LocationProbe />
    </MemoryRouter>,
  );
}

// kbFileService reads doc bytes through specstar auto-CRUD (apiFetch), not the
// KbApi — serve a content envelope + raw blob so opening a doc resolves.
vi.mock("../../api/http", () => ({
  API_BASE: "",
  apiFetch: vi.fn(async (path: string) => {
    if (/\/blobs\//.test(path)) return new Response("# Hello KB\n\nbody", { status: 200 });
    if (/^\/source-doc\/[^/]+$/.test(path)) {
      return new Response(
        JSON.stringify({
          data: { content: { file_id: "fid", content_type: "text/markdown", size: 10 } },
          revision_info: { revision_id: "rev-1" },
        }),
        { status: 200, headers: { "content-type": "application/json" } },
      );
    }
    return new Response("not found", { status: 404 });
  }),
}));

import { KbDocIde } from "./KbDocIde";

function doc(partial: Partial<KbDocument> & { path: string }): KbDocument {
  return {
    resource_id: `id:${partial.path}`,
    content_type: "text/markdown",
    created_by: "me",
    status: "ready",
    ...partial,
  };
}

function stubClient(items: KbDocument[], over: Partial<KbApi> = {}): KbApi {
  return {
    listDocuments: async () => ({
      items,
      total: items.length,
      offset: 0,
      limit: 200,
      has_more: false,
    }),
    ...over,
  } as unknown as KbApi;
}

describe("KbDocIde", () => {
  afterEach(cleanup);

  it("renders the collection's documents as a tree", async () => {
    renderWithQuery(
      <KbDocIde
        collectionId="c1"
        client={stubClient([doc({ path: "/notes.md" }), doc({ path: "/data.csv" })])}
      />,
    );
    expect(await screen.findByText("notes.md")).toBeInTheDocument();
    expect(screen.getByText("data.csv")).toBeInTheDocument();
    // nothing open yet → the pick-a-document prompt
    expect(screen.getByText(/select a document/i)).toBeInTheDocument();
  });

  it("shows the upload empty-state for a collection with no documents", async () => {
    renderWithQuery(<KbDocIde collectionId="c1" client={stubClient([])} />);
    expect(await screen.findByText(/upload markdown, text, or an archive/i)).toBeInTheDocument();
  });

  it("shows an empty folder from its .gitkeep, hiding the placeholder itself", async () => {
    renderWithQuery(
      <KbDocIde
        collectionId="c1"
        client={stubClient([doc({ path: "/notes.md" }), doc({ path: "/empty/.gitkeep" })])}
      />,
    );
    expect(await screen.findByText("notes.md")).toBeInTheDocument();
    expect(screen.getByText("empty")).toBeInTheDocument(); // the folder shows…
    expect(screen.queryByText(".gitkeep")).not.toBeInTheDocument(); // …its placeholder doesn't
  });

  it("badges a still-indexing document in the tree", async () => {
    renderWithQuery(
      <KbDocIde
        collectionId="c1"
        client={stubClient([doc({ path: "/fresh.md", status: "indexing" })])}
      />,
    );
    expect(await screen.findByText("fresh.md")).toBeInTheDocument();
    expect(screen.getByTitle("Indexing…")).toBeInTheDocument();
  });

  it("shows the active doc's path + status + chunks/cited in the bottom status bar", async () => {
    const user = userEvent.setup();
    renderWithQuery(
      <KbDocIde
        collectionId="c1"
        client={stubClient([
          doc({ path: "/hello.md", status: "ready", chunks: 4, cited: 2, size: 1280 }),
        ])}
      />,
    );
    await user.click(await screen.findByText("hello.md"));
    const bar = await screen.findByTestId("kb-ide-status");
    expect(bar).toHaveTextContent("/hello.md");
    expect(bar).toHaveTextContent("ready");
    expect(bar).toHaveTextContent("4 chunks");
    expect(bar).toHaveTextContent("cited 2×");
  });

  it("creates a new file inside an inferred (relative-stored) folder, not at root", async () => {
    const user = userEvent.setup();
    const uploadDocument = vi.fn(async (_c: string, _f: File, _p?: string) => ["doc-new"]);
    // A real upload stores a relative path (no leading slash); the tree must
    // still treat its folder as the create target.
    renderWithQuery(
      <KbDocIde
        collectionId="c1"
        client={stubClient([doc({ path: "mydir/report.md" })], { uploadDocument })}
      />,
    );
    await user.click(await screen.findByText("mydir")); // select the folder
    await user.click(screen.getByTitle(/new file/i)); // toolbar "new file"
    await user.type(await screen.findByPlaceholderText("file name"), "new.md{Enter}");

    expect(uploadDocument).toHaveBeenCalled();
    const [, , path] = uploadDocument.mock.calls[0]!;
    expect(path).toBe("/mydir/new.md"); // under the folder, NOT "/new.md"
  });

  const dropPayload = (paths: string[]) => ({
    dataTransfer: {
      getData: (t: string) => (t === "application/x-rca-file" ? JSON.stringify({ paths }) : ""),
      types: ["application/x-rca-file"],
    },
  });

  it("drags a file into a folder, moving it there", async () => {
    const moveDocument = vi.fn(async () => {});
    renderWithQuery(
      <KbDocIde
        collectionId="c1"
        client={stubClient([doc({ path: "a.md" }), doc({ path: "mydir/keep.md" })], { moveDocument })}
      />,
    );
    fireEvent.drop(await screen.findByText("mydir"), dropPayload(["/a.md"]));
    await vi.waitFor(() => expect(moveDocument).toHaveBeenCalledWith("id:a.md", "/mydir/a.md"));
  });

  it("drags a folder into another folder, fanning the move out over its docs", async () => {
    const moveDocument = vi.fn(async () => {});
    renderWithQuery(
      <KbDocIde
        collectionId="c1"
        client={stubClient([doc({ path: "src/a.md" }), doc({ path: "dst/keep.md" })], { moveDocument })}
      />,
    );
    fireEvent.drop(await screen.findByText("dst"), dropPayload(["/src"]));
    await vi.waitFor(() =>
      expect(moveDocument).toHaveBeenCalledWith("id:src/a.md", "/dst/src/a.md"),
    );
  });

  it("a freshly-moved file opens once the doc list catches up, not 'unknown KB document'", async () => {
    // Stateful stub: moveDocument re-keys the path; listDocuments reflects it on
    // the next refetch. The open must wait for that, not read against the stale
    // list (which would throw 'unknown KB document').
    const items: KbDocument[] = [doc({ path: "a.md" }), doc({ path: "mydir/keep.md" })];
    const client = {
      // Real refetch has latency — that lag IS the bug's window.
      listDocuments: async () => {
        await new Promise((r) => setTimeout(r, 20));
        return { items: [...items], total: items.length, offset: 0, limit: 200, has_more: false };
      },
      moveDocument: async (id: string, to: string) => {
        const i = items.findIndex((d) => d.resource_id === id);
        if (i >= 0) items[i] = { ...items[i], path: to };
      },
    } as unknown as KbApi;
    renderWithQuery(<KbDocIde collectionId="c1" client={client} />);
    fireEvent.drop(await screen.findByText("mydir"), dropPayload(["/a.md"]));
    expect(await screen.findByRole("heading", { name: "Hello KB" })).toBeInTheDocument();
    expect(screen.queryByText(/unknown KB document/i)).not.toBeInTheDocument();
  });

  it("creating a file in a folder opens it without 'unknown KB document'", async () => {
    const user = userEvent.setup();
    const items: KbDocument[] = [doc({ path: "mydir/keep.md" })];
    const client = {
      listDocuments: async () => {
        await new Promise((r) => setTimeout(r, 20));
        return { items: [...items], total: items.length, offset: 0, limit: 200, has_more: false };
      },
      uploadDocument: async (_c: string, f: File, p?: string) => {
        const path = (p ?? f.name).replace(/^\/+/, ""); // BE canonicalises → relative
        items.push({ ...doc({ path }), resource_id: `id:${path}` });
        return [`id:${path}`];
      },
    } as unknown as KbApi;
    renderWithQuery(<KbDocIde collectionId="c1" client={client} />);
    await user.click(await screen.findByText("mydir"));
    await user.click(screen.getByTitle(/new file/i));
    await user.type(await screen.findByPlaceholderText("file name"), "new.md{Enter}");
    // the freshly-created file opens (mock blob renders) and never errors
    expect(await screen.findByRole("heading", { name: "Hello KB" })).toBeInTheDocument();
    expect(screen.queryByText(/unknown KB document/i)).not.toBeInTheDocument();
  });

  it("reindexes the open document from the editor header", async () => {
    const user = userEvent.setup();
    const reindexDocument = vi.fn(async () => {});
    const d = doc({ path: "/hello.md", status: "ready", chunks: 2 });
    renderWithQuery(<KbDocIde collectionId="c1" client={stubClient([d], { reindexDocument })} />);
    await user.click(await screen.findByText("hello.md"));
    await user.click(await screen.findByRole("button", { name: /reindex/i }));
    expect(reindexDocument).toHaveBeenCalledWith(d.resource_id);
  });

  it("reindexes a multi-selection from the tree context menu (#98)", async () => {
    const user = userEvent.setup();
    const reindexDocument = vi.fn(async () => {});
    renderWithQuery(
      <KbDocIde
        collectionId="c1"
        client={stubClient([doc({ path: "/a.md" }), doc({ path: "/b.md" })], { reindexDocument })}
      />,
    );
    await user.click(await screen.findByText("a.md"));
    await user.keyboard("{Control>}");
    await user.click(screen.getByText("b.md"));
    await user.keyboard("{/Control}");
    fireEvent.contextMenu(screen.getByText("b.md"));
    const menu = await screen.findByTestId("tree-context-menu");
    await user.click(within(menu).getByRole("button", { name: /^reindex$/i }));
    expect(reindexDocument).toHaveBeenCalledWith("id:/a.md");
    expect(reindexDocument).toHaveBeenCalledWith("id:/b.md");
  });

  it("the status-bar chunks count links to the doc's full chunks page", async () => {
    const user = userEvent.setup();
    const d = doc({ path: "/hello.md", status: "ready", chunks: 4, size: 1280 });
    renderWithQuery(<KbDocIde collectionId="c1" client={stubClient([d])} />);
    await user.click(await screen.findByText("hello.md"));
    const link = await screen.findByRole("link", { name: /chunks/i });
    expect(link).toHaveAttribute("href", docHref(d.resource_id));
  });

  it("opens a document into the editor with a preview/edit toggle and Save", async () => {
    const user = userEvent.setup();
    renderWithQuery(<KbDocIde collectionId="c1" client={stubClient([doc({ path: "/hello.md" })])} />);
    await user.click(await screen.findByText("hello.md"));
    // the markdown renders (preview mode) …
    expect(await screen.findByRole("heading", { name: "Hello KB" })).toBeInTheDocument();
    // … with the shared editor controls
    expect(screen.getByRole("button", { name: "Edit" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /save/i })).toBeInTheDocument();
  });

  it("opening a document routes to its URL (#93)", async () => {
    const user = userEvent.setup();
    renderWithQuery(<KbDocIde collectionId="c1" client={stubClient([doc({ path: "/hello.md" })])} />);
    await user.click(await screen.findByText("hello.md"));
    expect(screen.getByTestId("loc")).toHaveTextContent("/kb/collections/c1/documents/hello.md");
  });

  it("deep-links to a nested, spaced doc path through the URL splat (#93)", async () => {
    // Round-trip the trickiest id shape: a sub-folder + a space. The segment is
    // percent-encoded (a%20dir) but the slash stays a real separator.
    renderWithQuery(
      <KbDocIde collectionId="c1" client={stubClient([doc({ path: "/a dir/b.md" })])} />,
      "/kb/collections/c1/documents/a%20dir/b.md",
    );
    // the doc opens straight from the URL (its bytes render + the status bar
    // shows the decoded canonical path)
    expect(await screen.findByRole("heading", { name: "Hello KB" })).toBeInTheDocument();
    expect(await screen.findByTestId("kb-ide-status")).toHaveTextContent("/a dir/b.md");
  });
});
