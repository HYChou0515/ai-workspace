import { describe, expect, it } from "vitest";

import { relPath } from "./relPath";

describe("relPath", () => {
  it("drops the leading slash the store's key carries", () => {
    expect(relPath("/a.md")).toBe("a.md");
    expect(relPath("/data/x.csv")).toBe("data/x.csv");
  });

  it("leaves an already-relative path alone (idempotent)", () => {
    expect(relPath("data/x.csv")).toBe("data/x.csv");
    expect(relPath(relPath("/data/x.csv"))).toBe("data/x.csv");
  });

  it("collapses the workspace root itself to the empty string", () => {
    expect(relPath("/")).toBe("");
    expect(relPath("")).toBe("");
  });

  it("strips a repeated leading slash, matching the backend's lstrip", () => {
    expect(relPath("//data/x.csv")).toBe("data/x.csv");
  });

  it("never touches slashes that are inside the path", () => {
    expect(relPath("/a/b/c.md")).toBe("a/b/c.md");
  });
});
