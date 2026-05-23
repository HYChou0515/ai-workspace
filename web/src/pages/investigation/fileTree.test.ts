import { describe, expect, it } from "vitest";

import { buildFileTree } from "./fileTree";

describe("buildFileTree", () => {
  it("nests files under inferred folders", () => {
    const tree = buildFileTree([
      { path: "/brief.md", size: 10 },
      { path: "/data/a.csv", size: 5 },
      { path: "/data/b.csv", size: 6 },
    ]);
    // dirs first, then files
    expect(tree.map((n) => `${n.name}${n.isDir ? "/" : ""}`)).toEqual(["data/", "brief.md"]);
    const data = tree.find((n) => n.name === "data")!;
    expect(data.isDir).toBe(true);
    expect(data.children.map((c) => c.name)).toEqual(["a.csv", "b.csv"]);
    expect(data.children[0]!.path).toBe("/data/a.csv");
  });

  it("handles deep nesting", () => {
    const tree = buildFileTree([{ path: "/a/b/c.txt", size: 1 }]);
    const a = tree[0]!;
    expect(a.name).toBe("a");
    const b = a.children[0]!;
    expect(b.name).toBe("b");
    expect(b.children[0]!.path).toBe("/a/b/c.txt");
    expect(b.children[0]!.isDir).toBe(false);
  });

  it("sorts files alphabetically", () => {
    const tree = buildFileTree([
      { path: "/z.md", size: 1 },
      { path: "/a.md", size: 1 },
    ]);
    expect(tree.map((n) => n.name)).toEqual(["a.md", "z.md"]);
  });
});
