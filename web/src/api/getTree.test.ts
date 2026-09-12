// @vitest-environment happy-dom
import { afterEach, describe, expect, it, vi } from "vitest";

import { realApi } from "./real";

const EMPTY = JSON.stringify({ files: [], dirs: [], unwalked: [], truncated: false });

function fetchSpy() {
  const calls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      calls.push(String(input));
      return new Response(EMPTY, { status: 200, headers: { "content-type": "application/json" } });
    }),
  );
  return calls;
}

describe("getTree — the lazy-folder query string", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("sends prefix and depth, and nothing for the preload", async () => {
    const calls = fetchSpy();
    await realApi.getTree("rca", "inv1");
    await realApi.getTree("rca", "inv1", { prefix: "/node_modules", depth: 1 });
    expect(calls[0]).toMatch(/\/tree$/);
    expect(calls[1]).toContain("/tree?prefix=%2Fnode_modules&depth=1");
  });

  it("does not depend on URLSearchParams.size, which older browsers lack", async () => {
    // Chrome < 113 / Safari < 17 / Firefox < 115 have no `.size`; there the
    // query would have been dropped and every expand would have fetched the
    // whole preload as the folder's level.
    vi.spyOn(URLSearchParams.prototype, "size", "get").mockReturnValue(undefined as never);
    const calls = fetchSpy();
    await realApi.getTree("rca", "inv1", { prefix: "/dist", depth: 1 });
    expect(calls[0]).toContain("/tree?prefix=%2Fdist&depth=1");
  });
});
