// @vitest-environment happy-dom
import { afterEach, describe, expect, it, vi } from "vitest";

import { realApi } from "./real";

function fetchSpy(body: string) {
  const calls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      calls.push(String(input));
      return new Response(body, { status: 200, headers: { "content-type": "application/json" } });
    }),
  );
  return calls;
}

describe("getWorkspaceUsage (#245)", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("GETs /a/{slug}/items/{id}/files/usage and returns {used, quota}", async () => {
    const calls = fetchSpy(JSON.stringify({ used: 500, quota: 1000 }));
    const usage = await realApi.getWorkspaceUsage("rca", "inv1");
    expect(usage).toEqual({ used: 500, quota: 1000 });
    expect(calls[0]).toContain("/a/rca/items/inv1/files/usage");
  });
});
