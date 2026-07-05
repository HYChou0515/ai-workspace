// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
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
  API_PREFIX: "/api",
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
  const counts: Record<string, number> = {};
  for (const d of items) counts[d.status] = (counts[d.status] ?? 0) + 1;
  return {
    listDocuments: async () => ({
      items,
      total: items.length,
      offset: 0,
      limit: 2000,
      has_more: false,
    }),
    // #395: the IDE polls this summary (not the list) while docs index.
    documentsStatus: async () => ({ total: items.length, counts, runs: {}, latest_ms: 0 }),
    // The open-a-document fields (rationale / parser guidance) come from the
    // cheap SourceDoc-envelope fetch, not the heavy render call nor the list row.
    getSourceDocMeta: async (id: string) => {
      const d = items.find((x) => x.resource_id === id) as
        | (KbDocument & { quality_rationale?: string; parser_guidance_override?: string })
        | undefined;
      return {
        quality_rationale: d?.quality_rationale,
        parser_guidance_override: d?.parser_guidance_override,
      };
    },
    // Still stubbed (the citation drawer uses it) — but the IDE must NOT call it
    // on open; tests assert that.
    renderDocument: async (id: string) => {
      const d = items.find((x) => x.resource_id === id);
      return {
        document_id: id,
        filename: d?.path.split("/").pop() ?? id,
        collection_id: "c1",
        markdown: "",
        file_id: "fid",
        content_type: d?.content_type ?? "text/markdown",
        size: d?.size ?? 0,
        chunks: d?.chunks ?? 0,
        cited: d?.cited ?? 0,
        created_by: d?.created_by ?? "me",
        updated_at: d?.updated_at ?? 0,
        status: d?.status ?? "ready",
        quality_score: d?.quality_score,
      };
    },
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

  it("offers a file filter and a resizable tree pane (#402)", async () => {
    renderWithQuery(
      <KbDocIde
        collectionId="c1"
        client={stubClient([doc({ path: "/notes.md" }), doc({ path: "/data.csv" })])}
      />,
    );
    // the FileTree filter box (searchable wired on the KB doc IDE)
    expect(await screen.findByPlaceholderText(/filter files/i)).toBeInTheDocument();
    // a drag handle to resize the tree width
    expect(screen.getByRole("separator")).toBeInTheDocument();
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
    // #171: de-jargoned — "處理中…" not "Indexing…".
    expect(screen.getByTitle("處理中…")).toBeInTheDocument();
  });

  it("badges a scored doc's quality in the tree (#105)", async () => {
    renderWithQuery(
      <KbDocIde
        collectionId="c1"
        client={stubClient([doc({ path: "/bad.md", quality_score: 22 })])}
      />,
    );
    expect(await screen.findByText("bad.md")).toBeInTheDocument();
    const badge = screen.getByTestId("kb-quality-badge");
    expect(badge).toHaveTextContent("22");
    expect(badge.className).toContain("kb-quality--bad");
  });

  it("shows the quality verdict in the status bar, rationale from the cheap doc-meta fetch (#105/#395)", async () => {
    // Opening a doc fetches just {rationale, guidance} from the SourceDoc
    // envelope (getSourceDocMeta) — NOT the heavy renderDocument, whose markdown
    // body the IDE discards while it re-reads the blob + runs count queries.
    const user = userEvent.setup();
    const base = stubClient([doc({ path: "/bad.md", quality_score: 22 })]);
    const getSourceDocMeta = vi.fn(async (_id: string) => ({
      quality_rationale: "OCR soup, no structure.",
    }));
    const renderDocument = vi.fn(base.renderDocument);
    renderWithQuery(
      <KbDocIde
        collectionId="c1"
        client={{ ...base, getSourceDocMeta, renderDocument } as unknown as KbApi}
      />,
    );
    await user.click(await screen.findByText("bad.md"));
    const verdict = await screen.findByTestId("kb-ide-quality");
    expect(within(verdict).getByTestId("kb-quality-badge")).toHaveTextContent("22");
    // #460 P7: the rationale now expands from the verdict (was hover-only + clipped)
    await user.click(within(verdict).getByTestId("kb-quality-details-toggle"));
    await within(verdict).findByText("OCR soup, no structure.");
    expect(getSourceDocMeta).toHaveBeenCalledWith("id:/bad.md");
    // the heavy render call is never made when merely opening a doc
    expect(renderDocument).not.toHaveBeenCalled();
  });

  it("expands the quality breakdown detail scores from the doc-meta fetch (#460 P8)", async () => {
    const user = userEvent.setup();
    const base = stubClient([doc({ path: "/bad.md", quality_score: 22 })]);
    const getSourceDocMeta = vi.fn(async (_id: string) => ({
      quality_rationale: "OCR soup.",
      quality_breakdown: { structure: 2, readability: 3 },
    }));
    renderWithQuery(
      <KbDocIde
        collectionId="c1"
        client={{ ...base, getSourceDocMeta } as unknown as KbApi}
      />,
    );
    await user.click(await screen.findByText("bad.md"));
    const verdict = await screen.findByTestId("kb-ide-quality");
    await user.click(within(verdict).getByTestId("kb-quality-details-toggle"));
    const bd = await within(verdict).findByTestId("kb-quality-breakdown");
    expect(within(bd).getByTestId("kb-quality-dim-structure")).toHaveTextContent("2");
    expect(within(bd).getByTestId("kb-quality-dim-readability")).toHaveTextContent("3");
  });

  it("prefills the Tune-parsing modal from the cheap doc-meta fetch (#356/#395)", async () => {
    const user = userEvent.setup();
    const base = stubClient([doc({ path: "/tuned.md" })]);
    const client = {
      ...base,
      getSourceDocMeta: async (_id: string) => ({
        parser_guidance_override: "treat tables as JSON",
      }),
      listCollections: async () => [],
    } as unknown as KbApi;
    renderWithQuery(<KbDocIde collectionId="c1" client={client} />);
    await user.click(await screen.findByText("tuned.md"));
    await user.click(await screen.findByRole("button", { name: "調整解析" }));
    const editor = await screen.findByLabelText("解析 prompt");
    expect(editor).toHaveValue("treat tables as JSON");
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
    expect(bar).toHaveTextContent("就緒"); // #171: "ready" → 就緒
    expect(bar).toHaveTextContent("4 chunks");
    expect(bar).toHaveTextContent("cited 2×");
  });

  it("shows a monotonic unit progress bar while a fanned-out doc indexes (#248)", async () => {
    const user = userEvent.setup();
    renderWithQuery(
      <KbDocIde
        collectionId="c1"
        client={stubClient([
          doc({ path: "/big.pdf", status: "indexing", units_done: 8, units_total: 24 }),
        ])}
      />,
    );
    await user.click(await screen.findByText("big.pdf"));
    const bar = await screen.findByTestId("kb-ide-status");
    expect(within(bar).getByTestId("kb-index-progress")).toHaveTextContent("8 / 24");
  });

  it("shows no unit bar for a single-job / ready doc (#248)", async () => {
    const user = userEvent.setup();
    renderWithQuery(
      <KbDocIde
        collectionId="c1"
        client={stubClient([doc({ path: "/note.md", status: "ready", units_total: 0 })])}
      />,
    );
    await user.click(await screen.findByText("note.md"));
    await screen.findByTestId("kb-ide-status");
    expect(screen.queryByTestId("kb-index-progress")).not.toBeInTheDocument();
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
    await user.click(await screen.findByRole("button", { name: /re-read/i }));
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
    await user.click(within(menu).getByRole("button", { name: /^re-read$/i }));
    // Re-indexing >=2 docs confirms first; approve, then both fire.
    const dialog = await screen.findByRole("dialog", { name: "Re-read 2 documents" });
    await user.click(within(dialog).getByRole("button", { name: "Re-read" }));
    expect(reindexDocument).toHaveBeenCalledWith("id:/a.md");
    expect(reindexDocument).toHaveBeenCalledWith("id:/b.md");
  });

  it("does not re-index a >=2 selection when the confirm is cancelled (#98)", async () => {
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
    await user.click(within(menu).getByRole("button", { name: /^re-read$/i }));
    const dialog = await screen.findByRole("dialog", { name: "Re-read 2 documents" });
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(reindexDocument).not.toHaveBeenCalled();
  });

  it("re-indexes a single doc from the tree context menu without confirming (#98)", async () => {
    const user = userEvent.setup();
    const reindexDocument = vi.fn(async () => {});
    renderWithQuery(
      <KbDocIde
        collectionId="c1"
        client={stubClient([doc({ path: "/a.md" }), doc({ path: "/b.md" })], { reindexDocument })}
      />,
    );
    // Right-click a single, unselected doc — one doc re-indexes straight away.
    fireEvent.contextMenu(await screen.findByText("a.md"));
    const menu = await screen.findByTestId("tree-context-menu");
    await user.click(within(menu).getByRole("button", { name: /^re-read$/i }));
    await waitFor(() => expect(reindexDocument).toHaveBeenCalledWith("id:/a.md"));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(reindexDocument).toHaveBeenCalledTimes(1);
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

  // #308 — per-doc read override dialog, opened from the editor header.
  function permFixture(over: Record<string, unknown> = {}) {
    return {
      visibility: "public",
      read_meta: [],
      write_meta: [],
      read_content: [],
      add_content: [],
      edit_content: [],
      read_chat: [],
      converse: [],
      execute: [],
      use_terminal: [],
      change_permission: [],
      ...over,
    };
  }

  async function openDoc(owner: string, over: Partial<KbApi> = {}) {
    const client = stubClient([doc({ path: "/notes.md" })], {
      listCollections: async () => [{ resource_id: "c1", owner }] as never,
      getDocPermission: async () => permFixture({ visibility: "private" }) as never,
      ...over,
    });
    renderWithQuery(<KbDocIde collectionId="c1" client={client} />);
    fireEvent.click(await screen.findByText("notes.md"));
    return client;
  }

  it("#308 offers the collection owner a per-doc Permissions action that opens the dialog", async () => {
    const setDoc = vi.fn(async () => ({ visibility: "private", notified: [] }));
    await openDoc("default-user", { setDocPermission: setDoc as never });
    fireEvent.click(await screen.findByTestId("doc-permissions"));
    // the reused #310 dialog opens for THIS doc
    expect(await screen.findByTestId("permission-dialog")).toBeInTheDocument();
    // saving a tightening override PUTs it
    fireEvent.click(screen.getByTestId("permission-save"));
    await vi.waitFor(() => expect(setDoc).toHaveBeenCalled());
  });

  it("#308 setting the doc back to Public clears the override (reverts to inheritance)", async () => {
    const setDoc = vi.fn(async () => ({ visibility: "public", notified: [] }));
    const clearDoc = vi.fn(async () => {});
    await openDoc("default-user", {
      setDocPermission: setDoc as never,
      clearDocPermission: clearDoc as never,
    });
    fireEvent.click(await screen.findByTestId("doc-permissions"));
    await screen.findByTestId("permission-dialog");
    fireEvent.click(screen.getByTestId("visibility-public"));
    fireEvent.click(screen.getByTestId("permission-save"));
    await vi.waitFor(() => expect(clearDoc).toHaveBeenCalled());
    expect(setDoc).not.toHaveBeenCalled();
  });

  it("#308 hides the Permissions action from a non-owner", async () => {
    await openDoc("someone-else");
    // the editor header renders (the doc opened) but no Permissions button
    expect(await screen.findByTestId("kb-ide-status")).toHaveTextContent("/notes.md");
    expect(screen.queryByTestId("doc-permissions")).not.toBeInTheDocument();
  });
});
