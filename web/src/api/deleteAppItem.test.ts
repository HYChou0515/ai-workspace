// @vitest-environment happy-dom
import { afterEach, describe, expect, it, vi } from "vitest";

import { realApi } from "./real";

function fetchSpy(status: number, body = "{}") {
  const calls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      calls.push(String(input));
      return new Response(body, { status, headers: { "content-type": "application/json" } });
    }),
  );
  return calls;
}

describe("deleteAppItem (plan-delete-item-cascade)", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("DELETEs the cascade route, not the raw /permanently", async () => {
    const calls = fetchSpy(204, "");
    await realApi.deleteAppItem("rca", "inv1");
    expect(calls[0]).toContain("/a/rca/items/inv1");
    expect(calls[0]).not.toContain("permanently");
  });

  it("REJECTS on failure — the backend's retry contract must reach the user", async () => {
    // Review H1: the old code never checked resp.ok, so a mid-sweep 500
    // ("retry to resume"), a busy 503 or a 403 all resolved as success — the
    // dialog closed, the list refetched, and the item silently resurrected.
    fetchSpy(500, JSON.stringify({ detail: "retry the delete to resume the sweep" }));
    await expect(realApi.deleteAppItem("rca", "inv1")).rejects.toThrow();
  });
});
