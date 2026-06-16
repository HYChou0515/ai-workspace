// @vitest-environment happy-dom
//
// Issue #34: a `/kb/doc/<encoded-id>` URL where the id contains ∕
// (U+2215) must round-trip back to a documentId that, when re-encoded
// for the API call, doesn't double-encode or drop the ∕.

import "@testing-library/jest-dom/vitest";
import { cleanup, render as rtlRender, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import type { KbApi, KbRenderedDoc } from "../../api/kb";
import { QueryWrap } from "../../test/queryWrapper";
import { KbDocPage } from "./KbDocPage";

const render = (ui: Parameters<typeof rtlRender>[0]) => rtlRender(ui, { wrapper: QueryWrap });

const DIVISION_SLASH = "∕"; // ∕

describe("KbDocPage with a ∕-containing doc_id splat", () => {
  let received: string[] = [];

  beforeEach(() => {
    received = [];
  });
  afterEach(cleanup);

  const fakeClient: KbApi = {
    renderDocument: async (id: string): Promise<KbRenderedDoc> => {
      received.push(id);
      return {
        document_id: id,
        collection_id: "col-1",
        filename: "guide.md",
        markdown: "# Guide\n\nbody",
        file_id: "blob-1",
        content_type: "text/markdown",
        size: 1024,
        chunks: 1,
        cited: 0,
        created_by: "me",
        updated_at: 0,
        status: "ready",
      };
    },
    getDocChunks: async () => [],
  } as unknown as KbApi;

  it("decodes %E2%88%95 splat segments back to the raw ∕ doc_id", async () => {
    // URL the FE writes when navigating to /kb/doc/<docPath-encoded-id>.
    const url = "/kb/doc/col-1%E2%88%95me%E2%88%95guide.md";
    render(
      <MemoryRouter initialEntries={[url]}>
        <Routes>
          <Route path="/kb/doc/*" element={<KbDocPage client={fakeClient} />} />
        </Routes>
      </MemoryRouter>,
    );
    // The component pulled params["*"] and handed it to renderDocument.
    await waitFor(() => expect(received).toHaveLength(1));
    // The id reaching the API client should have raw ∕ — react-router
    // SHOULD have decoded %E2%88%95 back, and kb.ts's
    // encodeURIComponent will re-encode for the HTTP call.
    expect(received[0]).toBe(`col-1${DIVISION_SLASH}me${DIVISION_SLASH}guide.md`);
  });

  it("handles a navigation that pasted the raw ∕ unencoded too (defensive)", async () => {
    // Someone pasted /kb/doc/col-1∕me∕guide.md straight into the bar.
    const url = `/kb/doc/col-1${DIVISION_SLASH}me${DIVISION_SLASH}guide.md`;
    render(
      <MemoryRouter initialEntries={[url]}>
        <Routes>
          <Route path="/kb/doc/*" element={<KbDocPage client={fakeClient} />} />
        </Routes>
      </MemoryRouter>,
    );
    await waitFor(() => expect(received).toHaveLength(1));
    expect(received[0]).toBe(`col-1${DIVISION_SLASH}me${DIVISION_SLASH}guide.md`);
  });
});
