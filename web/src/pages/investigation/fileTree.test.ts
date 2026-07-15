import { describe, expect, it } from "vitest";

import { buildFileTree, pruneTree } from "./fileTree";

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

  // The three below pin the identities the sibling scans used to compare, so a
  // lookup-index rewrite cannot quietly change what counts as "already there".

  it("keeps a file and a folder that share a name", () => {
    const tree = buildFileTree([{ path: "/data", size: 1 }], ["/data"]);
    expect(tree.map((n) => `${n.name}${n.isDir ? "/" : ""}`)).toEqual(["data/", "data"]);
  });

  it("drops a duplicate file rather than listing it twice", () => {
    const tree = buildFileTree([
      { path: "/data/a.csv", size: 1 },
      { path: "/data/a.csv", size: 999 },
    ]);
    expect(tree[0]!.children).toHaveLength(1);
    expect(tree[0]!.children[0]!.size).toBe(1); // first wins
  });

  it("dedupes on the normalised parent+name, keeping the first path verbatim", () => {
    // Both collapse to parent "/data", name "a.csv" once empty segments are
    // dropped — the second is a duplicate even though the raw strings differ.
    const tree = buildFileTree([
      { path: "/data//a.csv", size: 1 },
      { path: "/data/a.csv", size: 2 },
    ]);
    expect(tree[0]!.children).toHaveLength(1);
    expect(tree[0]!.children[0]!.path).toBe("/data//a.csv");
  });

  it("stays linear when thousands of files share one directory", () => {
    // The sibling scans made this O(N²): 8000 flat files took 1.3 s, which is
    // the whole reason the doc page stalls. Correctness proxy for the perf fix.
    const files = Array.from({ length: 4000 }, (_, i) => ({ path: `/f${i}.txt`, size: 1 }));
    const t0 = performance.now();
    const tree = buildFileTree(files);
    const ms = performance.now() - t0;
    expect(tree).toHaveLength(4000);
    // A quadratic build is ~300 ms here; a linear one is single-digit. The
    // bound is loose on purpose — it must catch the shape, not time the CI box.
    expect(ms).toBeLessThan(100);
  });
});

describe("pruneTree", () => {
  const sample = () =>
    buildFileTree([
      { path: "/brief.md", size: 1 },
      { path: "/reports/2024/q1.md", size: 1 },
      { path: "/reports/2024/q2.md", size: 1 },
      { path: "/notes/todo.txt", size: 1 },
    ]);

  it("keeps a matching file and its ancestor folders, expanding them", () => {
    const { tree, expand } = pruneTree(sample(), "q1");
    // only the /reports/2024 branch survives, down to q1.md
    expect(tree.map((n) => n.name)).toEqual(["reports"]);
    const reports = tree[0]!;
    expect(reports.children.map((n) => n.name)).toEqual(["2024"]);
    const y2024 = reports.children[0]!;
    expect(y2024.children.map((n) => n.name)).toEqual(["q1.md"]);
    // ancestor dirs are marked for auto-expand
    expect(expand.has("/reports")).toBe(true);
    expect(expand.has("/reports/2024")).toBe(true);
  });

  it("matching a folder name keeps its whole subtree", () => {
    const { tree } = pruneTree(sample(), "reports");
    expect(tree.map((n) => n.name)).toEqual(["reports"]);
    const y2024 = tree[0]!.children[0]!;
    expect(y2024.children.map((n) => n.name)).toEqual(["q1.md", "q2.md"]);
  });

  it("is case-insensitive", () => {
    const { tree } = pruneTree(sample(), "TODO");
    expect(tree.map((n) => n.name)).toEqual(["notes"]);
    expect(tree[0]!.children.map((n) => n.name)).toEqual(["todo.txt"]);
  });

  it("returns an empty tree when nothing matches", () => {
    const { tree, expand } = pruneTree(sample(), "zzz-nope");
    expect(tree).toEqual([]);
    expect(expand.size).toBe(0);
  });

  it("is a no-op for an empty / whitespace term (keeps collapse state)", () => {
    const input = sample();
    const { tree, expand } = pruneTree(input, "   ");
    expect(tree).toBe(input); // same reference — untouched
    expect(expand.size).toBe(0);
  });
});
