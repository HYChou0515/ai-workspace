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

  // Locks the by-step layout the local-lab prompt recommends. The regex
  // matches the *basename* anywhere in the workspace, not just root, so a
  // report parked under `/step6-report/` (or any other folder) is still
  // picked up by F11. The regression that motivated this: a "No report
  // versions yet" empty-state when the agent dutifully followed the new
  // organisation guidance.
  it("recognises report files nested under a step folder", () => {
    const versions = reportVersions([
      { path: "/step6-report/report.v1.md", size: 1 },
      { path: "/step6-report/report.v2.md", size: 1 },
    ]);
    expect(versions.map((v) => ({ v: v.v, isCurrent: v.isCurrent }))).toEqual([
      { v: 1, isCurrent: false },
      { v: 2, isCurrent: true },
    ]);
  });

  it("mixes root and nested report paths", () => {
    // An investigation in the middle of migrating from the flat layout to
    // by-step might have both shapes living together for one turn — the
    // version list should reconcile across them.
    const versions = reportVersions([
      { path: "/report.v1.md", size: 1 },
      { path: "/step6-report/report.v2.md", size: 1 },
    ]);
    expect(versions.map((v) => v.v)).toEqual([1, 2]);
    expect(versions[1]?.isCurrent).toBe(true);
  });

  it("doesn't match a path where report.v* isn't the basename", () => {
    // Defence against a sibling like `report.v1.md.bak` or
    // `something-report.v1.md.notes` accidentally being routed to F11.
    const versions = reportVersions([
      { path: "/report.v1.md.bak", size: 1 },
      { path: "/something-report.v1.md", size: 1 },
    ]);
    // `something-report.v1.md` ends in `report.v1.md` after a `/` boundary —
    // it doesn't, so this stays out. `.bak` extension means it's not `.md`.
    expect(versions).toEqual([]);
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
