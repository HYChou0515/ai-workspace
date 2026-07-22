import { describe, expect, it } from "vitest";

import { fuzzyFilter, fuzzyScore } from "./fuzzy";

describe("fuzzyScore — subsequence matching", () => {
  it("matches non-contiguous characters in order (the point of fuzzy)", () => {
    // `.includes()` can't do this — "wafmap" is not a substring of the path.
    expect(fuzzyScore("wafmap", "wafer_map.csv")).not.toBeNull();
  });

  it("returns null when a query character is missing or out of order", () => {
    expect(fuzzyScore("xyz", "wafer_map.csv")).toBeNull();
    expect(fuzzyScore("pam", "wafer_map.csv")).toBeNull(); // right letters, wrong order
  });
});

describe("fuzzyScore — slash look-alikes are the same as a typed /", () => {
  const U2215 = "∕"; // DIVISION SLASH — what a stored path uses where `/` can't go

  it("a typed / matches the U+2215 look-alike (nobody can type ∕)", () => {
    expect(fuzzyScore("col/guide", `col${U2215}me${U2215}guide.md`)).not.toBeNull();
    expect(fuzzyScore("/", `a${U2215}b`)).not.toBeNull();
  });

  it("folds the look-alike on both sides, so either spelling of the query works", () => {
    expect(fuzzyScore(`me${U2215}guide`, "col/me/guide.md")).not.toBeNull();
  });
});

describe("fuzzyFilter — ranks best matches first, drops non-matches", () => {
  const paths = ["docs/wafer_map.csv", "map.csv", "m_a_p_notes.txt", "readme.md"];

  it("keeps only matches, ordered by relevance", () => {
    const out = fuzzyFilter("map", paths, (p) => p);
    expect(out).not.toContain("readme.md"); // 'map' not a subsequence in order
    // A whole-word start beats a mid-path word beats scattered letters.
    expect(out.indexOf("map.csv")).toBeLessThan(out.indexOf("docs/wafer_map.csv"));
    expect(out.indexOf("docs/wafer_map.csv")).toBeLessThan(out.indexOf("m_a_p_notes.txt"));
  });

  it("an empty query keeps everything in original order", () => {
    expect(fuzzyFilter("  ", paths, (p) => p)).toEqual(paths);
  });
})
