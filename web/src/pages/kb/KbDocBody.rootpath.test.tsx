// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render as rtlRender, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { QueryWrap } from "../../test/queryWrapper";

// Simulate a sub-path deploy: the SPA was built with VITE_BASE_PATH=/sub, so
// every browser-facing URL must carry that prefix (#73). API_BASE is the baked
// base path the whole app reads.
vi.mock("../../api/http", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../api/http")>()),
  API_BASE: "/sub",
}));

import type { KbApi, KbRenderedDoc } from "../../api/kb";
import { KbDocBody } from "./KbDocBody";

const render = (ui: Parameters<typeof rtlRender>[0]) => rtlRender(ui, { wrapper: QueryWrap });

function mkDoc(over: Partial<KbRenderedDoc>): KbRenderedDoc {
  return {
    document_id: "col/u/d",
    collection_id: "col",
    file_id: "blob-1",
    content_type: "text/markdown",
    size: 1,
    chunks: 0,
    cited: 0,
    created_by: "u",
    updated_at: 0,
    status: "ready",
    markdown: "",
    filename: "d",
    ...over,
  } as KbRenderedDoc;
}

function fakeClient(doc: KbRenderedDoc): KbApi {
  return {
    renderDocument: async () => doc,
    getDocChunks: async () => [],
  } as unknown as KbApi;
}

describe("KbDocBody root-path (#73)", () => {
  afterEach(cleanup);

  it("prefixes the deploy base path on the image blob src", async () => {
    const doc = mkDoc({ filename: "x.png", content_type: "image/png", file_id: "blob-png" });
    render(<KbDocBody documentId="col/u/x" onNavigate={() => {}} client={fakeClient(doc)} />);
    const img = await screen.findByRole("img");
    expect(img).toHaveAttribute("src", "/sub/blobs/blob-png");
  });

  it("prefixes the base path on the pdf iframe src", async () => {
    const doc = mkDoc({ filename: "x.pdf", content_type: "application/pdf", file_id: "blob-pdf" });
    render(<KbDocBody documentId="col/u/x" onNavigate={() => {}} client={fakeClient(doc)} />);
    const frame = await screen.findByTitle("x.pdf");
    expect(frame).toHaveAttribute("src", "/sub/blobs/blob-pdf");
  });

  it("prefixes the base path on the preview iframe src", async () => {
    const doc = mkDoc({
      filename: "deck.pptx",
      content_type: "application/octet-stream",
      preview_file_id: "blob-preview",
    });
    render(<KbDocBody documentId="col/u/x" onNavigate={() => {}} client={fakeClient(doc)} />);
    const frame = await screen.findByTitle("deck.pptx");
    expect(frame).toHaveAttribute("src", "/sub/blobs/blob-preview");
  });

  it("prefixes the base path on root-relative images inside the BE-rendered markdown", async () => {
    // The BE embeds image siblings as `/blobs/{id}` in the rendered markdown.
    const doc = mkDoc({ filename: "d.md", markdown: "![chart](/blobs/embedded-img)" });
    render(<KbDocBody documentId="col/u/d" onNavigate={() => {}} client={fakeClient(doc)} />);
    const img = await screen.findByRole("img", { name: "chart" });
    expect(img).toHaveAttribute("src", "/sub/blobs/embedded-img");
  });
});
