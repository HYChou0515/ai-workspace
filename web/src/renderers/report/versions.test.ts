import { describe, expect, it } from "vitest";

import { reportVersions, versionFromPath } from "./versions";

describe("reportVersions", () => {
  it("marks the highest N as current", () => {
    const versions = reportVersions([
      { path: "/report.v1.md", size: 100 },
      { path: "/report.v3.md", size: 200 },
      { path: "/report.v2.md", size: 150 },
    ]);
    expect(versions.map((v) => ({ v: v.v, isCurrent: v.isCurrent }))).toEqual([
      { v: 1, isCurrent: false },
      { v: 2, isCurrent: false },
      { v: 3, isCurrent: true },
    ]);
  });

  it("ignores non-report files", () => {
    const versions = reportVersions([
      { path: "/brief.md", size: 1 },
      { path: "/report.v5.md", size: 1 },
      { path: "/photos/x.png", size: 1 },
    ]);
    expect(versions).toEqual([{ v: 5, path: "/report.v5.md", isCurrent: true }]);
  });

  it("returns empty when no reports exist", () => {
    expect(reportVersions([])).toEqual([]);
  });

  it("matches with or without leading slash", () => {
    const versions = reportVersions([
      { path: "report.v1.md", size: 1 },
      { path: "/report.v2.md", size: 1 },
    ]);
    expect(versions.map((v) => v.v)).toEqual([1, 2]);
  });
});

describe("versionFromPath", () => {
  it("returns the version matching the path", () => {
    const versions = reportVersions([
      { path: "/report.v1.md", size: 1 },
      { path: "/report.v2.md", size: 1 },
    ]);
    expect(versionFromPath(versions, "/report.v2.md")?.v).toBe(2);
  });

  it("returns null for an unmatched path", () => {
    expect(versionFromPath([], "/brief.md")).toBeNull();
  });
});
