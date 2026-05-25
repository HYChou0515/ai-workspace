import { describe, expect, it } from "vitest";

import { mockApi } from "./mock";
import { realApi } from "./real";

describe("getCurrentUser", () => {
  it("mock client resolves a user id", async () => {
    expect(await mockApi.getCurrentUser()).toBe("default-user");
  });

  it("real client resolves the mocked id without hitting the network", async () => {
    // No /me endpoint yet — realApi returns a stub, so this must not fetch.
    const orig = globalThis.fetch;
    globalThis.fetch = (() => {
      throw new Error("getCurrentUser must not call fetch until SSO lands");
    }) as typeof fetch;
    try {
      expect(await realApi.getCurrentUser()).toBe("default-user");
    } finally {
      globalThis.fetch = orig;
    }
  });
});
