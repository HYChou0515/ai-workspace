import { describe, expect, it, vi } from "vitest";

// Simulate a sub-path deploy (VITE_BASE_PATH=/sub).
vi.mock("../api/http", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../api/http")>()),
  API_BASE: "/sub",
}));

import { baseAwareUrlTransform } from "./mdUrlTransform";

describe("baseAwareUrlTransform (#73)", () => {
  const kb = baseAwareUrlTransform("kb://");

  it("leaves the preserved in-app scheme untouched (the link handler owns it)", () => {
    expect(kb("kb://doc/abc")).toBe("kb://doc/abc");
  });

  it("prepends the deploy base path to a root-relative BE URL", () => {
    expect(kb("/blobs/x")).toBe("/sub/blobs/x");
  });

  it("leaves absolute http(s) URLs alone", () => {
    expect(kb("https://cdn/y.png")).toBe("https://cdn/y.png");
  });

  it("works for any in-app scheme (wiki://)", () => {
    const wiki = baseAwareUrlTransform("wiki://");
    expect(wiki("wiki://page/x")).toBe("wiki://page/x");
    expect(wiki("/blobs/y")).toBe("/sub/blobs/y");
  });
});
