// @vitest-environment happy-dom
//
// URL composition for KB document endpoints when the doc_id contains
// U+2215 ∕ (DIVISION SLASH) — the look-alike the backend's
// `encode_doc_id` uses in place of ASCII `/` to keep the id slash-free
// (see src/workspace_app/kb/doc_id.py).
//
// Issue #34: clicking a reference card 404s because something in the
// chain leaves the ∕ unencoded. These tests pin every URL `kbApi`
// composes for a document and assert the ∕ comes through as %E2%88%95
// (its UTF-8 percent encoding) — never as a raw character.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { kbApi } from "./kb";

// One id with three ∕ separators (the kind real `encode_doc_id`
// output looks like: `<collection>∕<user>∕<path>`).
const DOC_ID = "col-1∕me∕manuals∕reflow∕guide.md";

const ENCODED = "col-1%E2%88%95me%E2%88%95manuals%E2%88%95reflow%E2%88%95guide.md";

type CapturedFetch = ReturnType<typeof installFetchSpy>;

function installFetchSpy() {
  const calls: string[] = [];
  const orig = globalThis.fetch;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      calls.push(url);
      return new Response(JSON.stringify({}), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    }),
  );
  return {
    calls,
    restore: () => {
      vi.unstubAllGlobals();
      globalThis.fetch = orig;
    },
  };
}

describe("kbApi URL composition for ∕-containing doc_ids", () => {
  let captured: CapturedFetch;
  beforeEach(() => {
    captured = installFetchSpy();
  });
  afterEach(() => {
    captured.restore();
  });

  it("renderDocument URL encodes every ∕ as %E2%88%95", async () => {
    await kbApi.renderDocument(DOC_ID);
    expect(captured.calls).toHaveLength(1);
    const url = captured.calls[0]!;
    expect(url).toContain(`/kb/documents?id=${ENCODED}`);
    // No raw ∕ leaked into the URL.
    expect(url).not.toContain("∕");
  });

  it("getDocChunks URL encodes every ∕", async () => {
    await kbApi.getDocChunks(DOC_ID);
    expect(captured.calls).toHaveLength(1);
    expect(captured.calls[0]!).toContain(`/kb/documents/chunks?id=${ENCODED}`);
    expect(captured.calls[0]!).not.toContain("∕");
  });

  it("reindexDocument URL encodes every ∕", async () => {
    await kbApi.reindexDocument(DOC_ID);
    expect(captured.calls[0]!).toContain(`/kb/documents/reindex?id=${ENCODED}`);
    expect(captured.calls[0]!).not.toContain("∕");
  });

  it("deleteDocument URL encodes every ∕", async () => {
    await kbApi.deleteDocument(DOC_ID);
    expect(captured.calls[0]!).toContain(`/kb/documents?id=${ENCODED}`);
    expect(captured.calls[0]!).not.toContain("∕");
  });
});
