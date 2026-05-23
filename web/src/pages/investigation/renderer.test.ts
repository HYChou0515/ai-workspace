import { describe, expect, it } from "vitest";

import { basename, breadcrumbSegments, dirChildren, dirname, pickRenderer } from "./renderer";

describe("pickRenderer", () => {
  it("routes /report.vN.md to the report renderer (not generic markdown)", () => {
    expect(pickRenderer("/report.v1.md")).toBe("report");
    expect(pickRenderer("/report.v42.md")).toBe("report");
    expect(pickRenderer("report.v3.md")).toBe("report");
  });

  it("does not match the report pattern for arbitrary .md files", () => {
    expect(pickRenderer("/brief.md")).toBe("markdown");
    expect(pickRenderer("/5-why.md")).toBe("markdown");
    expect(pickRenderer("/notes/report-template.md")).toBe("markdown");
  });

  it("routes by file extension", () => {
    expect(pickRenderer("/analyses/drift.ipynb")).toBe("notebook");
    expect(pickRenderer("/fishbone.canvas")).toBe("fishbone");
    expect(pickRenderer("/data/spc.csv")).toBe("csv");
    expect(pickRenderer("/config.json")).toBe("json");
    expect(pickRenderer("/photos/bridge.png")).toBe("image");
    expect(pickRenderer("/photos/x-ray.JPG")).toBe("image");
  });

  it("falls back to plain text for unknown extensions", () => {
    expect(pickRenderer("/data/log.txt")).toBe("text");
    expect(pickRenderer("/Makefile")).toBe("text");
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
    expect(dirChildren(files, "data/raw")).toEqual([
      { name: "spc.csv", path: "/data/raw/spc.csv", isDir: false },
      { name: "spc2.csv", path: "/data/raw/spc2.csv", isDir: false },
    ]);
  });
});
