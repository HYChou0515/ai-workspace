import { describe, expect, it } from "vitest";

import { mockApi } from "./mock";
import { realApi } from "./real";

describe("getCurrentUser", () => {
  it("mock client resolves a user id", async () => {
    expect(await mockApi.getCurrentUser()).toBe("default-user");
  });

  it("real client resolves the id from GET /me", async () => {
    const orig = globalThis.fetch;
    globalThis.fetch = (async () =>
      new Response(JSON.stringify({ id: "alice", name: "Alice Chen" }), {
        status: 200,
        headers: { "content-type": "application/json" },
      })) as typeof fetch;
    try {
      expect(await realApi.getCurrentUser()).toBe("alice");
    } finally {
      globalThis.fetch = orig;
    }
  });
});
