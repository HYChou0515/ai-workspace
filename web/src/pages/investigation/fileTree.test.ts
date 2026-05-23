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

  it("includes empty directories passed explicitly", () => {
    const tree = buildFileTree([{ path: "/brief.md", size: 1 }], ["/empty", "/data/inner"]);
    const names = tree.map((n) => `${n.name}${n.isDir ? "/" : ""}`);
    // dirs first (alpha), then the file
    expect(names).toEqual(["data/", "empty/", "brief.md"]);
    const data = tree.find((n) => n.name === "data")!;
    expect(data.children.map((c) => c.name)).toEqual(["inner"]);
    expect(data.children[0]!.isDir).toBe(true);
    expect(tree.find((n) => n.name === "empty")!.children).toEqual([]);
  });

  it("does not duplicate a dir that also has files", () => {
    const tree = buildFileTree([{ path: "/data/a.csv", size: 1 }], ["/data"]);
    expect(tree.filter((n) => n.name === "data")).toHaveLength(1);
    expect(tree[0]!.children.map((c) => c.name)).toEqual(["a.csv"]);
  });
});
