// @vitest-environment happy-dom
//
// #506/#577 follow-up: the real kbApi.getLatestCardGenFunnel — the last finalized
// run's funnel for a collection's 待審核 tab. Stubs fetch (the FE tests otherwise
// exercise the mock client) so the real URL composition + JSON parse are covered.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { kbApi } from "./kb";

const FUNNEL = { n_units: 4, n_raw_drafts: 9, n_proposals: 6, n_skipped_indexing: 2 };

describe("kbApi.getLatestCardGenFunnel", () => {
  const calls: string[] = [];

  beforeEach(() => {
    calls.length = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        calls.push(typeof input === "string" ? input : (input as URL).href ?? String(input));
        return new Response(JSON.stringify(FUNNEL), {
          status: 200,
          headers: { "content-type": "application/json" },
        });
      }),
    );
  });
  afterEach(() => vi.unstubAllGlobals());

  it("GETs the collection's latest-funnel endpoint and returns the parsed funnel", async () => {
    const got = await kbApi.getLatestCardGenFunnel("col-1");
    expect(calls).toHaveLength(1);
    expect(calls[0]!).toContain("/kb/collections/col-1/context-card-gen/latest");
    expect(got).toEqual(FUNNEL);
  });

  it("percent-encodes the collection id in the path", async () => {
    await kbApi.getLatestCardGenFunnel("a/b");
    expect(calls[0]!).toContain("/kb/collections/a%2Fb/context-card-gen/latest");
  });
});
