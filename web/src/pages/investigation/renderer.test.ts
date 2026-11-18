import { describe, expect, it } from "vitest";

import {
  basename,
  breadcrumbSegments,
  dirChildren,
  dirname,
  foldedCrumbCount,
  imageMime,
} from "./renderer";

describe("imageMime", () => {
  it("maps extensions to image MIME types", () => {
    expect(imageMime("/a.png")).toBe("image/png");
    expect(imageMime("/a.JPG")).toBe("image/jpeg");
    expect(imageMime("/a.jpeg")).toBe("image/jpeg");
    expect(imageMime("/a.gif")).toBe("image/gif");
    expect(imageMime("/a.svg")).toBe("image/svg+xml");
    expect(imageMime("/a.webp")).toBe("image/webp");
    expect(imageMime("/a.bmp")).toBe("image/bmp");
  });
});

describe("basename", () => {
  it("strips parent path", () => {
    expect(basename("/a/b/c.md")).toBe("c.md");
    expect(basename("brief.md")).toBe("brief.md");
    expect(basename("/")).toBe("");
  });
});

describe("breadcrumbSegments", () => {
  it("returns parent segments only", () => {
    expect(breadcrumbSegments("/analyses/drift.ipynb")).toEqual(["analyses"]);
    expect(breadcrumbSegments("/data/raw/spc.csv")).toEqual(["data", "raw"]);
  });

  it("returns empty for a top-level file", () => {
    expect(breadcrumbSegments("/brief.md")).toEqual([]);
    expect(breadcrumbSegments("brief.md")).toEqual([]);
  });
});

describe("dirname", () => {
  it("returns the parent directory without the leading slash", () => {
    expect(dirname("/data/raw/x.csv")).toBe("data/raw");
    expect(dirname("/data/meta.json")).toBe("data");
  });
  it("returns empty for a root-level file", () => {
    expect(dirname("/brief.md")).toBe("");
    expect(dirname("brief.md")).toBe("");
  });
});

describe("dirChildren", () => {
  const files = [
    "/brief.md",
    "/notes.md",
    "/data/meta.json",
    "/data/raw/spc.csv",
    "/data/raw/spc2.csv",
  ];

  it("lists root entries with folders first", () => {
    expect(dirChildren(files, "")).toEqual([
      { name: "data", path: "data", isDir: true },
      { name: "brief.md", path: "/brief.md", isDir: false },
      { name: "notes.md", path: "/notes.md", isDir: false },
    ]);
  });

  it("lists the immediate children of a subfolder", () => {
    expect(dirChildren(files, "data")).toEqual([
      { name: "raw", path: "data/raw", isDir: true },
      { name: "meta.json", path: "/data/meta.json", isDir: false },
    ]);
  });

  it("lists leaf files", () => {
    // Tree order (shared rule): "spc2.csv" splits into runs spc | 2 | .csv and
    // "spc" is a prefix of "spc.csv", so it sorts first — as in the file tree.
    expect(dirChildren(files, "data/raw")).toEqual([
      { name: "spc2.csv", path: "/data/raw/spc2.csv", isDir: false },
      { name: "spc.csv", path: "/data/raw/spc.csv", isDir: false },
    ]);
  });

  it("orders entries like the file tree, not the locale (numbers by value)", () => {
    // The tree sorts with `treeOrder`; the breadcrumb dropdown must agree, or
    // 10.png sits before 9.png in one and after it in the other.
    const shots = ["/shots/9.png", "/shots/10.png", "/shots/2.png", "/b/x", "/a10/x", "/a9/x"];
    expect(dirChildren(shots, "shots").map((e) => e.name)).toEqual(["2.png", "9.png", "10.png"]);
    expect(dirChildren(shots, "").map((e) => e.name)).toEqual(["a9", "a10", "b", "shots"]);
  });
});

describe("foldedCrumbCount", () => {
  // The path bar is one line. Folders shorten first (each down to a few
  // characters); when even that leaves the file name no room, every folder
  // but the file's own parent folds into one "…".
  const deep = ["projects", "2026-quarterly", "experiments", "run-0042"];

  it("folds nothing before the bar has been measured", () => {
    expect(foldedCrumbCount(0, deep)).toBe(0);
  });

  it("folds nothing when the shortened folders still leave the file room", () => {
    expect(foldedCrumbCount(900, deep)).toBe(0);
  });

  it("folds every folder but the parent when shortening is not enough", () => {
    expect(foldedCrumbCount(260, deep)).toBe(3);
  });

  it("never folds a single folder — a lone … would hide nothing worth a click", () => {
    expect(foldedCrumbCount(60, ["projects"])).toBe(0);
    expect(foldedCrumbCount(60, [])).toBe(0);
  });

  it("asks less room of a short folder name than of a long one", () => {
    // Four one-letter folders fit where four long names do not.
    expect(foldedCrumbCount(340, ["a", "b", "c", "d"])).toBe(0);
    expect(foldedCrumbCount(340, deep)).toBe(3);
  });
});
